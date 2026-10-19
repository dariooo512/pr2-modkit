"""Shared helpers for the Reko drivers in tools/reko/ (not a command).

Folders, all under <PR2_BUILD>/reko unless a driver's arguments say otherwise:
  raw/      per-function C from decode_isolated.py   (0x<VA8>.c)
  whole/    the whole-program project, the stubbed exe copy and Reko's program.reko/ output
  byfn/     per-function C carved from whole/program.reko/program.c by split_funcs.py
  work/     throwaway per-job folders of decode_isolated.py
  skip.txt  the working list of functions to stub for the whole-program decode
            (seeded from tools/reko/skip.txt on first use; run_whole.py appends to it)
"""
import csv
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

HERE = Path(__file__).resolve().parent
REKO_BUILD = kit_paths.BUILD / "reko"
RAW_DIR = REKO_BUILD / "raw"
WHOLE_DIR = REKO_BUILD / "whole"
BYFN_DIR = REKO_BUILD / "byfn"
WORK_DIR = REKO_BUILD / "work"
SKIP_FILE = REKO_BUILD / "skip.txt"
SKIP_SEED = HERE / "skip.txt"
# The function list Ghidra's CallGraph.java writes (tools/ghidra/ghidra.py callgraph).
DEFAULT_FUNCS = kit_paths.BUILD / "ghidra" / "out" / "worklist.tsv"
DEFAULT_CLASSES = "game,suspect_util"
CLASS_COLUMNS = ("cls", "nameClass", "class")


def decompile_exe() -> Path:
    """Reko's decompile.exe from REKO_HOME, or exit with a clear message."""
    home = kit_paths.need(kit_paths.REKO_HOME, "Reko build", "REKO_HOME")
    return kit_paths.need(home / "decompile.exe", "Reko decompile.exe", "REKO_HOME")


def load_functions(path: Path, classes=DEFAULT_CLASSES, include_all=False) -> dict:
    """Read a TSV function list: {va: (size, class)}.

    Needs columns `addr` (hex) and `size` (decimal, or hex with 0x). A class column (`cls`,
    `nameClass` or `class`) is optional; when present, only rows whose class is in `classes`
    (comma-separated) are kept, unless include_all.
    """
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"function list not found: {path}\n"
                         "make one with `python tools/ghidra/ghidra.py callgraph` "
                         "(writes <PR2_BUILD>/ghidra/out/worklist.tsv), or pass --funcs "
                         "a TSV with addr and size columns")
    keep = {c.strip() for c in classes.split(",") if c.strip()}
    out = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f, delimiter="\t")
        if not rd.fieldnames or "addr" not in rd.fieldnames or "size" not in rd.fieldnames:
            raise SystemExit(f"{path}: needs tab-separated columns 'addr' and 'size'")
        ccol = next((c for c in CLASS_COLUMNS if c in rd.fieldnames), None)
        for r in rd:
            cls = r.get(ccol, "") if ccol else ""
            if ccol and not include_all and cls not in keep:
                continue
            va = int(r["addr"], 16)
            sz = r["size"].strip()
            out[va] = (int(sz, 16) if sz.lower().startswith("0x") else int(sz), cls)
    return out


def parse_skip_line(line: str):
    """'fn00405F00', '0x00405F00' or '00405F00' -> int; blank and '#' lines -> None."""
    s = line.split("#", 1)[0].strip()
    if not s:
        return None
    s = s.upper()
    if s.startswith("FN"):
        s = s[2:]
    if s.startswith("0X"):
        s = s[2:]
    return int(s, 16)


def load_skip(path: Path = SKIP_FILE, seed: bool = True) -> list:
    """The working skip list; copied from the kit's seed list the first time."""
    path = Path(path)
    if not path.exists() and seed and path == SKIP_FILE and SKIP_SEED.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SKIP_SEED, path)
    vas = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            v = parse_skip_line(ln)
            if v is not None and v not in vas:
                vas.append(v)
    return vas


def save_skip(vas, path: Path = SKIP_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ("# Functions stubbed with `ret` for the whole-program Reko decode (see tools/reko/README.md).\n"
            "# One per line: fn<VA8>, 0x<VA> or <VA>; '#' starts a comment. run_whole.py appends here.\n")
    path.write_text(head + "".join("fn%08X\n" % v for v in sorted(set(vas))), encoding="utf-8")


def write_stubbed_exe(src: Path, dst: Path, vas) -> None:
    """Copy `src` to `dst` with the entry byte of each VA set to 0xC3 (ret). `src` is only read."""
    import pefile
    pe = pefile.PE(str(src), fast_load=True)
    data = bytearray(Path(src).read_bytes())
    base = pe.OPTIONAL_HEADER.ImageBase
    for va in vas:
        off = pe.get_offset_from_rva(va - base)
        if off is None:
            raise SystemExit("0x%08X is not backed by file data in %s" % (va, src))
        data[off] = 0xC3
    pe.close()
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # A cloud-synced folder may lock a file that was just rewritten;
    # retry a few times before giving up. Better: keep PR2_BUILD outside synced folders.
    for _ in range(5):
        try:
            dst.write_bytes(data)
            return
        except OSError:
            time.sleep(1.0)
    dst.write_bytes(data)
