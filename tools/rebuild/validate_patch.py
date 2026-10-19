#!/usr/bin/env python3
"""Check that a patched exe differs from the original ONLY inside the function bodies
you meant to replace.

    python validate_patch.py _build/rebuild/PR2_test.exe 0x0041C6F0:74
    python validate_patch.py patched.exe 0x0041C6F0:_build/rebuild/0x0041C6F0.obj:74   # relink's syntax works too
    python validate_patch.py some_copy.exe                 # no targets: any difference is a failure

Catches collateral damage such as an int3 pad that spills into the next function (this
happened once with a padding scan; the tools here take the length explicitly for that
reason). For each target it reports how many bytes changed and how long the trailing
int3 pad is. Exit code 0 when every changed byte lies inside a declared body.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402


def diff_offsets(a, b, chunk=4096):
    """File offsets where a and b differ (same length)."""
    out = []
    for base in range(0, len(a), chunk):
        x, y = a[base:base + chunk], b[base:base + chunk]
        if x != y:
            out.extend(base + i for i in range(len(x)) if x[i] != y[i])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patched", help="the patched exe")
    ap.add_argument("targets", nargs="*", metavar="VA:LEN",
                    help="each replaced function: VA and ORIGINAL length (VA:OBJ:LEN also accepted)")
    ap.add_argument("--orig", default=kit_paths.BASE_EXE,
                    help="the unpatched exe (default: PR2_BASE_EXE)")
    a = ap.parse_args()

    orig = pe_image.Image(kit_paths.need(a.orig, "original exe", "PR2_BASE_EXE"))
    b = Path(a.patched).read_bytes()
    if len(orig.data) != len(b):
        print(f"FAIL: size changed {len(orig.data)} -> {len(b)}")
        sys.exit(1)
    ranges = []
    for t in a.targets:
        va, ln = (pe_image.parse_target(t, with_obj=False) if t.count(":") == 1
                  else pe_image.parse_target(t)[0::2])
        ranges.append((va, orig.va_to_off(va), ln))

    diffs = diff_offsets(orig.data, b)
    allowed = set()
    for _va, off, ln in ranges:
        allowed.update(range(off, off + ln))
    print(f"differing bytes: {len(diffs)}")
    for va, off, ln in ranges:
        changed = sum(1 for i in diffs if off <= i < off + ln)
        pad = 0
        while pad < ln and b[off + ln - 1 - pad] == 0xCC:
            pad += 1
        print(f"  0x{va:08X}: {changed}/{ln} bytes changed, trailing int3 pad {pad} B")
    stray = [i for i in diffs if i not in allowed]
    if stray:
        print(f"FAIL: {len(stray)} byte(s) changed OUTSIDE the declared bodies:")
        for i in stray[:16]:
            print(f"    file offset 0x{i:X}: {orig.data[i]:02X} -> {b[i]:02X}")
    print("RESULT:", "INVALID (changes outside the declared bodies)" if stray
          else "VALID (only the declared bodies changed)")
    sys.exit(1 if stray else 0)


if __name__ == "__main__":
    main()
