#!/usr/bin/env python3
"""Reproducible patch builder for PR2.exe: one new code/data section + detours.

One script turns the pinned base exe and an ordered patch list into an output
exe plus a manifest of hashes, so every build is reproducible.

What it does, in order:
  1. reads the pinned base exe and refuses to continue unless its sha256 matches;
  2. appends ONE new section (default ".mod", --section-name) after .rsrc;
  3. lets patch functions place code/data at symbolic labels inside it and get
     their VAs back, overwrite bytes in the original image (old bytes checked),
     and write 5-byte E9 detours whose displaced instructions are verified with
     capstone against a caller-supplied instruction list;
  4. writes the exe and <out>.manifest.json (base hash, every patch with old/new
     bytes, the section layout, the output hash).

Section choice: a single RWX section (code | initialized data | read | write |
execute). The base exe has DllCharacteristics = 0 (no NX_COMPAT), so the loader
never maps any of its pages non-executable anyway, and a single section keeps
the header delta to one 40-byte entry plus three fields. Code and data still
live in separate labels; place them with align=0x1000 if you want a page split.

Header changes (and nothing else outside patched ranges):
  FILE_HEADER.NumberOfSections, OPTIONAL_HEADER.SizeOfImage,
  OPTIONAL_HEADER.CheckSum (only if the base had a non-zero one; PR2 ships 0 and
  the loader does not verify user-mode exe checksums), one new section header.
  The image has no .reloc directory (fixed base 0x400000), so absolute VAs in
  patch code are fine.

Library use:
    from pe_patch import build
    def my_patch(b):                       # b is a PatchBuilder
        d = b.detour(0x5BA0A0, ["mov al, byte ptr [esp + 4]", "cmp al, 4"],
                     target="hook", desc="IsAtWar hook")
        b.place_trampoline("hook", d, prologue=b"")   # displaced + jmp back
    build("_build/x/PR2_mod.exe", [my_patch])

CLI:
    python tools/exe/pe_patch.py OUT.exe [--size 0x1000] [--section-name .mod]
                                 [--base EXE] [--patch-module file.py ...]
Each patch module must define apply(builder).

The base is the DRM-free PR2.exe 1.1.2.3 made from your own copy (setting PR2_BASE_EXE,
tools/kit_paths.py); its sha256 is pinned. The output is for your own test install only:
pe_patch.py refuses to overwrite the base exe and to write into a Steam library folder.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Union

import capstone
import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402
BASE_EXE = kit_paths.BASE_EXE                   # setting PR2_BASE_EXE
BASE_SHA256 = kit_paths.BASE_SHA256
BUILD_DIR = kit_paths.BUILD                     # setting PR2_BUILD

SECTION_NAME = ".mod"
# CNT_CODE | CNT_INITIALIZED_DATA | MEM_EXECUTE | MEM_READ | MEM_WRITE
SECTION_CHARS = 0x00000020 | 0x00000040 | 0x20000000 | 0x40000000 | 0x80000000
JMP_LEN = 5

Content = Union[bytes, bytearray, Callable[[int], bytes]]


class PatchError(Exception):
    pass


def align_up(x: int, a: int) -> int:
    return (x + a - 1) // a * a


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel32(from_va: int, length: int, to_va: int) -> bytes:
    """rel32 displacement for an instruction of `length` bytes at from_va."""
    disp = to_va - (from_va + length)
    if not -(1 << 31) <= disp < (1 << 31):
        raise PatchError(f"rel32 out of range {from_va:#x} -> {to_va:#x}")
    return struct.pack("<i", disp)


def asm_jmp(from_va: int, to_va: int) -> bytes:
    return b"\xE9" + rel32(from_va, 5, to_va)


def asm_call(from_va: int, to_va: int) -> bytes:
    return b"\xE8" + rel32(from_va, 5, to_va)


def insn_text(i) -> str:
    return f"{i.mnemonic} {i.op_str}".strip()


def norm(s: str) -> str:
    return " ".join(s.lower().split())


@dataclass
class DisplacedInsn:
    va: int
    raw: bytes
    text: str
    kind: str          # "pic" | "call32" | "jmp32" | "jcc32"
    target: int | None = None

    def relocated(self, at_va: int) -> bytes:
        """Bytes that execute identically when placed at at_va."""
        if self.kind == "pic":
            return self.raw
        if self.kind in ("call32", "jmp32"):
            op = self.raw[:1]
            return op + rel32(at_va, 5, self.target)
        if self.kind == "jcc32":
            op = self.raw[:2]
            return op + rel32(at_va, 6, self.target)
        raise PatchError(self.kind)


@dataclass
class Detour:
    va: int                     # where the E9 is written
    length: int                 # bytes overwritten (>= 5)
    resume_va: int              # va + length: where the trampoline jumps back to
    insns: list[DisplacedInsn]
    target: Union[int, str]     # VA or section label
    desc: str = ""

    @property
    def displaced(self) -> bytes:
        """Original bytes of the displaced instructions (unrelocated)."""
        return b"".join(i.raw for i in self.insns)

    def relocated(self, at_va: int) -> bytes:
        """Displaced instructions re-encoded to run at at_va."""
        out = b""
        for i in self.insns:
            out += i.relocated(at_va + len(out))
        return out

    def relocated_len(self) -> int:
        return sum(5 if i.kind in ("call32", "jmp32") else 6 if i.kind == "jcc32"
                   else len(i.raw) for i in self.insns)


@dataclass
class Label:
    name: str
    offset: int
    size: int
    content: Content
    desc: str = ""


@dataclass
class _Patch:
    va: int
    size: int
    new: Union[bytes, Callable[[], bytes]]
    expected_old: bytes | None
    desc: str
    kind: str


class PatchBuilder:
    """Holds the base image and the pending patches; build() materializes them."""

    def __init__(self, base_path: Path | str = BASE_EXE, expected_sha256: str = BASE_SHA256,
                 section_size: int = 0x1000, section_name: str = SECTION_NAME,
                 section_chars: int = SECTION_CHARS, section_rva: int | None = None):
        """section_rva: place the new section at this RVA instead of right after the last
        section. Only for materialize() (a patch-data exporter builds twice at two RVAs to find
        the section-relative fields); build() refuses it, the file layout would not match."""
        self.base_path = Path(base_path)
        self.base = self.base_path.read_bytes()
        self.base_sha256 = sha256(self.base)
        if self.base_sha256 != expected_sha256:
            raise PatchError(f"base exe hash mismatch: {self.base_sha256} != {expected_sha256}")
        if len(section_name.encode()) > 8:
            raise PatchError("section name longer than 8 bytes")
        if section_size <= 0:
            raise PatchError("section size must be positive")
        self.pe = pefile.PE(data=self.base, fast_load=True)
        self.image_base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sec_align = self.pe.OPTIONAL_HEADER.SectionAlignment
        self.file_align = self.pe.OPTIONAL_HEADER.FileAlignment
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

        last = self.pe.sections[-1]
        if self.pe.get_overlay_data_start_offset() is not None:
            raise PatchError("base exe has overlay data; appending a section would corrupt it")
        self.sec_name = section_name
        self.sec_chars = section_chars
        self.sec_vsize = section_size
        self.sec_rva_default = align_up(last.VirtualAddress + max(last.Misc_VirtualSize,
                                                                  last.SizeOfRawData), self.sec_align)
        self.sec_rva = self.sec_rva_default if section_rva is None else section_rva
        self.sec_va = self.image_base + self.sec_rva
        self.sec_raw_ptr = align_up(last.PointerToRawData + last.SizeOfRawData, self.file_align)
        self.sec_raw_size = align_up(section_size, self.file_align)
        self.labels: dict[str, Label] = {}
        self._cursor = 0
        self.patches: list[_Patch] = []

    # ---- address helpers -------------------------------------------------
    def file_offset(self, va: int) -> int:
        rva = va - self.image_base
        for s in self.pe.sections:
            end = s.VirtualAddress + min(s.SizeOfRawData, s.Misc_VirtualSize or s.SizeOfRawData)
            if s.VirtualAddress <= rva < end:
                return s.PointerToRawData + rva - s.VirtualAddress
        raise PatchError(f"VA {va:#x} is not backed by file data in the base image")

    def read(self, va: int, n: int) -> bytes:
        off = self.file_offset(va)
        return self.base[off:off + n]

    def va_of(self, label: str) -> int:
        try:
            return self.sec_va + self.labels[label].offset
        except KeyError:
            raise PatchError(f"unknown label {label!r}") from None

    def resolve(self, target: Union[int, str]) -> int:
        return self.va_of(target) if isinstance(target, str) else target

    # ---- new-section placement -------------------------------------------
    def next_va(self, align: int = 16) -> int:
        """VA the next place()/reserve() with this alignment will get."""
        return self.sec_va + align_up(self._cursor, align)

    def place(self, label: str, content: Content, size: int | None = None,
              align: int = 16, desc: str = "") -> int:
        """Put bytes at a new label in the section; return its VA.

        content may be bytes, or a callable(va) -> bytes evaluated at build time
        (use it for code holding rel32 references to labels placed later); a
        callable needs an explicit size (the result must match it exactly)."""
        if label in self.labels:
            raise PatchError(f"label {label!r} already placed")
        if callable(content):
            if size is None:
                raise PatchError("callable content needs an explicit size")
        else:
            content = bytes(content)
            size = len(content) if size is None else size
            if len(content) > size:
                raise PatchError(f"{label}: content {len(content)} > size {size}")
        off = align_up(self._cursor, align)
        if off + size > self.sec_vsize:
            raise PatchError(f"section full: {label} needs {off + size:#x} > {self.sec_vsize:#x}")
        self.labels[label] = Label(label, off, size, content, desc)
        self._cursor = off + size
        return self.sec_va + off

    def reserve(self, label: str, size: int, align: int = 4, desc: str = "") -> int:
        """Zero-initialized data at a new label; return its VA."""
        return self.place(label, b"\x00" * size, size, align, desc)

    def place_trampoline(self, label: str, detour: Detour, prologue: Content = b"",
                         prologue_size: int | None = None, align: int = 16,
                         desc: str = "") -> int:
        """Place [prologue][displaced instructions relocated][jmp detour.resume_va].

        prologue: hook code run first (bytes, or callable(va) -> bytes with
        prologue_size). Return the trampoline VA."""
        if callable(prologue):
            if prologue_size is None:
                raise PatchError("callable prologue needs prologue_size")
            psize = prologue_size
        else:
            psize = len(prologue)
        size = psize + detour.relocated_len() + JMP_LEN

        def gen(va: int) -> bytes:
            pro = prologue(va) if callable(prologue) else bytes(prologue)
            if len(pro) != psize:
                raise PatchError(f"{label}: prologue is {len(pro)} bytes, declared {psize}")
            body = detour.relocated(va + psize)
            tail = asm_jmp(va + psize + len(body), detour.resume_va)
            return pro + body + tail

        return self.place(label, gen, size, align,
                          desc or f"trampoline for detour @{detour.va:#x}")

    # ---- patches into the original image ---------------------------------
    def _check_overlap(self, va: int, size: int):
        for p in self.patches:
            if va < p.va + p.size and p.va < va + size:
                raise PatchError(f"patch {va:#x}+{size} overlaps earlier patch {p.va:#x}+{p.size}")

    def patch_bytes(self, va: int, new: bytes, expected_old: bytes | None = None,
                    desc: str = "") -> None:
        """Overwrite bytes in the original image (old bytes verified if given)."""
        new = bytes(new)
        old = self.read(va, len(new))
        if len(old) != len(new):
            raise PatchError(f"patch at {va:#x} runs past file data")
        if expected_old is not None and old != bytes(expected_old):
            raise PatchError(f"patch at {va:#x}: expected {bytes(expected_old).hex()}, found {old.hex()}")
        self._check_overlap(va, len(new))
        self.patches.append(_Patch(va, len(new), new, old, desc, "bytes"))

    def decode(self, va: int, n: int = 32):
        return list(self.md.disasm(self.read(va, n), va))

    def detour(self, va: int, expected: Iterable[str], target: Union[int, str],
               desc: str = "") -> Detour:
        """Write `jmp target` at va (NOP-padded to an instruction boundary).

        expected: exact instruction texts (capstone Intel syntax, e.g.
        "mov al, byte ptr [esp + 4]") of every instruction overwritten, in order.
        target: a VA or a section label (resolved at build time).
        Displaced instructions must be position-independent or rel32
        call/jmp/jcc (those are relocated); rel8 branches, loop/jecxz, ret and
        anything after an unconditional transfer are rejected."""
        expected = [norm(e) for e in expected]
        insns, total = [], 0
        for i in self.md.disasm(self.read(va, 32), va):
            if total >= JMP_LEN:
                break
            insns.append(i)
            total += i.size
        if total < JMP_LEN:
            raise PatchError(f"detour at {va:#x}: could not decode {JMP_LEN} bytes")
        got = [norm(insn_text(i)) for i in insns]
        if got != expected:
            raise PatchError(f"detour at {va:#x}: expected {expected}, found {got}")

        displaced = []
        for n, i in enumerate(insns):
            groups = set(i.groups)
            is_last = n == len(insns) - 1
            b = bytes(i.bytes)
            text = insn_text(i)
            if capstone.x86.X86_GRP_RET in groups or capstone.x86.X86_GRP_INT in groups \
                    or capstone.x86.X86_GRP_IRET in groups or i.mnemonic in ("int3", "hlt", "ud2"):
                raise PatchError(f"detour at {va:#x}: cannot displace {text!r}")
            if capstone.x86.X86_GRP_JUMP in groups or capstone.x86.X86_GRP_CALL in groups:
                op = i.operands[0] if i.operands else None
                if op is None or op.type != capstone.x86.X86_OP_IMM:
                    # indirect call/jmp through reg/mem: position-independent
                    if i.mnemonic == "jmp" and not is_last:
                        raise PatchError(f"detour at {va:#x}: {text!r} is not last")
                    displaced.append(DisplacedInsn(i.address, b, text, "pic"))
                    continue
                tgt = op.imm & 0xFFFFFFFF
                if b[0] == 0xE8 and len(b) == 5:
                    kind = "call32"
                elif b[0] == 0xE9 and len(b) == 5:
                    kind = "jmp32"
                    if not is_last:
                        raise PatchError(f"detour at {va:#x}: {text!r} is not last")
                elif b[0] == 0x0F and 0x80 <= b[1] <= 0x8F and len(b) == 6:
                    kind = "jcc32"
                else:
                    raise PatchError(f"detour at {va:#x}: cannot relocate {text!r} ({b.hex()})")
                displaced.append(DisplacedInsn(i.address, b, text, kind, tgt))
                continue
            displaced.append(DisplacedInsn(i.address, b, text, "pic"))

        # a branch target inside (va, va+total) would land in the middle of our jmp
        for i in displaced:
            if i.target is not None and va < i.target < va + total:
                raise PatchError(f"detour at {va:#x}: {i.text!r} targets the overwritten range")

        self._check_overlap(va, total)
        d = Detour(va, total, va + total, displaced, target, desc)

        def gen() -> bytes:
            return asm_jmp(va, self.resolve(target)) + b"\x90" * (total - JMP_LEN)

        self.patches.append(_Patch(va, total, gen, b"".join(i.raw for i in displaced),
                                   desc, "detour"))
        return d

    # ---- output ------------------------------------------------------------
    def _section_bytes(self) -> bytes:
        buf = bytearray(self.sec_raw_size)
        for lab in self.labels.values():
            data = lab.content(self.sec_va + lab.offset) if callable(lab.content) else lab.content
            data = bytes(data)
            if callable(lab.content) and len(data) != lab.size:
                raise PatchError(f"label {lab.name}: generated {len(data)} bytes, declared {lab.size}")
            buf[lab.offset:lab.offset + len(data)] = data
        return bytes(buf)

    def materialize(self) -> tuple[bytes, list[dict]]:
        """The new section's contents (virtual size) and every image patch as
        {va, kind, old, new, desc}, without writing anything (for an in-memory loader's
        patch data, e.g. Sovereign Nations' tools/export_patches.py). Same checks as build()."""
        records = []
        for p in self.patches:
            new = p.new() if callable(p.new) else p.new
            if len(new) != p.size:
                raise PatchError(f"patch {p.va:#x}: size changed")
            if self.read(p.va, p.size) != p.expected_old:
                raise PatchError(f"patch {p.va:#x}: base bytes changed")
            records.append({"va": p.va, "kind": p.kind, "old": p.expected_old, "new": bytes(new),
                            "desc": p.desc})
        return self._section_bytes()[:self.sec_vsize], records

    def build(self, out_path: Path | str) -> dict:
        if self.sec_rva != self.sec_rva_default:
            raise PatchError("build() needs the default section RVA (section_rva is for materialize())")
        out_path = Path(out_path).resolve()
        _guard_out_path(out_path, self.base_path)
        img = bytearray(self.base)
        pe = self.pe
        fh_off = pe.FILE_HEADER.get_file_offset()
        oh_off = pe.OPTIONAL_HEADER.get_file_offset()
        nsec = pe.FILE_HEADER.NumberOfSections
        sectab = oh_off + pe.FILE_HEADER.SizeOfOptionalHeader
        new_hdr = sectab + nsec * 40
        size_of_headers = pe.OPTIONAL_HEADER.SizeOfHeaders
        first_raw = min(s.PointerToRawData for s in pe.sections if s.SizeOfRawData)
        if new_hdr + 40 > min(size_of_headers, first_raw):
            raise PatchError("no room for another section header")
        if any(img[new_hdr:new_hdr + 40]):
            raise PatchError("header slack after the section table is not zero")
        if len(img) != self.sec_raw_ptr:
            raise PatchError(f"file size {len(img):#x} != expected end of last section {self.sec_raw_ptr:#x}")

        header_changes = []

        def put(off, fmt, value, what):
            old = struct.unpack_from(fmt, img, off)[0]
            struct.pack_into(fmt, img, off, value)
            header_changes.append({"field": what, "file_offset": off, "old": old, "new": value})

        put(fh_off + 2, "<H", nsec + 1, "FILE_HEADER.NumberOfSections")
        size_of_image = align_up(self.sec_rva + self.sec_vsize, self.sec_align)
        put(oh_off + 56, "<I", size_of_image, "OPTIONAL_HEADER.SizeOfImage")
        struct.pack_into("<8sIIIIIIHHI", img, new_hdr,
                         self.sec_name.encode().ljust(8, b"\x00"), self.sec_vsize, self.sec_rva,
                         self.sec_raw_size, self.sec_raw_ptr, 0, 0, 0, 0, self.sec_chars)
        header_changes.append({"field": f"section header {self.sec_name}", "file_offset": new_hdr,
                               "old": "00" * 40, "new": bytes(img[new_hdr:new_hdr + 40]).hex()})

        img += self._section_bytes()

        records = []
        for p in self.patches:
            new = p.new() if callable(p.new) else p.new
            if len(new) != p.size:
                raise PatchError(f"patch {p.va:#x}: size changed")
            off = self.file_offset(p.va)
            if bytes(img[off:off + p.size]) != p.expected_old:
                raise PatchError(f"patch {p.va:#x}: base bytes changed")
            img[off:off + p.size] = new
            records.append({"va": f"{p.va:#x}", "file_offset": f"{off:#x}", "kind": p.kind,
                            "old": p.expected_old.hex(), "new": new.hex(), "desc": p.desc})

        checksum_off = oh_off + 64
        if pe.OPTIONAL_HEADER.CheckSum:
            struct.pack_into("<I", img, checksum_off, 0)
            cs = pefile.PE(data=bytes(img), fast_load=True).generate_checksum()
            put(checksum_off, "<I", cs, "OPTIONAL_HEADER.CheckSum")

        out = bytes(img)
        sec_data = out[self.sec_raw_ptr:]
        manifest = {
            "tool": "tools/exe/pe_patch.py",
            "base": {"path": str(self.base_path), "sha256": self.base_sha256, "size": len(self.base)},
            "output": {"path": str(out_path), "sha256": sha256(out), "size": len(out)},
            "header_changes": header_changes,
            "section": {
                "name": self.sec_name, "va": f"{self.sec_va:#x}", "rva": f"{self.sec_rva:#x}",
                "virtual_size": f"{self.sec_vsize:#x}", "raw_offset": f"{self.sec_raw_ptr:#x}",
                "raw_size": f"{self.sec_raw_size:#x}", "characteristics": f"{self.sec_chars:#010x}",
                "used": f"{self._cursor:#x}", "sha256": sha256(sec_data),
                "labels": [{"name": l.name, "va": f"{self.sec_va + l.offset:#x}",
                            "offset": f"{l.offset:#x}", "size": l.size, "desc": l.desc,
                            "bytes": sec_data[l.offset:l.offset + l.size].hex()}
                           for l in self.labels.values()],
            },
            "patches": records,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out)
        manifest_path(out_path).write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


def manifest_path(out_path: Path | str) -> Path:
    p = Path(out_path)
    return p.with_name(p.name + ".manifest.json")


def _guard_out_path(out_path: Path, base_path: Path):
    """Never overwrite the base exe, and never write a patched exe into a Steam library."""
    if out_path == Path(base_path).resolve():
        raise PatchError(f"refusing to overwrite the base exe {out_path}")
    if any(part.lower() == "steamapps" for part in out_path.parts):
        raise PatchError(f"refusing to write {out_path}: inside a Steam library; "
                         "build into a folder of your own and deploy to a test install")


PatchFn = Callable[[PatchBuilder], None]


def build(out_path: Path | str, patches: Iterable[PatchFn], section_size: int = 0x1000,
          base_path: Path | str = BASE_EXE, expected_sha256: str = BASE_SHA256,
          section_name: str = SECTION_NAME) -> dict:
    """Apply `patches` (functions taking a PatchBuilder) in order; write exe + manifest."""
    b = PatchBuilder(base_path, expected_sha256, section_size, section_name)
    for p in patches:
        p(b)
    return b.build(out_path)


def _load_module(path: str) -> PatchFn:
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.apply


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", help="output exe (for your own test install)")
    ap.add_argument("--size", type=lambda s: int(s, 0), default=0x1000, help="new section size")
    ap.add_argument("--section-name", default=SECTION_NAME, help="new section's name (<= 8 bytes)")
    ap.add_argument("--base", type=Path, default=BASE_EXE,
                    help="the DRM-free 1.1.2.3 exe (default: setting PR2_BASE_EXE)")
    ap.add_argument("--patch-module", action="append", default=[],
                    help="python file defining apply(builder); repeatable, applied in order")
    args = ap.parse_args(argv)
    try:
        if not args.base.exists():
            raise PatchError(f"base exe not found: {args.base} (set PR2_BASE_EXE, see tools/kit_paths.py)")
        m = build(args.out, [_load_module(p) for p in args.patch_module], args.size,
                  base_path=args.base, section_name=args.section_name)
    except PatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {m['output']['path']}  sha256 {m['output']['sha256']}")
    print(f"section {m['section']['name']} @ {m['section']['va']}  "
          f"{len(m['patches'])} patch(es), {len(m['section']['labels'])} label(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
