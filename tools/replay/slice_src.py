#!/usr/bin/env python3
"""Build an injectable DLL from one rebuilt function, bound to the game's real addresses.

    python slice_src.py build 0x0044DA70 path/to/0x0044DA70.cpp   # -> _build/replay/recon/recon_0x0044DA70.dll
    python slice_src.py emit  0x0044DA70 path/to/0x0044DA70.cpp   # print the generated source

The source file holds the function as `sub_<VA>` (or Ghidra's `FUN_<va>`), plus whatever
typedefs/structs it needs and extern declarations of its callees and globals. This tool:
  * cuts out the definition of the target function and renames it `fn<VA>`, the export
    name replay.py looks for;
  * binds every other callee `sub_<hex>`/`FUN_<hex>` and every global `DAT_<hex>` to its
    real absolute address with a #define (callee convention and return type from its
    prototype in the file; default cdecl / unsigned int), so at replay time the rebuild
    calls the original code and reads/writes the real (overlaid) memory;
  * compiles that one file to a standalone DLL with the 32-bit MinGW g++ (C first, then C++),
    with only KERNEL32 and msvcrt imports, so LoadLibrary works inside the suspended game.

Model every convention as __fastcall(ecx, edx, stack...) or give the real one in the
signature (__thiscall/__stdcall/__cdecl): replay jumps to the export with the recorded
registers, so the signature must match the original at the call boundary.

Needs: MINGW32_BIN (a 32-bit MinGW-w64 GCC with g++). Writes the .c/.cpp, a compile log
and the DLL to <PR2_BUILD>/replay/recon/.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import common as CM
kit_paths = CM.kit_paths

CFLAGS_C = ["-x", "c", "-std=gnu99", "-m32", "-O1", "-shared", "-w", "-fpermissive",
            "-static-libgcc", "-static"]
CFLAGS_CPP = ["-x", "c++", "-std=gnu++11", "-m32", "-O1", "-shared", "-w", "-fpermissive",
              "-static-libgcc", "-static"]

_FN = r"(?:sub|FUN)_"
_GLOB = re.compile(r"\bDAT_([0-9A-Fa-f]{6,8})\b")
_CONVATTR = {"__cdecl": "", "__fastcall": "__attribute__((fastcall))",
             "__thiscall": "__attribute__((thiscall))", "__stdcall": "__attribute__((stdcall))"}


def gxx():
    g = kit_paths.gcc()
    gp = g.with_name("g++.exe")
    if not gp.exists():
        raise SystemExit(f"no g++.exe next to {g}: install mingw-w64-i686-gcc (it ships g++)")
    return gp


def _clean_type(s):
    s = re.sub(r'\bextern\b|"C"|[{}]|__cdecl|__fastcall|__thiscall|__stdcall', " ", s)
    return re.sub(r"\s+", " ", s).strip() or "unsigned int"


def _mask_code(s):
    """Same-length copy of `s` with comment and string/char literal contents blanked, so
    boundary scans never match a ; { } inside a comment or a string."""
    out = list(s)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                out[i] = " "
                i += 1
        elif c == "/" and i + 1 < n and s[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (s[i] == "*" and i + 1 < n and s[i + 1] == "/"):
                if s[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
        elif c in "\"'":
            q = c
            i += 1
            while i < n and s[i] != q:
                if s[i] == "\\" and i + 1 < n:
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                if s[i] != "\n":
                    out[i] = " "
                i += 1
            i += 1
        else:
            i += 1
    return "".join(out)


def _hexnames(va):
    h = "%08X" % va
    return [h, h.lower(), h[2:], h[2:].lower()] if h.startswith("00") else [h, h.lower()]


def extract_def(va, text):
    """(definition renamed to fn<VA>, start index, error). The definition only: an enclosing
    `extern "C" {` wrapper is excluded."""
    alts = "|".join(_hexnames(va))
    masked = _mask_code(text)
    for m in re.finditer(r"\b%s(%s)\s*\(" % (_FN, alts), masked):
        # match on the masked text: a ( ) { } inside a comment or string must not count
        op = masked.find("(", m.start())
        cp = CM.match_paren(masked, op)
        if cp == -1:
            continue
        j = cp
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != "{":     # a prototype
            continue
        end = CM.match_brace(masked, j)
        if end == -1:
            continue
        s = max(masked.rfind(";", 0, m.start()), masked.rfind("}", 0, m.start()),
                masked.rfind("{", 0, m.start())) + 1
        defn = re.sub(r"\b%s(%s)\b" % (_FN, alts), "fn" + CM.norm_va(va), text[s:end])
        return defn, s, None
    return None, -1, "NO-DEF"


def _strip_extern_c_blocks(s):
    masked = _mask_code(s)
    out, i = [], 0
    while True:
        m = re.search(r'extern\s*"\s*"\s*\{', masked[i:])
        if not m:
            out.append(s[i:])
            break
        start = i + m.start()
        end = CM.match_brace(masked, i + m.end() - 1)
        out.append(s[i:start])
        i = end if end != -1 else len(s)
    return "".join(out)


def carry_preamble(text, func_start):
    """The file-scope type definitions before the function, minus extern declarations
    (those are rebound by #define)."""
    pre = _strip_extern_c_blocks(text[:func_start])
    pre = re.sub(r'(?m)^\s*extern\b[^;{}]*;', '', pre)
    pre = re.sub(r'extern\s*"C"\s*', '', pre)
    return pre.strip()


def _names(prefix, h):
    return sorted({prefix + h, prefix + h.lower()})


def _callee_bind(text, pfx, h):
    m = re.search(r"([A-Za-z_][\w\s\*]*?)\s*(__cdecl|__fastcall|__thiscall|__stdcall)?\s*"
                  r"%s%s\s*\(" % (pfx, h), text, re.I)
    ret = _clean_type(m.group(1)) if m else "unsigned int"
    attr = _CONVATTR.get((m.group(2) or "").lower() if m else "", "")
    ptr = "%s(%s *)()" % (ret, (" " + attr) if attr else "")
    return ["#define %s ((%s)0x%08X)" % (n, ptr, int(h, 16)) for n in _names(pfx, h)]


def _callee_bind_cpp(text, pfx, h):
    """C++: a typed function-like macro keeps the callee's arity and convention (a bare `()`
    cast means zero arguments in C++)."""
    m = re.search(r"(?:extern\s*\"C\"\s*)?(?:\{\s*)?([A-Za-z_][\w\s\*]*?)\s*"
                  r"(__cdecl|__fastcall|__thiscall|__stdcall)?\s*%s%s\s*\(([^;{]*?)\)\s*;" % (pfx, h),
                  text, re.I)
    if not m:
        return _callee_bind(text, pfx, h)
    ret = _clean_type(m.group(1))
    attr = _CONVATTR.get((m.group(2) or "").lower(), "")
    params = m.group(3).strip() or "void"
    cast = "%s (%s *)(%s)" % (ret, attr, params)
    return ["#define %s(...) ((%s)0x%08X)(__VA_ARGS__)" % (n, cast, int(h, 16)) for n in _names(pfx, h)]


def _global_bind(text, h):
    m = re.search(r"\b([A-Za-z_][\w\s\*]*?)\s+DAT_%s\s*(\[\s*\])?\s*;" % h, text, re.I)
    typ = _clean_type(m.group(1)) if m else "unsigned int"
    if m and m.group(2):        # array lvalue: DAT_X[i], decay to T* and &DAT_X all work
        val = "(*(%s(*)[1])0x%08X)" % (typ, int(h, 16))
    else:
        val = "(*(%s*)0x%08X)" % (typ, int(h, 16))
    return ["#define %s %s" % (n, val) for n in _names("DAT_", h)]


def emit_tu(va, src, lang="c"):
    text = Path(src).read_text(encoding="utf-8", errors="ignore")
    defn, fstart, err = extract_def(va, text)
    if err:
        return None, err, ""
    defn = re.sub(r'extern\s*"C"\s*', "", defn)
    preamble = carry_preamble(text, fstart)
    callees = {}
    for m in re.finditer(r"\b(sub|FUN)_([0-9A-Fa-f]{6,8})\b", defn):
        if int(m.group(2), 16) != va:
            callees[(m.group(1) + "_", m.group(2).upper())] = 1
    globs = sorted({h.upper() for h in _GLOB.findall(defn)})
    binds = []
    for pfx, h in sorted(callees):
        binds += _callee_bind_cpp(text, pfx, h) if lang == "cpp" else _callee_bind(text, pfx, h)
    for h in globs:
        binds += _global_bind(text, h)
    head = "/* fn%s from %s (lang=%s); callees and globals bound to real addresses */\n" % (
        CM.norm_va(va), Path(src).name, lang)
    detail = "callees=%d globals=%d lang=%s" % (len(callees), len(globs), lang)
    if lang == "cpp":
        tu = (head + (preamble + "\n\n" if preamble else "") + "\n".join(binds)
              + "\n\nextern \"C\" __declspec(dllexport)\n" + defn + "\n")
    else:
        # C: a tagged `struct X {...}` does not make `X` a type name -> forward typedefs
        tags = sorted(set(re.findall(r"\bstruct\s+([A-Za-z_]\w*)\s*\{", preamble + defn)))
        fwd = "".join("typedef struct %s %s;\n" % (t, t) for t in tags)
        tu = (head + "#include <stdbool.h>\n" + fwd + "\n"
              + (preamble + "\n\n" if preamble else "") + "\n".join(binds)
              + "\n\n__declspec(dllexport)\n" + defn + "\n")
    return tu, "ok", detail


def build_recon_dll(va, src, outdir=None):
    """Try C, then C++. -> (dll path or None, "OK" | "COMPILE-FAIL", detail)."""
    out = Path(outdir) if outdir else CM.outdir("recon")
    out.mkdir(parents=True, exist_ok=True)
    norm = CM.norm_va(va)
    dll = out / ("recon_0x%s.dll" % norm)
    compiler = gxx()
    env = dict(os.environ)
    env["PATH"] = str(compiler.parent) + os.pathsep + env.get("PATH", "")
    last = "compile failed"
    for lang, cflags in (("c", CFLAGS_C), ("cpp", CFLAGS_CPP)):
        tu, st, detail = emit_tu(va, src, lang)
        if tu is None:
            return None, "COMPILE-FAIL", st
        cfile = out / ("recon_0x%s.%s" % (norm, lang))
        cfile.write_text(tu, encoding="utf-8", newline="\n")
        dll.unlink(missing_ok=True)
        p = subprocess.run([str(compiler)] + cflags + [str(cfile), "-o", str(dll)],
                           env=env, capture_output=True, text=True)
        (out / ("recon_0x%s.%s.log" % (norm, lang))).write_text((p.stdout or "") + (p.stderr or ""),
                                                                 encoding="utf-8", errors="ignore")
        if p.returncode == 0 and dll.exists():
            return dll, "OK", detail
        errs = [ln for ln in (p.stderr or p.stdout or "").splitlines() if ": error:" in ln]
        msg = re.sub(r"^.*[\\/]([^\\/]+\.(?:c|cpp):)", r"\1", errs[0])[:150] if errs else "link/compile failed"
        last = msg + " [%s]" % lang
    return None, "COMPILE-FAIL", last


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("build", "emit"))
    ap.add_argument("va", help="the function's entry VA (hex)")
    ap.add_argument("src", help="the .c/.cpp file that defines sub_<VA>")
    ap.add_argument("--lang", choices=("c", "cpp"), default="c", help="emit: which flavour to print")
    ap.add_argument("--out-dir", help="where the DLL goes (default _build/replay/recon)")
    a = ap.parse_args()
    va = int(a.va, 16)
    if not Path(a.src).exists():
        raise SystemExit(f"{a.src} not found")
    if a.cmd == "emit":
        tu, st, d = emit_tu(va, a.src, a.lang)
        print("// %s %s\n%s" % (st, d, tu or ""))
        sys.exit(0 if tu else 1)
    dll, st, d = build_recon_dll(va, a.src, a.out_dir)
    print("[%s] 0x%08X -> %s  (%s)" % (st, va, dll, d))
    sys.exit(0 if dll else 1)


if __name__ == "__main__":
    main()
