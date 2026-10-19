#!/usr/bin/env python3
"""Pull a function's machine code out of a `cl /FAcs` listing (.cod) and check that it
is position-independent: no call, every jump stays inside, it ends in ret.

    cl /nologo /c /O2 /FAcs my_func.c          (writes my_func.cod)
    python extract_fn.py my_func.cod my_func.bin
    python extract_fn.py my_func.cod --proc _my_func     # pick one of several functions

Only position-independent code can go through this path (patch_fn.py drops the bytes in
as they are). The listing shows 00 bytes where the linker would fix up an address, so a
function that calls or references globals must go through build.py + relink.py instead,
which resolve those references. Writes the .bin only when the check passes (or --force).
"""
import argparse
import re
import sys
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

# In a /FAcs listing the machine-code column is the SECOND tab-separated field, with or
# without a leading offset:  "  00030\t8d 04 95 00 00\t\t lea ..."  /  "\t00 00\t\t lea ..."
BYTECOL = re.compile(r"^[0-9a-fA-F]{2}(?: [0-9a-fA-F]{2})*$")


def extract(cod, proc=None):
    """{proc name: bytes} for every PROC in the listing (or just the one asked for)."""
    if not Path(cod).exists():
        raise SystemExit(f"no such listing: {cod}")
    out, cur = {}, None
    for line in open(cod, encoding="latin-1"):
        m = re.match(r"^(\S+)\s+PROC\b", line)
        if m:
            cur = m.group(1)
            out[cur] = bytearray()
            continue
        if re.match(r"^\S+\s+ENDP\b", line):
            cur = None
            continue
        if cur is None:
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2 and BYTECOL.match(parts[1].strip()):
            out[cur].extend(int(h, 16) for h in parts[1].split())
    if proc is not None:
        if proc not in out:
            raise SystemExit(f"no PROC {proc} in {cod}; found: {', '.join(out) or 'none'}")
        return {proc: out[proc]}
    return out


def check(code):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    ins = list(md.disasm(code, 0))
    if not ins:
        return ["nothing decodes"]
    problems = []
    if ins[-1].mnemonic not in ("ret", "retn"):
        problems.append(f"does not end in ret (ends in {ins[-1].mnemonic})")
    for x in ins:
        if x.mnemonic == "call":
            problems.append(f"call at +0x{x.address:X}")
        if x.mnemonic.startswith("j") or x.mnemonic.startswith("loop"):
            try:
                t = int(x.op_str, 16)
                if not 0 <= t < len(code):
                    problems.append(f"jump out of the function at +0x{x.address:X} -> 0x{t:X}")
            except ValueError:
                problems.append(f"indirect jump at +0x{x.address:X}: {x.op_str}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cod", help="the .cod listing from cl /FAcs")
    ap.add_argument("out", nargs="?", help="write the bytes here (.bin)")
    ap.add_argument("--proc", help="the PROC to extract (default: the only one in the listing)")
    ap.add_argument("--force", action="store_true", help="write even if the check fails")
    a = ap.parse_args()

    procs = extract(a.cod, a.proc)
    if not procs:
        raise SystemExit(f"no PROC found in {a.cod}")
    if len(procs) > 1:
        raise SystemExit(f"{len(procs)} functions in the listing; pick one with --proc: "
                         + ", ".join(procs))
    name, code = next(iter(procs.items()))
    code = bytes(code)
    print(f"{name}: {len(code)} bytes")
    print("hex:", code.hex())
    problems = check(code)
    print("position-independent:", "YES" if not problems else "NO")
    for p in problems:
        print("  !", p)
    if a.out and (not problems or a.force):
        Path(a.out).write_bytes(code)
        print("wrote", a.out)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
