#!/usr/bin/env python3
"""Dump an original function's bytes, or diff them against a recompiled one.

Compares the bytes/disassembly of a reconstructed+recompiled function against
the ORIGINAL function in the DRM-free PR2.exe (setting PR2_BASE_EXE, or --exe),
so the compiler flags can be tuned until the two match
(docs/techniques/04-finding-things.md section 5; tools/rebuild/ for the full pipeline).

Two inputs:
  --orig-va 0x4XXXXX  : function start in the DRM-free exe
  --new    path.obj   : the recompiled object/exe to extract our function from
                        (or --new-bin raw.bin of just the function's bytes)

For the MVP we typically build a tiny standalone .exe/.obj with VC7.1, locate
our function (by symbol or by a unique byte signature), dump its bytes, and
diff. This script focuses on the ORIGINAL side + a flexible byte/disasm diff;
the 'new' side accepts a raw .bin you carve from the VC7.1 output.

Usage:
  python tools/exe/codegen_diff.py --orig-va 0x4a1c20 --len 48 --new-bin mine.bin
  python tools/exe/codegen_diff.py --orig-va 0x4a1c20 --len 48           # just dump orig

The dump is the game's own code: keep it for your own use, out of public repositories.
"""
import sys, argparse
from pathlib import Path
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

ap = argparse.ArgumentParser(description="Dump an original function's bytes, or diff them against a recompiled one.")
ap.add_argument("--exe", default=str(kit_paths.BASE_EXE), help="default: setting PR2_BASE_EXE")
ap.add_argument("--orig-va", required=True)
ap.add_argument("--len", type=int, default=0, help="bytes; 0 = until CC CC")
ap.add_argument("--new-bin", help="raw bytes of recompiled function")
args = ap.parse_args()

va = int(args.orig_va, 16)
pe = pefile.PE(args.exe, fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
for s in pe.sections:
    if s.Name.rstrip(b"\x00") == b".text":
        tva, tdata = base + s.VirtualAddress, s.get_data()
off = va - tva
n = args.len
if n == 0:
    n = 0
    while off + n < len(tdata) and not (tdata[off+n] == 0xCC and tdata[off+n+1] == 0xCC):
        n += 1
orig = tdata[off:off+n]
md = Cs(CS_ARCH_X86, CS_MODE_32)

def show(label, code, addr):
    print(f"\n=== {label}  ({len(code)} bytes) ===")
    print("  hex:", code.hex())
    for ins in md.disasm(code, addr):
        print(f"  0x{ins.address:X}: {ins.mnemonic:<6} {ins.op_str}")

show(f"ORIGINAL @0x{va:X}", orig, va)

if args.new_bin:
    new = open(args.new_bin, "rb").read()
    show("RECOMPILED", new, va)
    # normalize: ignore rel32 operands of call/jmp (they differ by placement)
    same = orig == new
    print(f"\nexact byte match: {same}")
    if not same and len(orig) == len(new):
        diffs = [(i, orig[i], new[i]) for i in range(len(orig)) if orig[i] != new[i]]
        print(f"{len(diffs)} differing bytes:")
        for i, a, b in diffs[:40]:
            print(f"  +0x{i:02X}: orig {a:02X}  new {b:02X}")
        print("(rel32 targets of call/jmp are expected to differ — check those "
              "land at the same logical destination, not raw byte equality)")
