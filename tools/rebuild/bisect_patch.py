#!/usr/bin/env python3
"""Find which of several rebuilt functions breaks the game, by bisection.

Given a set of functions that work one by one but fail together, each step links a
subset into a fresh copy of the exe (relink.py) and runs YOUR test command on it. The
command decides: exit code 0 = good, anything else = bad. `{exe}` in the command is
replaced by the path of the patched exe for that step.

    python bisect_patch.py --test "python ../test/my_smoke_test.py --exe {exe}" \\
        0x0041C6F0:_build/rebuild/0x0041C6F0.obj:74 0x005E3720:_build/rebuild/0x005E3720.obj:112
    python bisect_patch.py --list targets.txt --test "..."      # one VA:OBJ:LEN per line

This tool never starts the game itself; the test command does (for example the kit's
test install, see docs/techniques/07-testing.md). Assumes a single culprit; when neither
half fails alone it falls back to removing one function at a time from the failing group.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
import relink  # noqa: E402
from pe_image import kit_paths  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", metavar="VA:OBJ:LEN")
    ap.add_argument("--list", help="file with one VA:OBJ:LEN per line (# comments allowed)")
    ap.add_argument("--test", required=True,
                    help="test command; {exe} is replaced by the patched exe; exit 0 = good")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="input exe (default: PR2_BASE_EXE)")
    ap.add_argument("--work", help="folder for the step exes (default: _build/rebuild/bisect)")
    ap.add_argument("--name", default="PR2_bisect.exe", help="file name of the step exe")
    ap.add_argument("--timeout", type=float, default=600, help="seconds per test run (default 600)")
    a = ap.parse_args()

    lines = list(a.targets)
    if a.list:
        lines += [ln.split("#", 1)[0].strip() for ln in Path(a.list).read_text().splitlines()]
    cands = [pe_image.parse_target(t) for t in lines if t]
    if not cands:
        raise SystemExit("no targets")
    img = pe_image.Image(kit_paths.need(a.exe, "input exe", "PR2_BASE_EXE"))
    work = pe_image.work_dir(a.work or (kit_paths.BUILD / "rebuild" / "bisect"))
    step_exe = work / a.name
    pe_image.refuse_overwrite(img.path, step_exe)
    runs = [0]

    def bad(subset):
        data, n = relink.link(img, subset, log=lambda *_: None)
        if n != len(subset):
            raise SystemExit("some targets do not link; run relink.py on them first")
        step_exe.write_bytes(data)
        cmd = a.test.replace("{exe}", str(step_exe))
        try:
            r = subprocess.run(cmd, shell=True, timeout=a.timeout)
            failed = r.returncode != 0
        except subprocess.TimeoutExpired:
            failed = True
        runs[0] += 1
        print(f"  run {runs[0]}: {len(subset):3d} functions -> {'BAD' if failed else 'good'}")
        return failed

    print(f"baseline: all {len(cands)} functions")
    if not bad(cands):
        print("the full set passes the test; nothing to bisect (or the failure is not reproducible)")
        return
    group = cands[:]
    while len(group) > 1:
        mid = len(group) // 2
        left, right = group[:mid], group[mid:]
        print(f"bisect: {len(group)} -> {len(left)} / {len(right)}")
        if bad(left):
            group = left
        elif bad(right):
            group = right
        else:
            print("  neither half fails alone (an interaction); removing one at a time")
            for i in range(len(group)):
                if not bad(group[:i] + group[i + 1:]):
                    print(f"\nremoving 0x{group[i][0]:08X} makes the failure go away: culprit")
                    return
            print("  could not isolate one function; group:",
                  " ".join(f"0x{v:08X}" for v, _o, _l in group))
            sys.exit(1)
    print(f"\nCULPRIT: 0x{group[0][0]:08X} ({group[0][1]}, {group[0][2]} B)")


if __name__ == "__main__":
    main()
