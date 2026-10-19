#!/usr/bin/env python3
"""Find every instruction that uses a given value: a field offset, an address or a constant.

    python tools/exe/find_disp.py 0x702B14 [0x... ...]          dword anywhere in the encoding
    python tools/exe/find_disp.py 0x358 --operand                 decoded operands, any width
    python tools/exe/find_disp.py 0xE2 --operand --range 0x618FC0 0x619400

Default mode: byte-search every executable section for the value's 4-byte little-endian
encoding and print the instruction that contains it (absolute addresses, disp32 field offsets,
imm32 constants). --operand decodes the code instead (all of .text, or --range LO HI) and
prints every instruction whose operands contain the value, which also finds 8- and 16-bit
displacements and immediates (`mov al, [ecx+0x58]`, `cmp eax, 4`, `imul eax, eax, 0xe2`).
This is the "list every consumer" check before you change a field. Reads the DRM-free exe
(setting PR2_BASE_EXE) or --exe. Read-only.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_image import Image, add_exe_arg, hexint  # noqa: E402


def by_bytes(img: Image, value: int):
    """Instructions whose disp32 or imm32 field is the value (capstone's encoding offsets)."""
    pat = struct.pack("<I", value & 0xFFFFFFFF)
    for sva, raw, name in img.code_sections():
        i = raw.find(pat)
        while i >= 0:
            for start in range(i - 1, max(-1, i - 12), -1):
                ins = next(img.md.disasm(raw[start:start + 16], sva + start, 1), None)
                if ins and ((ins.disp_size == 4 and start + ins.disp_offset == i)
                            or (ins.imm_size == 4 and start + ins.imm_offset == i)):
                    yield name, ins
                    break
            i = raw.find(pat, i + 1)


def by_operand(img: Image, value: int, lo=None, hi=None):
    pat = rf"(?<![0-9a-fx]){value:#x}(?![0-9a-f])"
    if value < 10:                        # capstone prints 0..9 in decimal
        pat += rf"|(?<![\w.]){value}(?!\w)"
    want = re.compile(pat)
    start = lo if lo is not None else img.text_va
    end = hi if hi is not None else img.text_va + len(img.text)
    va = start
    while va < end:                       # linear sweep in 4 KB windows; skip a byte after data
        stop = min(end, va + 0x1000)
        nxt = va + 1
        for ins in img.disasm(va, stop + 16 - va):
            if ins.address >= stop:
                break
            nxt = ins.address + ins.size
            if want.search(ins.op_str):
                yield ".text", ins
        va = max(nxt, va + 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("values", type=hexint, nargs="+")
    add_exe_arg(ap)
    ap.add_argument("--operand", action="store_true", help="match decoded operands (any width)")
    ap.add_argument("--range", type=hexint, nargs=2, metavar=("LO", "HI"), help="limit --operand to a VA range")
    a = ap.parse_args(argv)
    img = Image(a.exe)
    for v in a.values:
        hits = list(by_operand(img, v, *(a.range or (None, None))) if a.operand else by_bytes(img, v))
        for name, ins in hits:
            print(f"0x{v:X} @ {name}: 0x{ins.address:X}: {ins.bytes.hex(' '):<22} {ins.mnemonic} {ins.op_str}")
        print(f"0x{v:X}: {len(hits)} instruction(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
