#!/usr/bin/env python3
"""Put every available decompiler's view of a function side by side in one file.

Reading one decompiler alone, you inherit its mistakes (wrong signedness, a missed cast, a
mis-modelled structure offset, a dropped branch). Where two independent decompilers agree,
the logic is almost certainly right; where they differ, the disassembly and raw bytes decide.

For each VA it writes <out>/0x<VA8>.multi.txt with labelled sections:
  GHIDRA  <ghidra-dir>/0x<VA8>.decomp.txt from `ghidra.py dump` (pseudo-C + disassembly +
          raw bytes: the authoritative section, the bytes are ground truth)
  REKO    the first of <reko-dir>/0x<VA8>.c found (default: whole-program split, byfn/, then
          the isolated decode, raw/), else carved straight out of --reko-program
  RETDEC  <retdec-dir>/0x<VA8>.c or .retdec.c, when you give --retdec-dir

    python tools/ghidra/ghidra.py dump 0x41C6F0       # Ghidra view first
    python tools/ghidra/gather_decomp.py 0x41C6F0 0x4D5AD0

Defaults are under PR2_BUILD: ghidra/out, reko/byfn + reko/raw, reko/whole/program.reko/program.c,
output multi/. The files hold decompiled game code: keep them out of anything you publish.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

B = kit_paths.BUILD
SEP = "=" * 80


def norm(va: str) -> str:
    s = va.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return "0x%08X" % int(s, 16)


def read(p: Path):
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else None


def reko_view(va: str, dirs, program_c: Path):
    for d in dirs:
        t = read(Path(d) / (va + ".c"))
        if t is not None:
            return t, str(d)
    if program_c and program_c.exists():
        key = "// " + va[2:] + ":"
        out, grab = [], False
        with open(program_c, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                if ln.startswith(key):
                    grab = True
                if grab:
                    out.append(ln.rstrip("\n"))
                    if ln.rstrip("\n") == "}":
                        break
        if out:
            return "\n".join(out), str(program_c)
    return None, None


def retdec_view(va: str, dirs):
    for d in dirs:
        for name in (va + ".c", va + ".retdec.c", va.lower() + ".c"):
            t = read(Path(d) / name)
            if t is not None:
                return t
    return None


def build(va_in: str, args):
    va = norm(va_in)
    g = read(Path(args.ghidra_dir) / (va + ".decomp.txt"))
    r, rsrc = reko_view(va, args.reko_dir, args.reko_program)
    rd = retdec_view(va, args.retdec_dir)
    found = [n for n, v in (("ghidra", g), ("reko", r), ("retdec", rd)) if v]
    parts = [SEP,
             " MULTI-DECOMPILER VIEW  --  %s" % va,
             " %d independent decompilation(s): %s." % (len(found), ", ".join(found) or "none"),
             " Where they AGREE, trust it. Where they DIFFER, the GHIDRA disassembly and",
             " RAW BYTES are ground truth.",
             SEP, ""]
    parts += ["#" * 12 + "  GHIDRA  (pseudo-C + disassembly + RAW BYTES -- AUTHORITATIVE)  " + "#" * 12,
              g or "(no Ghidra view: run `python tools/ghidra/ghidra.py dump %s`)" % va, ""]
    parts += ["#" * 12 + "  REKO  %s  " % ("(" + rsrc + ")" if rsrc else "") + "#" * 12,
              r or "(no Reko view: run tools/reko/decode_isolated.py, or run_whole.py + split_funcs.py)", ""]
    parts += ["#" * 12 + "  RETDEC  " + "#" * 12,
              rd or "(no RetDec view for this function)", ""]
    out = Path(args.out) / (va + ".multi.txt")
    out.write_text("\n".join(parts), encoding="utf-8")
    return va, found, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("vas", nargs="+", help="function addresses, hex (0x41C6F0)")
    ap.add_argument("--ghidra-dir", type=Path, default=B / "ghidra" / "out",
                    help="folder with 0x<VA8>.decomp.txt (default: <PR2_BUILD>/ghidra/out)")
    ap.add_argument("--reko-dir", type=Path, action="append", default=None,
                    help="folder with 0x<VA8>.c, repeatable, first hit wins "
                         "(default: <PR2_BUILD>/reko/byfn then <PR2_BUILD>/reko/raw)")
    ap.add_argument("--reko-program", type=Path, default=B / "reko" / "whole" / "program.reko" / "program.c",
                    help="whole-program program.c to carve from when no per-function file exists")
    ap.add_argument("--retdec-dir", type=Path, action="append", default=[],
                    help="folder with RetDec per-function slices (repeatable; none by default)")
    ap.add_argument("--out", type=Path, default=B / "multi",
                    help="output folder (default: <PR2_BUILD>/multi)")
    args = ap.parse_args()
    if args.reko_dir is None:
        args.reko_dir = [B / "reko" / "byfn", B / "reko" / "raw"]
    args.out.mkdir(parents=True, exist_ok=True)
    for a in args.vas:
        va, found, out = build(a, args)
        print("%s -> %s  [%s]" % (va, out, ", ".join(found) or "NONE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
