#!/usr/bin/env python3
"""Carve compiled functions into a copy of the exe, in place, with their references
resolved to the original addresses.

The output is always the whole original binary, except that each target function's
bytes are replaced by the code compiled from your source. Every relocation in that code
is fixed up to the ORIGINAL address of what it references, found by name:

    sub_0063A3E0, FUN_0063a3e0, loc_...   -> 0x0063A3E0   (function / code label)
    DAT_00701EE8, g_..., byte_/word_/dword_/unk_ -> that VA (global data, IAT slot)

The address token is found anywhere in the (possibly C++-mangled or fastcall-decorated)
symbol name, so `@sub_0063A3E0@8`, `?f@DAT_0067F9C0_str@@...` and `__imp__DAT_...` all
resolve. Relocations against the function's own .text (switch jump tables, labels) are
placed relative to the function's VA. Anything else (string literals, float constants,
statics in .data/.rdata) cannot be placed: in-place rebuilding adds no data, so reference
the original's copy as DAT_<VA> instead.

Limits: in place only. The compiled code must fit in the original body; the rest of the
body is filled with int3 (0xCC). No new section, no new globals.

    python relink.py --out _build/rebuild/PR2_test.exe 0x0041C6F0:_build/rebuild/0x0041C6F0.obj:74
    python relink.py --out copy.exe              # no targets: a byte-identical copy (sanity check)

Each obj must hold one function in its .text (compile one function per file with
`cl /c /O2`; build.py does this). The input exe is never written.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402

IMAGE_REL_I386_DIR32 = 6
IMAGE_REL_I386_REL32 = 20

_TOKEN = re.compile(r"(?<![0-9A-Za-z])(?:sub|FUN|loc|LAB|DAT|g|unk|byte|word|dword)_"
                    r"([0-9A-Fa-f]{4,8})(?![0-9A-Fa-f])")


def name_to_va(name, extra=None):
    """The VA a symbol name stands for, or None."""
    if extra:
        if name in extra:
            return extra[name]
        bare = name.lstrip("_@").split("@")[0]               # _f, __f, @f@8 -> f
        for k, v in extra.items():
            if k.lstrip("_@").split("@")[0] == bare:
                return v
    m = _TOKEN.search(name)
    return int(m.group(1), 16) if m else None


class Coff:
    """Minimal COFF object reader: sections, relocations, symbols."""

    def __init__(self, data):
        self.d = data
        (self.machine, self.nsec, _ts, self.psym, self.nsym,
         self.optsz, _ch) = struct.unpack_from("<HHIIIHH", data, 0)
        if self.machine != 0x14C:
            raise SystemExit(f"not an x86 COFF object (machine 0x{self.machine:X})")
        self.strtab_off = self.psym + self.nsym * 18
        self.sections = []
        off = 20 + self.optsz
        for i in range(self.nsec):
            name = data[off:off + 8].rstrip(b"\x00")
            (_vsize, _vaddr, rawsz, prawptr, preloc, _plnno,
             nreloc, _nlnno, chars) = struct.unpack_from("<IIIIIIHHI", data, off + 8)
            self.sections.append(dict(index=i + 1, name=name, rawptr=prawptr, rawsz=rawsz,
                                      preloc=preloc, nreloc=nreloc, chars=chars))
            off += 40

    def symbol(self, idx):
        """(name, value, section number) of symbol idx."""
        base = self.psym + idx * 18
        raw = self.d[base:base + 8]
        if raw[:4] == b"\x00\x00\x00\x00":
            stroff = struct.unpack_from("<I", raw, 4)[0]
            end = self.d.index(b"\x00", self.strtab_off + stroff)
            name = self.d[self.strtab_off + stroff:end].decode("latin1")
        else:
            name = raw.rstrip(b"\x00").decode("latin1")
        value, secnum = struct.unpack_from("<Ih", self.d, base + 8)
        return name, value, secnum

    def symbols(self):
        i = 0
        while i < self.nsym:
            name, value, secnum = self.symbol(i)
            yield i, name, value, secnum
            i += 1 + self.d[self.psym + i * 18 + 17]          # skip aux records

    def relocs(self, sec):
        return [struct.unpack_from("<IIH", self.d, sec["preloc"] + i * 10)
                for i in range(sec["nreloc"])]

    def code_section(self, func_va=None):
        """The .text section that defines the function at func_va, else the first
        non-empty .text."""
        texts = [s for s in self.sections if s["name"] == b".text" and s["rawsz"]]
        if not texts:
            raise SystemExit("no .text section in the obj")
        if func_va is not None:
            for _i, name, value, secnum in self.symbols():
                if secnum > 0 and name_to_va(name) == func_va:
                    sec = self.sections[secnum - 1]
                    if value != 0:
                        raise SystemExit(f"{name} starts at offset {value} of its section: put "
                                         "the function alone (or first) in its source file")
                    return sec
        return texts[0]


def carve_and_fix(obj_bytes, func_va, extra=None):
    """(code bytes placed at func_va, [unresolved symbol names]). Trailing int3/nop
    padding is trimmed after the relocations are applied."""
    c = Coff(obj_bytes)
    sec = c.code_section(func_va)
    code = bytearray(c.d[sec["rawptr"]:sec["rawptr"] + sec["rawsz"]])
    unresolved = []
    for off, symidx, typ in c.relocs(sec):
        name, value, secnum = c.symbol(symidx)
        if secnum == sec["index"]:
            target = func_va + value                          # a label in this .text
        else:
            target = name_to_va(name, extra)
        if target is None:
            where = c.sections[secnum - 1]["name"].decode() if secnum > 0 else "external"
            unresolved.append(f"{name} ({where})")
            continue
        addend = struct.unpack_from("<i", code, off)[0]
        if typ == IMAGE_REL_I386_DIR32:
            val = (target + addend) & 0xFFFFFFFF
        elif typ == IMAGE_REL_I386_REL32:
            val = (target + addend - (func_va + off + 4)) & 0xFFFFFFFF
        else:
            unresolved.append(f"{name} (relocation type {typ})")
            continue
        struct.pack_into("<I", code, off, val)
    while code and code[-1] in (0xCC, 0x90):
        code.pop()
    return bytes(code), unresolved


def link(img, targets, extra=None, log=print):
    """Return (patched bytes, number linked). targets: [(va, obj path, original length)]."""
    data = bytearray(img.data)
    helpers = img.runtime_helpers()
    helpers.update(extra or {})
    linked = 0
    for va, obj, origlen in targets:
        code, unresolved = carve_and_fix(Path(obj).read_bytes(), va, helpers)
        off = img.va_to_off(va)
        log(f"0x{va:08X}  original {origlen} B, compiled {len(code)} B  ({obj})")
        if unresolved:
            log(f"  SKIP: unresolved symbols {unresolved[:8]}")
            continue
        if len(code) > origlen:
            log(f"  SKIP: {len(code)} B does not fit the {origlen} B body (in place only)")
            continue
        data[off:off + len(code)] = code
        data[off + len(code):off + origlen] = b"\xCC" * (origlen - len(code))
        linked += 1
        log(f"  linked ({len(code)} B + {origlen - len(code)} B int3)")
    return bytes(data), linked


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", metavar="VA:OBJ:LEN",
                    help="function VA, its compiled .obj, and the ORIGINAL function length in bytes")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE,
                    help="input exe or dll (default: PR2_BASE_EXE, the DRM-free PR2.exe)")
    ap.add_argument("--out", required=True, help="output file (never the input)")
    ap.add_argument("--symbol", action="append", metavar="NAME=VA",
                    help="resolve an extra symbol name to a VA (repeatable)")
    a = ap.parse_args()

    exe = kit_paths.need(a.exe, "input exe", "PR2_BASE_EXE")
    pe_image.refuse_overwrite(exe, a.out)
    img = pe_image.Image(exe)
    targets = [pe_image.parse_target(t) for t in a.targets]
    data, linked = link(img, targets, pe_image.parse_symbols(a.symbol))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(data)
    print(f"wrote {a.out}: {linked}/{len(targets)} linked"
          + ("  (byte-identical copy)" if not targets else ""))
    sys.exit(0 if linked == len(targets) else 1)


if __name__ == "__main__":
    main()
