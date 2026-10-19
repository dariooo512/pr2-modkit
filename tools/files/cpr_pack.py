#!/usr/bin/env python3
"""Pack a folder into a Port Royale 2 `.cpr` archive (single or multi-segment).

    python tools/files/cpr_pack.py DIR OUT.cpr                       files sorted by path
    python tools/files/cpr_pack.py DIR OUT.cpr --like ORIGINAL.cpr   the original's order,
                                                                     segments and duplicates
    python tools/files/cpr_pack.py DIR --like ORIGINAL.cpr --check   build in memory and compare
                                                                     with ORIGINAL byte for byte

With --like, every file the original lists is taken from DIR under its stored name (a repeated
name's second copy from `name__seg1.ext`, as cpr_extract.py writes it), in the original's
directory order and segments; files in DIR that the original does not list follow in new
segments, sorted. Packing an unchanged extraction with --like gives the original archive byte
for byte (checked on all four archives of 1.1.2.3).

You rarely need this: the game loads a loose file in its folder in place of the archived one
(docs/techniques/01-game-files.md). That a repacked archive with changed or added files loads in
the game has not been tested.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpr_archive import build, read_index  # noqa: E402
from cpr_extract import dup_name  # noqa: E402


def collect(root: Path):
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(root)).replace(os.sep, "\\")] = p
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("directory", type=Path)
    ap.add_argument("output", type=Path, nargs="?")
    ap.add_argument("--like", type=Path, help="take order, segments and duplicates from this archive")
    ap.add_argument("--check", action="store_true", help="compare with --like instead of writing")
    a = ap.parse_args(argv)
    if a.check and not a.like:
        ap.error("--check needs --like")
    if not a.check and not a.output:
        ap.error("give OUT.cpr (or --check)")

    have = collect(a.directory)
    lower = {k.lower(): k for k in have}
    used, files, segs = set(), [], []
    if a.like:
        orig = a.like.read_bytes()
        seen = {}
        for e in read_index(orig):
            key = e.name.lower()
            seen[key] = seen.get(key, -1) + 1
            want = dup_name(e.name, seen[key]) if seen[key] else e.name
            match = lower.get(want.lower())
            if match is None:
                print(f"error: {want} is in {a.like.name} but not in {a.directory}", file=sys.stderr)
                return 1
            used.add(match)
            files.append((e.name, have[match].read_bytes()))
            segs.append(e.segment)
    extra = sorted((k for k in have if k not in used), key=str.lower)
    if extra:
        start = (segs[-1] + 1) if segs else 0
        tail = build([(k, b"") for k in extra])          # greedy segmentation of the extras
        tail_segs = [e.segment + start for e in read_index(tail)]
        files += [(k, have[k].read_bytes()) for k in extra]
        segs += tail_segs
    data = build(files, segs)
    print(f"{len(files)} files in {len(set(segs))} segment(s), {len(data)} bytes"
          + (f" ({len(extra)} not in {a.like.name})" if a.like and extra else ""))
    if a.check:
        if data == orig:
            print(f"IDENTICAL to {a.like}  sha256 {hashlib.sha256(data).hexdigest()}")
            return 0
        n = min(len(data), len(orig))
        first = next((i for i in range(n) if data[i] != orig[i]), n)
        print(f"DIFFERENT from {a.like}: sizes {len(data)} vs {len(orig)}, first difference at {first:#x}")
        return 1
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_bytes(data)
    print(f"wrote {a.output}  sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
