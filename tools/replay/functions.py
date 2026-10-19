#!/usr/bin/env python3
"""List the function entry points of an exe: the target list the recorder and coverage tools arm.

    python functions.py                          # -> _build/replay/functions.tsv
    python functions.py --min-callers 2 --out my_targets.tsv
    python functions.py --exe path/to/PR2.exe.unpacked.exe

A function entry here is the target of a direct `call rel32` in .text that is also the start
of an instruction in a linear disassembly of .text (so a call decoded from data or from the
middle of another instruction does not count). Functions reached only through vtables or
function pointers are missed; add them from a Ghidra/Reko export if you need them (any TSV
with an `addr` column, or a text file with one hex VA per line, works as a target list).

Every INT3 the recorder sets goes at one of these addresses. An address that is not a real
instruction start corrupts the game's code, so prefer --min-callers 2 for a first run.

Output columns: addr, callers (number of distinct call sites), size (bytes up to the next
listed entry; an upper bound).
"""
import argparse
from pathlib import Path

import capstone
import pefile

import common as CM
kit_paths = CM.kit_paths


def scan(exe: Path):
    pe = pefile.PE(str(exe), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    lo = base + text.VirtualAddress
    data = text.get_data()[:text.Misc_VirtualSize]
    hi = lo + len(data)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    starts = set()
    calls = {}                                  # target -> set(call sites)
    off = 0
    while off < len(data):
        n = 0
        for addr, size, mnem, ops in md.disasm_lite(data[off:], lo + off):
            starts.add(addr)
            n += size
            if mnem == "call" and size == 5 and data[addr - lo] == 0xE8:
                tgt = int(ops, 16) if ops.startswith("0x") else None
                if tgt is not None and lo <= tgt < hi:
                    calls.setdefault(tgt, set()).add(addr)
        off += n if n else 1                    # undecodable byte: skip it and resync
    return {t: len(s) for t, s in calls.items() if t in starts}, hi


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 2)[2],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default=None,
                    help="the exe to scan (default: PR2_BASE_EXE, the DRM-free 1.1.2.3 exe)")
    ap.add_argument("--min-callers", type=int, default=1,
                    help="keep only functions called from at least N sites (default 1)")
    ap.add_argument("--out", default=None, help="output TSV (default _build/replay/functions.tsv)")
    a = ap.parse_args()
    exe = kit_paths.need(Path(a.exe) if a.exe else kit_paths.BASE_EXE, "exe to scan", "PR2_BASE_EXE")
    found, text_end = scan(exe)
    keep = sorted(va for va, n in found.items() if n >= a.min_callers)
    out = Path(a.out) if a.out else CM.outdir() / "functions.tsv"
    with open(out, "w", newline="\n") as f:
        f.write("addr\tcallers\tsize\n")
        for i, va in enumerate(keep):
            nxt = keep[i + 1] if i + 1 < len(keep) else text_end
            f.write("0x%08X\t%d\t%d\n" % (va, found[va], nxt - va))
    print(f"{len(keep)} function entries ({len(found)} call targets, min callers "
          f"{a.min_callers}) -> {out}")


if __name__ == "__main__":
    main()
