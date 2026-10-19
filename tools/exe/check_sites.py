#!/usr/bin/env python3
"""Check your own binary facts: at these addresses, the game has these instructions or bytes.

    python tools/exe/check_sites.py FACTS.txt [FACTS2.txt ...]
    python tools/exe/check_sites.py tools/exe/sites.example.txt

A facts file has one fact per line (blank lines and lines starting with # are ignored):

    <where>  <kind>  <expected>                       [# note: what it proves, where it is documented]

    0x64E03F                 insn   push 0x74; push 0x6ae380     # entry point (OEP)
    0x6ABDD8                 str    towns\\%s_Stadtplatz.tow
    A2dLib-2.20A.dll+0x2F2C0 bytes  53 55 56 57 8B E9

<where> is a VA in the exe (the DRM-free PR2.exe, setting PR2_BASE_EXE, or --exe) or
Module.dll+0xRVA for an engine DLL (found in --game, default PR2_PRISTINE, your unmodified
copy of the game folder). Kinds: `insn` (capstone text of consecutive instructions, `;`
between them), `bytes` (hex), `str` (a NUL-terminated latin-1 string; \\\\ is one backslash).

Put every address your docs or your mod rely on into such a file and run it on every change:
a FAIL means the exe is not the build you think, or a note is wrong. Keep the file short facts
(a few bytes or instructions per address), not dumps of the game's code. Read-only.
"""
import argparse
import re
import sys
from pathlib import Path

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_image import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

LINE = re.compile(r"^(\S+)\s+(insn|bytes|str)\s+(.*?)\s*(?:#\s*(.*))?$")


class Module:
    def __init__(self, path: Path):
        self.pe = pefile.PE(str(path), fast_load=True)
        self.data = self.pe.__data__

    def read_rva(self, rva: int, n: int) -> bytes:
        off = self.pe.get_offset_from_rva(rva)
        return bytes(self.data[off:off + n])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def check(img: Image, mods: dict, game: Path, where: str, kind: str, expected: str):
    if "+" in where:
        name, rva = where.split("+", 1)
        if name.lower() not in mods:
            mods[name.lower()] = Module(kit_paths.need(game / name, name, "PR2_PRISTINE"))
        mod, rva = mods[name.lower()], int(rva, 16)
        base = mod.pe.OPTIONAL_HEADER.ImageBase

        def read(n):
            return mod.read_rva(rva, n)
        va = base + rva
    else:
        va = int(where, 16)

        def read(n):
            return img.pe.get_data(va - img.base, n)
    if kind == "bytes":
        want = bytes.fromhex(expected.replace(" ", ""))
        got = read(len(want))
        return got == want, got.hex(" ")
    if kind == "str":
        want = expected.replace("\\\\", "\\").encode("latin-1") + b"\0"
        got = read(len(want))
        return got == want, got[:-1].decode("latin-1", "replace")
    want = [norm(x) for x in expected.split(";")]
    code = read(16 * len(want) + 16)
    got = [norm(f"{i.mnemonic} {i.op_str}") for i in img.md.disasm(code, va)][:len(want)]
    return got == want, "; ".join(got)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("facts", type=Path, nargs="+")
    ap.add_argument("--exe", type=Path, help="default: setting PR2_BASE_EXE")
    ap.add_argument("--game", type=Path, default=kit_paths.PRISTINE,
                    help="folder with the engine DLLs (default: setting PR2_PRISTINE)")
    a = ap.parse_args(argv)
    img, mods, n, bad = Image(a.exe), {}, 0, 0
    for f in a.facts:
        for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = LINE.match(line)
            if not m:
                print(f"{f.name}:{lineno}: cannot parse: {line}")
                bad += 1
                continue
            where, kind, expected, note = m.groups()
            n += 1
            try:
                ok, got = check(img, mods, a.game, where, kind, expected)
            except (ValueError, pefile.PEFormatError) as e:
                ok, got = False, f"error: {e}"
            if not ok:
                bad += 1
                print(f"FAIL {f.name}:{lineno} {where} {kind}\n     expected: {expected}\n     found:    {got}"
                      + (f"\n     ({note})" if note else ""))
    print(f"{n - bad}/{n} facts hold" if not bad else f"{bad} of {n} facts FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
