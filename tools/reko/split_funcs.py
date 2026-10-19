#!/usr/bin/env python3
"""Split Reko's whole-program program.c into one file per function, 0x<VA8>.c.

Reko starts every emitted procedure with a banner comment
    // 004D5AD0: <signature>
so the address is recoverable even though Reko names functions fn<VA>. Each file runs from
its banner to the line before the next banner (or the end of the file). The per-VA layout is
the same as decode_isolated.py's, so tools that look up a function by address
(tools/ghidra/gather_decomp.py) can use either.

    python tools/reko/split_funcs.py
    python tools/reko/split_funcs.py --src <path to program.c> --out <folder>
"""
import argparse
import re
import sys
from pathlib import Path

import reko_common as rc

BANNER = re.compile(r"^//\s*([0-9A-Fa-f]{8}):\s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="See tools/reko/README.md.")
    ap.add_argument("--src", type=Path, default=rc.WHOLE_DIR / "program.reko" / "program.c",
                    help="Reko's program.c (default: <PR2_BUILD>/reko/whole/program.reko/program.c)")
    ap.add_argument("--out", type=Path, default=rc.BYFN_DIR,
                    help="output folder (default: <PR2_BUILD>/reko/byfn)")
    args = ap.parse_args()

    if not args.src.exists():
        raise SystemExit("not found: %s (run run_whole.py or decode.py first)" % args.src)
    args.out.mkdir(parents=True, exist_ok=True)
    lines = args.src.read_text(encoding="utf-8", errors="ignore").splitlines()
    idx = [(i, m.group(1).upper()) for i, ln in enumerate(lines) for m in [BANNER.match(ln)] if m]
    for k, (i, va) in enumerate(idx):
        end = idx[k + 1][0] if k + 1 < len(idx) else len(lines)
        body = "\n".join(lines[i:end]).rstrip() + "\n"
        (args.out / ("0x%s.c" % va)).write_text(body, encoding="ascii", errors="replace")
    print("wrote %d per-function files to %s" % (len(idx), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
