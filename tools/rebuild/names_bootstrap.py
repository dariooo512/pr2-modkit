#!/usr/bin/env python3
"""Seed a name table with every name the exe gives away for free: the import slots
(IAT VA -> DLL!function), plus optional function names you already have.

    python names_bootstrap.py                                  # -> _build/rebuild/names.tsv
    python names_bootstrap.py --functions my_names.tsv --out names.tsv

Output columns: va, kind (func|data), name, source, dll (for imports). Rows you added by hand (any source
other than auto-import / auto-symbol) are kept when the file is regenerated.

Use it to read and write rebuilt code: an indirect `call dword ptr [<slot>]` is an
imported function, declared as `extern "C" void* DAT_<slot>;` and called through it
(see README); the table tells you which one. --functions takes a TSV with 'addr' (or
'va') and 'name' columns, for example an export from your disassembler; names that
start with FUN_, LAB_ or thunk_ are skipped.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402

AUTO_SOURCES = {"auto-import", "auto-symbol"}
FIELDS = ["va", "kind", "name", "source", "dll"]


def cident(name):
    s = re.sub(r"[^0-9A-Za-z_]", "_", name)
    return "_" + s if s and s[0].isdigit() else s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="binary (default: PR2_BASE_EXE)")
    ap.add_argument("--functions", help="TSV with addr/va and name columns to add as function names")
    ap.add_argument("--out", help="the name table (default: _build/rebuild/names.tsv)")
    a = ap.parse_args()

    out = Path(a.out) if a.out else pe_image.work_dir() / "names.tsv"
    rows = {}
    if out.exists():
        with open(out, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                if r.get("source") not in AUTO_SOURCES:
                    rows[(r["va"].upper(), r["kind"])] = r

    img = pe_image.Image(kit_paths.need(a.exe, "binary", "PR2_BASE_EXE"))
    n_imp = n_sym = 0
    for slot, full in sorted(img.imports().items()):
        key = (f"0x{slot:08X}", "data")
        if key in rows:
            continue
        dll, name = full.split("!", 1)
        rows[key] = dict(va=key[0], kind="data", name=cident(name), source="auto-import", dll=dll)
        n_imp += 1

    if a.functions:
        with open(a.functions, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                addr, name = r.get("addr") or r.get("va"), (r.get("name") or "").strip()
                if not addr or not name or name.startswith(("FUN_", "LAB_", "thunk_")):
                    continue
                key = (f"0x{pe_image.parse_va(addr):08X}", "func")
                if key in rows:
                    continue
                rows[key] = dict(va=key[0], kind="func", name=cident(name), source="auto-symbol")
                n_sym += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["kind"], r["va"])))
    print(f"{out}: {len(rows)} rows (+{n_imp} imports, +{n_sym} function names)")


if __name__ == "__main__":
    main()
