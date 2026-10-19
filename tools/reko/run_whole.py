#!/usr/bin/env python3
"""Drive Reko's whole-program decode to a clean finish, stubbing the functions that crash it.

Reko decodes the whole exe in one dataflow pass (which is what keeps return values and
parameters), but Reko 0.12.2 has an SSA StackTransformer bug ("Offset must be non-negative
(Parameter 'bitOffset')") that a few functions trip, and one such function aborts the entire
run with no C output. Marking it decompile="false" does not help: the body is still analysed.
What works is to overwrite the function's entry byte with 0xC3 (ret) in a THROWAWAY copy of
the exe. Reko then sees a 1-byte function there and decodes everything else.

Reko reports only the first crasher per run, so this loops: stub the known crashers, run,
on a crash read program.reko/analysis_99_crash.txt for the new one, add it to the skip list,
repeat until a run is clean. Use a Reko built with reko-patches/ (without the performance
fixes a whole-program decode of PR2 does not finish: one ran 19 hours). With them a run took
about 4 minutes of CPU when first measured; later builds measured up to ~30 minutes, nearly all
of it in type inference, and peak memory is high (do not run two at once).

    python tools/reko/run_whole.py              # -> <PR2_BUILD>/reko/whole/program.reko/program.c
    python tools/reko/run_whole.py --max 20
    python tools/reko/split_funcs.py            # then: one file per function

The input exe (PR2_BASE_EXE) is only read; the stubbed copy is <whole>/program.exe. The skip
list is <PR2_BUILD>/reko/skip.txt (seeded from tools/reko/skip.txt).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import reko_common as rc
import gen_project
import kit_paths


def crashed(log: str) -> bool:
    return ("bitOffset" in log) or ("internal error occurred" in log)


def new_crasher(crash_dump: Path, known):
    if not crash_dump.exists():
        return None
    txt = crash_dump.read_text(encoding="utf-8", errors="ignore")
    for n in re.findall(r"fn([0-9A-Fa-f]{8})\s=+", txt):
        v = int(n, 16)
        if v not in known:
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="See tools/reko/README.md.")
    ap.add_argument("--max", type=int, default=30, help="maximum decode iterations (default 30)")
    ap.add_argument("--funcs", type=Path, default=rc.DEFAULT_FUNCS,
                    help="TSV function list (default: <PR2_BUILD>/ghidra/out/worklist.tsv)")
    ap.add_argument("--classes", default=rc.DEFAULT_CLASSES,
                    help="roots: rows whose class column is one of these (default: %(default)s)")
    ap.add_argument("--include-all", action="store_true", help="seed every row as a root")
    ap.add_argument("--exe", type=Path, default=None, help="input binary (default: PR2_BASE_EXE); only read")
    ap.add_argument("--whole", type=Path, default=rc.WHOLE_DIR,
                    help="work folder for the project, the stubbed exe and the output "
                         "(default: <PR2_BUILD>/reko/whole); keep it on a local, non-synced disk")
    ap.add_argument("--skip", type=Path, default=rc.SKIP_FILE,
                    help="skip list, updated in place (default: <PR2_BUILD>/reko/skip.txt)")
    ap.add_argument("--keep-listings", action="store_true",
                    help="also write program.asm/.dis (~50 MB); needs the emitter patch to have any effect")
    args = ap.parse_args()

    decomp = rc.decompile_exe()
    src = kit_paths.need(args.exe or kit_paths.BASE_EXE, "binary", "PR2_BASE_EXE" if args.exe is None else "--exe")
    whole = args.whole.resolve()
    whole.mkdir(parents=True, exist_ok=True)
    proj = whole / "all.dcproject"
    stubbed = whole / "program.exe"
    outdir = whole / "program.reko"
    env = dict(os.environ)
    if not args.keep_listings:
        env["REKO_SKIP_LISTINGS"] = "1"           # read by the emitter patch; stock Reko ignores it

    skip = rc.load_skip(args.skip)
    for it in range(1, args.max + 1):
        rc.write_stubbed_exe(src, stubbed, skip)
        rc.save_skip(skip, args.skip)
        gen_project.write_project(proj, args.funcs, args.classes, args.include_all, args.skip)
        shutil.rmtree(outdir, ignore_errors=True)
        print("[iter %d] skip=%d -> decoding..." % (it, len(skip)), flush=True)
        r = subprocess.run([str(decomp), "decompile", str(proj)],
                           capture_output=True, text=True, cwd=str(whole), env=env)
        log = (r.stdout or "") + (r.stderr or "")
        (whole / "run_whole.log").write_text(log, encoding="utf-8", errors="replace")
        cfile = outdir / "program.c"
        if not crashed(log) and cfile.exists():
            n = len(re.findall(r"(?m)^// [0-9A-F]{8}: ", cfile.read_text(encoding="utf-8", errors="ignore")))
            print("[iter %d] clean run: %s has ~%d functions; skip=%d" % (it, cfile, n, len(skip)))
            print("skip list: %s" % ", ".join("fn%08X" % v for v in sorted(skip)))
            return 0
        nc = new_crasher(outdir / "analysis_99_crash.txt", skip)
        if nc is None:
            print("[iter %d] Reko failed and named no new crasher; see %s" % (it, whole / "run_whole.log"))
            print(log[-600:])
            return 1
        print("[iter %d] crashed on fn%08X -> stubbing it" % (it, nc))
        skip.append(nc)
        rc.save_skip(skip, args.skip)
    print("hit --max=%d iterations; skip=%d (run again to continue)" % (args.max, len(skip)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
