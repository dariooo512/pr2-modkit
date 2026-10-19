#!/usr/bin/env python3
"""Disassemble a function or a range, list its calls, or find who calls or jumps to it.

    python tools/exe/disasm_func.py 0x41C6F0                 function up to the CC padding
    python tools/exe/disasm_func.py 0x41C6F0 --len 0x40      a byte range instead
    python tools/exe/disasm_func.py 0x41C6F0 --bytes         with the instruction bytes
    python tools/exe/disasm_func.py 0x41C6F0 --callers       every E8 call / E9 jmp rel32 to it

Reads the DRM-free exe (setting PR2_BASE_EXE) or --exe. Read-only. Indirect calls (through a
vtable or a function pointer) are not found by --callers; use pe_xref.py for references to an
address stored as data.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_image import Image, add_exe_arg, hexint  # noqa: E402


def callers(img: Image, va: int):
    out = []
    for sva, raw, _name in img.code_sections():
        for i in range(len(raw) - 4):
            op = raw[i]
            if op in (0xE8, 0xE9) and sva + i + 5 + struct.unpack_from("<i", raw, i + 1)[0] == va:
                out.append(("call" if op == 0xE8 else "jmp ", sva + i))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("va", type=hexint)
    add_exe_arg(ap)
    ap.add_argument("--len", type=hexint, help="disassemble this many bytes (hex) instead of to the padding")
    ap.add_argument("--bytes", action="store_true", help="show the instruction bytes")
    ap.add_argument("--callers", action="store_true", help="find E8/E9 rel32 sites that target VA")
    a = ap.parse_args(argv)
    img = Image(a.exe)

    if a.callers:
        found = callers(img, a.va)
        print(f"callers of 0x{a.va:X}: {len(found)}")
        for kind, at in found:
            print(f"  {kind} at 0x{at:X}")
        return 0

    n = a.len if a.len else img.fn_end(a.va) - img.text_off(a.va)
    print(f"{'range' if a.len else 'function'} 0x{a.va:X} .. ~0x{a.va + n:X}")
    calls = []
    for ins in img.disasm(a.va, n):
        b = f"{ins.bytes.hex(' '):<24} " if a.bytes else ""
        print(f"  0x{ins.address:X}: {b}{ins.mnemonic:<7} {ins.op_str}")
        if ins.mnemonic == "call":
            calls.append((ins.address, ins.op_str))
    if calls:
        print("\nCALL targets:")
        for at, t in calls:
            print(f"  0x{at:X} -> {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
