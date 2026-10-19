#!/usr/bin/env python3
"""Byte-variance map over all `.tow` town files: which bytes are structure, which are layout.

    python tools/files/tow_analyze.py [DIR]      (default PR2_WORKDIR/arct, the extracted archive)

With ~80 towns of the same size, byte positions that are constant across all of them are file
structure; positions that vary are per-town layout. Prints the size groups (version 8 towns are
36,319 bytes, the two version 7 pirate hideouts 36,318), the constant/variable runs of the
majority group, the largest variable region and its factor pairs, a few headers and footers, and
the most common byte values (tile codes). Read-only. Format: docs/techniques/01-game-files.md §3.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", nargs="?", type=Path, default=kit_paths.WORKDIR / "arct")
    a = ap.parse_args(argv)
    files = sorted(a.dir.rglob("*Stadtplatz.tow")) or sorted(a.dir.rglob("*.tow"))
    if not files:
        print(f"no .tow files under {a.dir}", file=sys.stderr)
        return 1
    print(f"{len(files)} .tow files")
    allblobs = [(f, f.read_bytes()) for f in files]

    versions = Counter((b[0], len(b)) for _f, b in allblobs)
    for (ver, size), n in sorted(versions.items()):
        print(f"  version {ver}: {n} file(s) of {size} bytes")
    for f, b in allblobs:
        if b[0] != 8:
            print(f"  note: {f.name} is version {b[0]} ({len(b)} bytes; its footer is one byte shorter)")

    sizes = Counter(len(b) for _f, b in allblobs)
    maj = sizes.most_common(1)[0][0]
    group = [(f, b) for f, b in allblobs if len(b) == maj]
    blobs = [b for _f, b in group]
    print(f"analyzing {len(blobs)} files of the majority size {maj}")

    minlen = min(len(b) for _f, b in allblobs)
    div = next((i for i in range(minlen) if any(b[i] != allblobs[0][1][i] for _f, b in allblobs)), None)
    print(f"first byte position that differs across ALL files: {div}")

    n = len(blobs[0])
    const = [all(b[i] == blobs[0][i] for b in blobs) for i in range(n)]
    print(f"\nconstant positions: {sum(const)} / {n}   variable: {n - sum(const)}")
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and const[j] == const[i]:
            j += 1
        runs.append((i, j - i, "CONST" if const[i] else "VAR"))
        i = j
    print("\nfirst 20 runs (offset, length, kind):")
    for off, ln, kind in runs[:20]:
        print(f"  @{off:>6} len={ln:>6} {kind}")
    print("last 6 runs:")
    for off, ln, kind in runs[-6:]:
        print(f"  @{off:>6} len={ln:>6} {kind}")
    var = [r for r in runs if r[2] == "VAR"]
    if var:
        big = max(var, key=lambda r: r[1])
        print(f"\nlargest VARIABLE region: offset={big[0]} length={big[1]}")
        print("factor pairs:")
        for w in range(1, int(big[1] ** 0.5) + 1):
            if big[1] % w == 0:
                print(f"   {w} x {big[1] // w}")

    print("\nfirst 24 bytes of 4 towns (hex):")
    for f, b in group[:4]:
        print(f"  {f.name[:20]:<20} {b[:24].hex(' ')}")
    print("last 24 bytes of 4 towns (hex):")
    for f, b in group[:4]:
        print(f"  {f.name[:20]:<20} {b[-24:].hex(' ')}")
    hist = Counter()
    for b in blobs:
        hist.update(b)
    print("\ntop 16 byte values overall (likely tile codes):")
    for val, cnt in hist.most_common(16):
        print(f"  0x{val:02X} ({val:>3}) : {cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
