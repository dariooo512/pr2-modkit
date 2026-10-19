#!/usr/bin/env python3
"""Example data mod: turn a town's coast tiles into buildable land (about 1.7x the land).

    python tools/files/make_maxland_tow.py -o OUTDIR                 every town
    python tools/files/make_maxland_tow.py -o OUTDIR Antigua Barbados

Reads towns\\*.tow from --src (default PR2_WORKDIR/arct/towns, the extracted archive), maps
tile 0x1E (sand/coast) to 0x02 (land) in the 128 x 226 grid and leaves water (0x00) alone, so
the town stays reachable by ship. Writes OUTDIR\\<same name>.tow. To try it, copy the files into
the test install as <test install>\\towns\\*.tow (the loose-file override) and start a NEW game:
an existing save keeps its towns' structures inside the .pr2. The output is derived from the
game's own files: keep it out of every repository and every release.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

GRID_OFF, GRID_LEN = 1, 128 * 226
COAST, LAND = 0x1E, 0x02


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("towns", nargs="*", help="only these towns (name prefixes)")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--src", type=Path, default=kit_paths.WORKDIR / "arct" / "towns")
    a = ap.parse_args(argv)
    files = sorted(a.src.glob("*.tow"))
    if a.towns:
        files = [f for f in files if any(f.name.lower().startswith(n.lower()) for n in a.towns)]
    if not files:
        print(f"no matching .tow files in {a.src}", file=sys.stderr)
        return 1
    if a.out.resolve() == a.src.resolve():
        print("refusing to overwrite the source files", file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    for f in files:
        d = bytearray(f.read_bytes())
        grid = d[GRID_OFF:GRID_OFF + GRID_LEN]
        conv = grid.count(COAST)
        d[GRID_OFF:GRID_OFF + GRID_LEN] = grid.replace(bytes([COAST]), bytes([LAND]))
        (a.out / f.name).write_bytes(d)
        land = d[GRID_OFF:GRID_OFF + GRID_LEN].count(LAND)
        print(f"{f.name:32s} +{conv:5d} coast->land  land now {land * 100 // GRID_LEN}%")
    print(f"\nwrote {len(files)} town(s) to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
