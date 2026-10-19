#!/usr/bin/env python3
"""Write a Reko .dcproject that seeds every known function as a procedure root.

Why: Reko's own recursive-descent scan from the PE entry point reaches only about 24
procedures in PR2.exe; the rest are reached through vtables and indirect calls it cannot
trace statically. Seeding every function Ghidra found makes Reko decode the whole call graph
in one pass, so direct-call edges give it inter-procedural register liveness and callees keep
their return values and parameters (an isolated decode loses them).

The project names its input `program.exe` (relative): the exe must sit beside the project
file. run_whole.py and decode.py put a stubbed copy there for you.

    python tools/reko/gen_project.py                       # <PR2_BUILD>/reko/whole/all.dcproject
    python tools/reko/gen_project.py --funcs my_list.tsv --include-all --out x.dcproject

Functions in the skip list are added as roots with decompile="false". (That alone does not
stop the SSA crash; run_whole.py also stubs them with `ret` in the exe copy.)
"""
import argparse
import sys
from pathlib import Path

import reko_common as rc

NS = "http://schemata.jklnet.org/Reko/v5"


def build_project(addrs, skip, exe_name="program.exe") -> str:
    """The .dcproject XML text. Reko wants addresses as hex WITHOUT 0x."""
    skip = set(skip)
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<project xmlns="%s">' % NS,
             '  <input>',
             '    <filename>%s</filename>' % exe_name,
             '    <user>']
    for a in sorted(set(addrs) | skip):
        flag = ' decompile="false"' if a in skip else ''
        lines.append('      <procedure name="fn%08X"%s>' % (a, flag))
        lines.append('        <address>%08X</address>' % a)
        lines.append('      </procedure>')
    lines += ['    </user>', '  </input>', '</project>', '']
    return "\n".join(lines)


def write_project(out: Path, funcs: Path, classes: str, include_all: bool,
                  skip_file: Path, exe_name: str = "program.exe") -> tuple:
    tbl = rc.load_functions(funcs, classes, include_all)
    skip = rc.load_skip(skip_file)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_project(tbl.keys(), skip, exe_name), encoding="utf-8")
    return len(set(tbl) | set(skip)), len(skip)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="See tools/reko/README.md.")
    ap.add_argument("--funcs", type=Path, default=rc.DEFAULT_FUNCS,
                    help="TSV function list with addr and size columns "
                         "(default: <PR2_BUILD>/ghidra/out/worklist.tsv from ghidra.py callgraph)")
    ap.add_argument("--classes", default=rc.DEFAULT_CLASSES,
                    help="keep rows whose class column (cls/nameClass) is one of these "
                         "(default: %(default)s)")
    ap.add_argument("--include-all", action="store_true", help="seed every row, ignore the class column")
    ap.add_argument("--skip", type=Path, default=rc.SKIP_FILE,
                    help="skip list (default: <PR2_BUILD>/reko/skip.txt, seeded from tools/reko/skip.txt)")
    ap.add_argument("--exe-name", default="program.exe",
                    help="input file name written into the project (default: %(default)s)")
    ap.add_argument("--out", type=Path, default=rc.WHOLE_DIR / "all.dcproject",
                    help="project file to write (default: <PR2_BUILD>/reko/whole/all.dcproject)")
    args = ap.parse_args()
    n, n_skip = write_project(args.out, args.funcs, args.classes, args.include_all,
                              args.skip, args.exe_name)
    print("wrote %s  (%d procedure roots, %d marked decompile=false)" % (args.out, n, n_skip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
