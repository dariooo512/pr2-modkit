#!/usr/bin/env python3
"""Render a `.tow` town's terrain grid as a PNG.

    python tools/files/tow_render.py Antigua                       one town, width 226
    python tools/files/tow_render.py Antigua Havanna --widths 113 226 256
    python tools/files/tow_render.py path\\to\\X_Stadtplatz.tow -o OUTDIR

A town name is looked up as towns\\<Name>_Stadtplatz.tow under --src (default
PR2_WORKDIR/arct, the extracted archive). Images go to --out (default PR2_BUILD/tow_render) as
<Town>_w<width>.png. The grid is 128 major rows x 226 minor columns (index = major * 0xE2 +
minor), so width 226 gives a clean map; other widths interlace, which is how the layout was
found. --scale enlarges each tile. Colours: water blue, land green, sand/coast yellow, other codes
grey or their own colour. Format: docs/techniques/01-game-files.md §3.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

GRID_OFF, GRID_LEN = 1, 128 * 226
PALETTE = {0x00: (40, 90, 170), 0x02: (60, 140, 70), 0x1E: (210, 200, 120),
           0x05: (150, 75, 40), 0x04: (120, 120, 120), 0x0F: (200, 60, 60)}


def find(town: str, src: Path) -> Path:
    p = Path(town)
    if p.suffix.lower() == ".tow" and p.exists():
        return p
    hits = [f for f in src.rglob("*_Stadtplatz.tow") if f.name.lower().startswith(town.lower() + "_")]
    if not hits:
        raise SystemExit(f"no towns\\{town}_Stadtplatz.tow under {src}")
    return hits[0]


def render(blob: bytes, width: int, scale: int) -> Image.Image:
    region = blob[GRID_OFF:GRID_OFF + GRID_LEN]
    h = len(region) // width
    img = Image.new("RGB", (width, h))
    img.putdata([PALETTE.get(v, (v, v, v)) for v in region[:width * h]])
    return img.resize((width * scale, h * scale), Image.NEAREST) if scale > 1 else img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("towns", nargs="+", help="town names (Antigua) or .tow paths")
    ap.add_argument("--widths", type=int, nargs="+", default=[226])
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--src", type=Path, default=kit_paths.WORKDIR / "arct")
    ap.add_argument("-o", "--out", type=Path, default=kit_paths.BUILD / "tow_render")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for t in a.towns:
        f = find(t, a.src)
        blob = f.read_bytes()
        base = f.name.split("_")[0]
        for w in a.widths:
            img = render(blob, w, a.scale)
            path = a.out / f"{base}_w{w}.png"
            img.save(path)
            print(f"{base}: width {w} -> {path} ({img.width}x{img.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
