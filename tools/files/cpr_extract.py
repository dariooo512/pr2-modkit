#!/usr/bin/env python3
"""Extract, list or map a Port Royale 2 `.cpr` archive (single or multi-segment).

    python tools/files/cpr_extract.py <game>\\pr2_arcs.cpr --map        segment map and file types
    python tools/files/cpr_extract.py <game>\\pr2_arcs.cpr -l           list every file
    python tools/files/cpr_extract.py <game>\\pr2_arcs.cpr [-d OUT]     extract

Without -d the files go to PR2_WORKDIR\\<name> (pr2_arcs.cpr -> arcs\\), the layout the other
tools read (tools/kit_paths.py). Read-only on the archive; run it on your own game files and
keep the output out of every repository (it is the game's data).

A name that occurs twice is extracted the second time as `name__seg1.ext` (cpr_pack.py --like
puts it back under its own name).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402
from cpr_archive import read_index  # noqa: E402


def default_out(archive: Path) -> Path:
    stem = archive.stem.lower()
    return kit_paths.WORKDIR / (stem[4:] if stem.startswith("pr2_") else stem)


def dup_name(rel: str, k: int) -> str:
    root, ext = os.path.splitext(rel)
    return f"{root}__seg{k}{ext}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive", type=Path)
    ap.add_argument("-d", "--directory", type=Path, help="output folder (default PR2_WORKDIR\\<name>)")
    ap.add_argument("-l", "--list", action="store_true", help="list the files, extract nothing")
    ap.add_argument("--map", action="store_true", help="print the segment map only")
    a = ap.parse_args(argv)

    data = a.archive.read_bytes()
    try:
        entries = read_index(data, log=print)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\nTOTAL: {len({e.segment for e in entries})} segments, {len(entries)} files")
    exts = {}
    for e in entries:
        x = os.path.splitext(e.name)[1].lower() or "<none>"
        exts[x] = exts.get(x, 0) + 1
    for x, n in sorted(exts.items(), key=lambda kv: -kv[1]):
        print(f"  {x:<8} {n}")
    if a.map:
        return 0
    if a.list:
        for e in entries:
            print(f"{e.segment:>3} {e.offset:#010x} {e.size:>9}  {e.name}")
        return 0

    out = a.directory or default_out(a.archive)
    seen = {}
    for e in entries:
        rel = e.name.replace("\\", os.sep).replace("/", os.sep)
        key = rel.lower()
        seen[key] = seen.get(key, -1) + 1
        if seen[key]:
            rel = dup_name(rel, seen[key])
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data[e.offset:e.offset + e.size])
    print(f"\nextracted {len(entries)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
