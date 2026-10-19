#!/usr/bin/env python3
"""Compile one rebuilt function with the game's compiler and compare it byte for byte
with the original.

    python build.py my_func.cpp --va 0x0041C6F0 --size 74
    python build.py 0x0041C6F0.cpp                     # VA from the file name, size guessed

Compiles the source with the Visual C++ Toolkit 2003 (`cl /nologo /c /O2`, the flags the
game was built with), places the compiled code at the VA with every sub_<VA>/DAT_<VA>
reference resolved to the original address (relink.carve_and_fix), and compares it with
the bytes of the target binary at that VA. On a miss it prints both instruction listings
side by side and hints for the usual fixes (signedness, operand width, shift vs multiply).

Exit code: 0 byte-identical, 1 differs, 2 compile failed, 3 source refused (see --allow-asm).

The target binary defaults to PR2_BASE_EXE (the DRM-free PR2.exe). Any other 32-bit PE
works through --exe, for example one of the game's engine DLLs: the VA is looked up in
whichever section contains it, so an unpacked image whose code is not in ".text" works too.

The .obj goes to the work folder (default _build/rebuild); hand it to relink.py to put
the function into a copy of the exe. Writes nothing else.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402
from relink import carve_and_fix  # noqa: E402

from capstone import CS_ARCH_X86, CS_MODE_32, Cs  # noqa: E402

EH_PROLOGUE = b"\x64\xA1\x00\x00\x00\x00"        # mov eax, fs:[0]  (C++/SEH frame)


def hint(om, oo, rm, ro):
    """Suggestions for one differing instruction pair (original vs recompiled)."""
    hints = []
    if om in ("movzx", "movsx") and rm == "mov":
        w = "byte" if "byte" in oo else ("word" if "word" in oo else "?")
        hints.append(f"original {om} {w}: narrow the value to "
                     f"{'unsigned ' if om == 'movzx' else ''}{'char' if w == 'byte' else 'short'}")
    if om == "mov" and rm in ("movzx", "movsx"):
        hints.append("original uses a full-width mov: make the local a 32-bit int")
    for w in ("byte", "word", "dword"):
        if (w + " ptr") in oo and (w + " ptr") not in ro and ro:
            ow = next((x for x in ("byte", "word", "dword") if (x + " ptr") in ro), "?")
            hints.append(f"operand size: original {w} ptr, yours {ow} ptr: change the pointer cast")
    signed, unsigned = {"jg", "jge", "jl", "jle"}, {"ja", "jae", "jb", "jbe"}
    if om in signed and rm in unsigned:
        hints.append(f"original {om} is a signed compare: use int")
    if om in unsigned and rm in signed:
        hints.append(f"original {om} is an unsigned compare: use unsigned")
    if om in ("shl", "shr", "sar") and rm in ("imul", "mul", "lea"):
        hints.append("original shifts: write x << n")
    if om in ("imul", "mul") and rm in ("shl", "shr", "lea"):
        hints.append("original multiplies: write x * K")
    if om == "lea" and rm in ("add", "mov"):
        hints.append("original folds an add into lea: combine the address arithmetic into one expression")
    return hints


def show_diff(orig, code, va):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    O = [(i.address - va, i.mnemonic, i.op_str) for i in md.disasm(orig, va)]
    R = [(i.address - va, i.mnemonic, i.op_str) for i in md.disasm(code, va)]
    print(f"{'OFF':>4}  {'ORIGINAL':36}  RECOMPILED")
    hints = []
    if len(O) != len(R):
        hints.append(f"instruction count differs ({len(O)} original, {len(R)} yours): a statement "
                     "is extra, missing or split")
    for i in range(max(len(O), len(R))):
        o = O[i] if i < len(O) else (None, "", "")
        r = R[i] if i < len(R) else (None, "", "")
        same = (o[1], o[2]) == (r[1], r[2])
        off = o[0] if o[0] is not None else r[0]
        print(f"{off:>4X}  {(o[1] + ' ' + o[2]).strip():36.36}  "
              f"{(r[1] + ' ' + r[2]).strip():32.32}{'' if same else '  <<'}")
        if not same and o[1] and r[1]:
            hints += hint(o[1], o[2], r[1], r[2])
    if hints:
        print("\nHINTS (change the C, keep the behaviour):")
        for h in dict.fromkeys(hints):
            print(f"  - {h}")


def va_from_name(path):
    m = re.search(r"(?:0x|sub_|FUN_)([0-9A-Fa-f]{6,8})", Path(path).stem)
    return int(m.group(1), 16) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="the .c/.cpp holding the one function")
    ap.add_argument("--va", help="the function's VA (default: taken from the file name, e.g. 0x0041C6F0.cpp)")
    ap.add_argument("--size", type=lambda s: int(s, 0),
                    help="the ORIGINAL function length in bytes (from your disassembler); "
                         "without it the length is guessed")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="target binary (default: PR2_BASE_EXE)")
    ap.add_argument("--work", help="folder for the .obj (default: _build/rebuild)")
    ap.add_argument("--include", action="append", default=[], metavar="DIR",
                    help="extra include folder (repeatable), e.g. a 2003-era Platform SDK")
    ap.add_argument("--cl-flag", action="append", default=[], metavar="FLAG",
                    help="extra cl flag (repeatable); the game itself was built with plain /O2")
    ap.add_argument("--symbol", action="append", metavar="NAME=VA",
                    help="resolve an extra symbol name to a VA (repeatable)")
    ap.add_argument("--allow-asm", action="store_true",
                    help="accept __asm / __declspec(naked) / _emit in a function that has no "
                         "C++ exception frame (refused by default, see README)")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.exists():
        raise SystemExit(f"no source: {src}")
    va = pe_image.parse_va(a.va) if a.va else va_from_name(src)
    if va is None:
        raise SystemExit("give --va (the file name does not contain one)")
    img = pe_image.Image(kit_paths.need(a.exe, "target binary", "PR2_BASE_EXE"))
    if not img.is_code(va):
        raise SystemExit(f"0x{va:08X} is not in an executable section of {img.path.name}")
    size = a.size
    guessed = size is None
    if guessed:
        size = pe_image.guess_size(img, va)

    # Inline asm is accepted for functions with an exception-handling frame only: that
    # prologue cannot be written in C. Elsewhere it hides ABI mistakes (see README).
    text = src.read_text(encoding="utf-8", errors="ignore")
    is_eh = EH_PROLOGUE in img.read(va, max(size, 64))
    if not a.allow_asm:
        if re.search(r"\b__?emit\b", text):
            print(f"0x{va:08X}  REFUSED: _emit copies machine code instead of writing source "
                  "(--allow-asm to accept)")
            sys.exit(3)
        if re.search(r"__declspec\(\s*naked\s*\)|__asm\b", text):
            if not is_eh:
                print(f"0x{va:08X}  REFUSED: __asm / naked in a function without an exception "
                      "frame; write it in C (--allow-asm to accept)")
                sys.exit(3)
            print(f"0x{va:08X}  [exception-frame function: inline asm accepted]")

    cl, env = pe_image.vc71()
    work = pe_image.work_dir(a.work)
    obj = work / f"0x{va:08X}.obj"
    cmd = [str(cl), "/nologo", "/c", "/O2", *a.cl_flag, *[f"/I{d}" for d in a.include],
           str(src), f"/Fo{obj}"]
    r = subprocess.run(cmd, capture_output=True, env=env, text=True)
    if r.returncode != 0:
        print("COMPILE FAILED:\n" + (r.stdout or "") + (r.stderr or ""))
        sys.exit(2)

    helpers = img.runtime_helpers()
    helpers.update(pe_image.parse_symbols(a.symbol))
    code, unres = carve_and_fix(obj.read_bytes(), va, helpers)
    # Compare over the whole recompiled length, not only the declared size: a function's
    # real end can sit past the size a disassembler reports (an epilogue reached only by
    # forward jumps). Require len(code) >= size so a short compile cannot pass on a prefix.
    orig = img.read(va, max(size, len(code)))
    exact = (not unres) and len(code) >= size and code == orig[:len(code)]
    print(f"0x{va:08X}  original {size} B{' (guessed)' if guessed else ''} / recompiled "
          f"{len(code)} B  exact={exact}" + (f"  UNRESOLVED={unres}" if unres else ""))
    print(f"obj: {obj}")
    if len(code) > size and not guessed:
        print(f"note: {len(code)} B does not fit the {size} B body; relink.py will refuse it")
    if not exact:
        show_diff(orig, code, va)
    sys.exit(0 if exact else 1)


if __name__ == "__main__":
    main()
