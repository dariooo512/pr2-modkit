#!/usr/bin/env python3
"""Export a mod's exe patch modules as patch data for the loader (loader/README.md).

    python tools/release/export_patches.py <mod>/install.json [--out FILE] [--version V] [--check]
                                           [--base EXE]

The patch modules (install.json `patches`) stay the single source: this runs them through the
kit's pe_patch.py PatchBuilder exactly like a file build does, but instead of writing an exe it
records what the patched image must look like in memory:

  * every image patch: VA, expected original bytes, new bytes (the loader checks the original
    bytes of ALL patches of a mod first and patches nothing of it on any mismatch);
  * the new section's contents (code caves and data), at its address (install.json section_va,
    default right after the exe's last section, 0x9F7000);
  * relocations: the 4-byte fields whose value depends on where the section lands. They are
    found, not hand-written: the patch set is built at three section addresses (A, A+d, A+2d)
    and every field that moves by +d/+2d (absolute address or rel32 into the section) or
    -d/-2d (rel32 from the section back into the image) is recorded. Anything else that changes
    means the set is not relocatable, and the export fails;
  * the trigger: the IAT slot the loader hooks (MSVCR71!__set_app_type, called by the CRT
    startup right after the original entry point) and the return address that identifies the
    CRT's call, both verified by disassembly here;
  * the mod's name (`name`) and its DLL (`dll`, the file name of install.json `dll`).

A mod without exe patches (`patches: []`) gets patch data with only the trigger and its DLL:
the loader loads the DLL at the trigger. Output: install.json `patch_data` (or --out), text,
one record per line. --check also proves that applying the data to the base image gives
exactly the image of the file build (pe_patch.build), so memory and file route run the same
bytes, and that the data still works when the section has to move.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

import capstone
import pefile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exe"))
sys.path.insert(0, str(HERE))
import pe_patch  # noqa: E402
from pe_patch import PatchBuilder, PatchError  # noqa: E402
from install_json import Config, ConfigError  # noqa: E402

MAGIC = "PR2-PATCHDATA 2"
MAGIC_V1 = "PR2NATION-PATCHDATA 1"          # Sovereign Nations' first format; the loader reads both

# trigger: the CRT startup's first call through an import nobody else uses this early
TRIGGER_CALL = 0x64E0B0           # call dword ptr [0x67f6f0]  ; MSVCR71.dll!__set_app_type
TRIGGER_IAT = 0x67F6F0
TRIGGER_IMPORT = ("MSVCR71.dll", "__set_app_type")
DELTA = 0x00123457                # odd low byte: every moved field differs in its first byte
M32 = 0xFFFFFFFF
IMAGE_BASE = 0x400000


def patch_modules(cfg: Config):
    mods = []
    for path in cfg.patch_modules():
        spec = importlib.util.spec_from_file_location(path.stem, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        if not hasattr(m, "apply"):
            raise PatchError(f"{path}: a patch module needs apply(builder)")
        mods.append((path.name, m.apply))
    return mods


def materialize(cfg: Config, mods, base: Path, rva=None):
    b = PatchBuilder(base, pe_patch.BASE_SHA256, cfg.section_size, cfg.section_name, section_rva=rva)
    for _, apply in mods:
        apply(b)
    sec, recs = b.materialize()
    return b, sec, recs


def fields(a: bytes, b: bytes, c: bytes, what: str):
    """4-byte fields that differ between a (at A), b (A+d), c (A+2d) -> [(offset, sign)]."""
    out, i = [], 0
    while i < len(a):
        if a[i] == b[i]:
            if a[i] != c[i]:
                raise PatchError(f"{what}+{i:#x}: changes between A+d and A+2d only")
            i += 1
            continue
        if i + 4 > len(a):
            raise PatchError(f"{what}+{i:#x}: moved bytes at the end")
        va, vb, vc = (struct.unpack_from("<I", x, i)[0] for x in (a, b, c))
        if (vb - va) & M32 == DELTA and (vc - va) & M32 == (2 * DELTA) & M32:
            out.append((i, +1))
        elif (va - vb) & M32 == DELTA and (va - vc) & M32 == (2 * DELTA) & M32:
            out.append((i, -1))
        else:
            raise PatchError(f"{what}+{i:#x}: not a relocatable 32-bit field "
                             f"({va:#x}, {vb:#x}, {vc:#x})")
        i += 4
    return out


def trigger_check(base_path):
    pe = pefile.PE(str(base_path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    names = {i.address: (e.dll.decode(), i.name.decode() if i.name else None)
             for e in pe.DIRECTORY_ENTRY_IMPORT for i in e.imports}
    if names.get(TRIGGER_IAT) != TRIGGER_IMPORT:
        raise PatchError(f"IAT {TRIGGER_IAT:#x} is {names.get(TRIGGER_IAT)}, expected {TRIGGER_IMPORT}")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    code = pe.get_data(TRIGGER_CALL - pe.OPTIONAL_HEADER.ImageBase, 16)
    ins = next(md.disasm(code, TRIGGER_CALL))
    if f"{ins.mnemonic} {ins.op_str}" != f"call dword ptr [{TRIGGER_IAT:#x}]":
        raise PatchError(f"trigger call at {TRIGGER_CALL:#x} is {ins.mnemonic} {ins.op_str}")
    # a patch on the code between the entry point and the trigger has already run (or not)
    # when the loader writes it: the loader applies it anyway, harmlessly
    return TRIGGER_CALL + ins.size


def git_version(root: Path):
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=8", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "nogit"


def export(cfg: Config, version: str, base: Path) -> tuple[str, dict]:
    mods = patch_modules(cfg)
    rva = None if cfg.section_va is None else cfg.section_va - IMAGE_BASE
    b1, s1, r1 = materialize(cfg, mods, base, rva)
    a = b1.sec_rva
    _, s2, r2 = materialize(cfg, mods, base, a + DELTA)
    _, s3, r3 = materialize(cfg, mods, base, a + 2 * DELTA)
    if [(r["va"], r["old"]) for r in r1] != [(r["va"], r["old"]) for r in r2] \
            or [r["va"] for r in r1] != [r["va"] for r in r3]:
        raise PatchError("patch list depends on the section address")
    sec_relocs = fields(s1, s2, s3, "section")
    patch_relocs = []
    for n, (x, y, z) in enumerate(zip(r1, r2, r3)):
        for off, sign in fields(x["new"], y["new"], z["new"], f"patch {x['va']:#x}"):
            patch_relocs.append((n, off, sign))
    ret = trigger_check(base)
    sec_va = b1.sec_va
    used = max((l.offset + l.size for l in b1.labels.values()), default=0)
    has_section = used > 0 or bool(sec_relocs)

    lines = [MAGIC,
             f"# {cfg.name} patch data, generated by the modding kit's tools/release/export_patches.py",
             "# from the mod's patch modules through pe_patch.py. Do not edit: re-export instead.",
             f"name {cfg.name}",
             f"mod {version}",
             f"base {b1.base_sha256}",
             f"hook {TRIGGER_IAT:08X} {ret:08X} {TRIGGER_IMPORT[0]} {TRIGGER_IMPORT[1]}"]
    if has_section:
        lines.append(f"section {sec_va:08X} {cfg.section_size:08X} {hashlib.sha256(s1).hexdigest()}")
        for off in range(0, used, 32):
            chunk = s1[off:min(off + 32, used)]
            if any(chunk):
                lines.append(f"data {off:04X} {chunk.hex().upper()}")
    for r in r1:
        desc = " ".join(r["desc"].split())
        lines.append(f"patch {r['va']:08X} {r['old'].hex().upper()} {r['new'].hex().upper()} ; {desc}")
    for off, sign in sec_relocs:
        lines.append(f"reloc S {off:04X} {'+' if sign > 0 else '-'}")
    for n, off, sign in patch_relocs:
        lines.append(f"reloc P {n} {off:X} {'+' if sign > 0 else '-'}")
    lines.append(f"dll {cfg.dll_name}")
    body = "\n".join(lines) + "\n"
    lines.append(f"end {hashlib.sha256(body.encode('ascii')).hexdigest()}")
    text = "\n".join(lines) + "\n"
    info = {"modules": len(mods), "patches": len(r1), "section_va": sec_va if has_section else None,
            "section_used": used, "section_relocs": len(sec_relocs), "patch_relocs": len(patch_relocs),
            "trigger_ret": ret}
    return text, info


# ---- a Python model of the loader (used by --check and the tests) -------------------------
def parse(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0] not in (MAGIC, MAGIC_V1):
        raise PatchError("not patch data")
    if not lines[-1].startswith("end "):
        raise PatchError("truncated (no end line)")
    body = "\n".join(lines[:-1]) + "\n"
    if hashlib.sha256(body.encode("ascii")).hexdigest() != lines[-1].split()[1]:
        raise PatchError("checksum mismatch")
    d = {"format": 2 if lines[0] == MAGIC else 1, "data": [], "patches": [], "sreloc": [], "preloc": [],
         "section": None}
    for ln in lines[1:-1]:
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("name "):
            d["name"] = ln[5:]
            continue
        k, *f = ln.split(" ;")[0].split()
        if k == "section":
            d["section"] = (int(f[0], 16), int(f[1], 16))
        elif k == "data":
            d["data"].append((int(f[0], 16), bytes.fromhex(f[1])))
        elif k == "patch":
            d["patches"].append((int(f[0], 16), bytes.fromhex(f[1]), bytes.fromhex(f[2])))
        elif k == "reloc":
            if f[0] == "S":
                d["sreloc"].append((int(f[1], 16), 1 if f[2] == "+" else -1))
            else:
                d["preloc"].append((int(f[1]), int(f[2], 16), 1 if f[3] == "+" else -1))
        elif k == "hook":
            d["hook"] = (int(f[0], 16), int(f[1], 16))
        elif k in ("mod", "base", "dll"):
            d[k] = f[0] if f else ""
    if "hook" not in d or not d.get("dll"):
        raise PatchError("incomplete (no hook or dll record)")
    return d


def ranges(d: dict) -> list[tuple[int, int]]:
    """The byte ranges the data changes: its patch sites and its section."""
    out = [(va, len(old)) for va, old, _ in d["patches"]]
    if d["section"]:
        out.append(d["section"])
    return out


def conflict(a: dict, b: dict) -> int | None:
    """The first address both patch data change (the loader refuses the later file), or None."""
    for va, n in ranges(a):
        for vb, nb in ranges(b):
            if va < vb + nb and vb < va + n:
                return max(va, vb)
    return None


def apply_model(d: dict, read, sec_at: int | None = None):
    """What the loader writes: [(va, bytes)], or raises on an original-bytes mismatch.
    read(va, n) -> bytes of the current image."""
    for va, old, _ in d["patches"]:
        if read(va, len(old)) != old:
            raise PatchError(f"original bytes differ at {va:#x}")
    writes, delta = [], 0
    if d["section"]:
        va0, size = d["section"]
        at = va0 if sec_at is None else sec_at
        delta = (at - va0) & M32
        sec = bytearray(size)
        for off, chunk in d["data"]:
            sec[off:off + len(chunk)] = chunk
    news = [bytearray(n) for _, _, n in d["patches"]]

    def fix(buf, off, sign):
        v = struct.unpack_from("<I", buf, off)[0]
        struct.pack_into("<I", buf, off, (v + sign * delta) & M32)
    if d["section"]:
        for off, sign in d["sreloc"]:
            fix(sec, off, sign)
        writes.append((at, bytes(sec)))
    for n, off, sign in d["preloc"]:
        fix(news[n], off, sign)
    return writes + [(va, bytes(n)) for (va, _, _), n in zip(d["patches"], news)]


def check(cfg: Config, text: str, base: Path, out_dir: Path) -> str:
    """Applying the data to the base image == the file build's image (pe_patch.build)."""
    mods = patch_modules(cfg)
    d = parse(text)
    if not mods:
        return "no exe patches: the data holds the trigger and the DLL only"
    out = out_dir / "check" / f"{cfg.patch_data.stem}_check.exe"
    pe_patch.build(out, [a for _, a in mods], cfg.section_size, base, section_name=cfg.section_name)
    built = pefile.PE(str(out), fast_load=True)
    basepe = pefile.PE(str(base), fast_load=True)
    base_img = basepe.get_memory_mapped_image()
    img = bytearray(base_img)
    file_sec_va = IMAGE_BASE + built.sections[-1].VirtualAddress
    writes = apply_model(d, lambda va, n: bytes(img[va - IMAGE_BASE:va - IMAGE_BASE + n]),
                         sec_at=file_sec_va if d["section"] else None)
    end = max([va + len(b) for va, b in writes] + [IMAGE_BASE + len(img)])
    img += b"\0" * (end - IMAGE_BASE - len(img))
    for va, data in writes:
        img[va - IMAGE_BASE:va - IMAGE_BASE + len(data)] = data
    want = built.get_memory_mapped_image()
    # headers differ (one more section); compare from the first section on
    first = basepe.sections[0].VirtualAddress
    n = min(len(img), len(want))
    if bytes(img[first:n]) != want[first:n]:
        bad = next(i for i in range(first, n) if img[i] != want[i])
        raise PatchError(f"memory image differs from the file build at {bad + IMAGE_BASE:#x}")
    if d["section"]:
        # relocated placement is consistent too: a copy at another address == a build there
        other = apply_model(d, lambda va, k: bytes(base_img[va - IMAGE_BASE:va - IMAGE_BASE + k]), sec_at=0x12340000)
        _, s_other, r_other = materialize(cfg, mods, base, 0x12340000 - IMAGE_BASE)
        if other[0][1][:len(s_other)] != s_other or [w[1] for w in other[1:]] != [r["new"] for r in r_other]:
            raise PatchError("relocated section differs from a build at that address")
    return (f"memory image == file build {out.name} (sha256 {pe_patch.sha256(out.read_bytes())[:16]})"
            + ("; relocation ok" if d["section"] else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("install_json", type=Path, help="the mod's install.json (or its folder)")
    ap.add_argument("--out", type=Path, help="default: install.json patch_data")
    ap.add_argument("--version", default=None, help="the `mod` record (default: the mod's git commit)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--base", type=Path, default=pe_patch.BASE_EXE,
                    help="the DRM-free PR2.exe 1.1.2.3 (default: setting PR2_BASE_EXE)")
    a = ap.parse_args(argv)
    try:
        cfg = Config(a.install_json)
        if not a.base.exists():
            raise PatchError(f"base exe not found: {a.base} (set PR2_BASE_EXE, see tools/kit_paths.py)")
        out = a.out or cfg.patch_data
        text, info = export(cfg, a.version or cfg.version or git_version(cfg.root), a.base)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="ascii", newline="\n")
        sec = f"section {info['section_va']:#x} ({info['section_used']:#x} bytes used)" if info["section_va"] \
            else "no section"
        print(f"wrote {out}: {info['patches']} patches from {info['modules']} module(s), {sec}, "
              f"relocs {info['section_relocs']}+{info['patch_relocs']}, dll {cfg.dll_name}")
        if a.check:
            print(check(cfg, text, a.base, out.parent))
    except (PatchError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
