#!/usr/bin/env python3
"""Run one whole-program Reko decode of an existing project, with the patched Reko's knobs.

run_whole.py finds the crashers and leaves a project + stubbed exe in <PR2_BUILD>/reko/whole;
use this script for every later decode of that project (for example after rebuilding Reko
with a changed emitter). If the stubbed program.exe is missing beside the project, it is
written from PR2_BASE_EXE with the skip list's stubs.

    python tools/reko/decode.py
    python tools/reko/decode.py --emit-cpp                    # readable C++ (emitter patch)
    python tools/reko/decode.py --cache-save types.cache      # full decode, save the analysis
    python tools/reko/decode.py --cache-load types.cache      # re-emit in seconds
    python tools/reko/decode.py --cache-load types.cache --header-only

Output: <project folder>/program.reko/program.c and program.h; Reko's console output goes to
decode_stdout.txt / decode_stderr.txt beside the project. --copy-to copies program.c/.h to a
folder of your choice. The knobs are environment variables read by a Reko built with
reko-patches/reko-0.12.2-emit-cpp-and-flags.patch; stock Reko (or the perf patch alone)
ignores them.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import reko_common as rc
import kit_paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="See tools/reko/README.md for what each knob does.")
    ap.add_argument("--project", type=Path, default=rc.WHOLE_DIR / "all.dcproject",
                    help="the .dcproject (default: <PR2_BUILD>/reko/whole/all.dcproject from run_whole.py/gen_project.py)")
    ap.add_argument("--exe", type=Path, default=None,
                    help="source of program.exe if it is missing beside the project (default: PR2_BASE_EXE)")
    ap.add_argument("--skip", type=Path, default=rc.SKIP_FILE,
                    help="skip list used when program.exe has to be written (default: <PR2_BUILD>/reko/skip.txt)")
    ap.add_argument("--keep-listings", action="store_true",
                    help="also write program.asm/.dis (~50 MB; skipped by default: REKO_SKIP_LISTINGS=1)")
    ap.add_argument("--header-only", action="store_true", help="emit program.h only (REKO_HEADER_ONLY=1)")
    ap.add_argument("--phase-timing", action="store_true",
                    help="log the wall-clock at each phase to decode_stderr.txt (REKO_PHASE_TIMING=1)")
    ap.add_argument("--emit-cpp", action="store_true",
                    help="readable C++ member access instead of byte-offset C (REKO_EMIT_CPP=1)")
    ap.add_argument("--cache-save", type=Path, help="save the post-analysis state here (REKO_CACHE_SAVE)")
    ap.add_argument("--cache-load", type=Path,
                    help="skip scan/dataflow/types/structuring, re-emit from this cache (REKO_CACHE_LOAD)")
    ap.add_argument("--copy-to", type=Path, help="copy program.c/program.h here afterwards")
    args = ap.parse_args()

    decomp = rc.decompile_exe()
    proj = kit_paths.need(args.project.resolve(), "Reko project", "--project (run gen_project.py or run_whole.py)")
    work = proj.parent
    exe = work / "program.exe"
    if not exe.exists():
        src = kit_paths.need(args.exe or kit_paths.BASE_EXE, "binary", "PR2_BASE_EXE" if args.exe is None else "--exe")
        skip = rc.load_skip(args.skip)
        rc.write_stubbed_exe(src, exe, skip)
        print("[decode] wrote %s (%d functions stubbed)" % (exe, len(skip)))

    env = dict(os.environ)
    knobs = []

    def knob(var, value, label):
        env[var] = value
        knobs.append(label)

    if not args.keep_listings:
        knob("REKO_SKIP_LISTINGS", "1", "skipListings")
    if args.header_only:
        knob("REKO_HEADER_ONLY", "1", "headerOnly")
    if args.phase_timing:
        knob("REKO_PHASE_TIMING", "1", "phaseTiming")
    if args.emit_cpp:
        knob("REKO_EMIT_CPP", "1", "emitCpp")
    if args.cache_save:
        knob("REKO_CACHE_SAVE", str(args.cache_save.resolve()), "cacheSave")
    if args.cache_load:
        knob("REKO_CACHE_LOAD", str(kit_paths.need(args.cache_load.resolve(), "cache", "--cache-load")), "cacheLoad")

    outdir = work / (exe.stem + ".reko")
    shutil.rmtree(outdir, ignore_errors=True)
    print("[decode] decompiling %s ...%s" % (proj.name, (" [" + ",".join(knobs) + "]") if knobs else ""),
          flush=True)
    with open(work / "decode_stdout.txt", "w", encoding="utf-8") as so, \
         open(work / "decode_stderr.txt", "w", encoding="utf-8") as se:
        rcode = subprocess.run([str(decomp), "decompile", str(proj)], cwd=str(work),
                               stdout=so, stderr=se, env=env).returncode

    c, h = outdir / "program.c", outdir / "program.h"
    if not h.exists() or (not args.header_only and not c.exists()):
        print("[decode] FAILED (exit %d): no output in %s; see %s" % (rcode, outdir, work / "decode_stderr.txt"))
        return 1
    print("[decode] OK -> %s" % outdir)
    if args.copy_to:
        args.copy_to.mkdir(parents=True, exist_ok=True)
        for f in (h, c):
            if f.exists():
                shutil.copy2(f, args.copy_to / f.name)
        print("[decode] copied to %s" % args.copy_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
