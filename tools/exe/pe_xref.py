#!/usr/bin/env python3
"""Find code that references an address (a string, a global, a vtable) and show the function.

    python tools/exe/pe_xref.py 0x6ABDD8 [0x... ...]
    python tools/exe/pe_xref.py 0x6ABDD8 --no-disasm          sites only

Byte-searches .text for the address's little-endian encoding (so it works without a
disassembler's analysis), then disassembles the enclosing function (found by scanning back for
`push ebp; mov ebp, esp` or CC padding) and marks the referencing line. Reads the DRM-free exe
(setting PR2_BASE_EXE) or --exe. Read-only.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_image import Image, add_exe_arg, hexint  # noqa: E402


def func_start(text: bytes, off: int) -> int:
    best = None
    for i in range(off, max(0, off - 0x1200), -1):
        if text[i:i + 3] == b"\x55\x8B\xEC":
            return i
        if text[i] == 0xCC and text[i + 1] == 0xCC and i + 2 <= off and best is None:
            best = i + 2
    return best if best is not None else max(0, off - 0x80)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", type=hexint, nargs="+")
    add_exe_arg(ap)
    ap.add_argument("--no-disasm", action="store_true", help="print only the referencing sites")
    a = ap.parse_args(argv)
    img = Image(a.exe)
    text, tva = img.text, img.text_va
    for tgt in a.targets:
        le = struct.pack("<I", tgt)
        xrefs, k = [], text.find(le)
        while k >= 0:
            xrefs.append(k)
            k = text.find(le, k + 1)
        print(f"\n########## target 0x{tgt:X}: {len(xrefs)} xref(s) in .text ##########")
        for k in xrefs:
            fs = func_start(text, k)
            print(f"\n--- xref at 0x{tva + k:X} (func ~0x{tva + fs:X}) ---")
            if a.no_disasm:
                continue
            end = fs
            while end < len(text) - 1 and end < fs + 0x600:
                if text[end] == 0xCC and text[end + 1] == 0xCC and end > k:
                    break
                end += 1
            for ins in img.md.disasm(text[fs:end], tva + fs):
                mark = "   <===" if ins.address <= tva + k < ins.address + ins.size else ""
                print(f"  0x{ins.address:X}: {ins.mnemonic:<7} {ins.op_str}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
