#!/usr/bin/env python3
"""In-process differential fuzzer: the original machine code vs a rebuilt function.

    python fuzz.py one 0x0041C6F0 path/to/0x0041C6F0.cpp
    python fuzz.py sweep --list vas.txt --src-dir path/to/src     # src-dir/0x<VA>.cpp each

For a rebuilt function whose compiled bytes differ from the original, this asks whether it
still BEHAVES the same: it copies the original bytes (read from PR2_BASE_EXE) to an
executable page, runs them and the rebuild side by side on random inputs (random ecx object
buffer, edx and six stack arguments, same seed for both) and compares the return value
(masked to the declared return type) and the object buffer. No game, no image mapping.

Only valid for position-independent code: no calls, no absolute memory operands (globals),
no indirect jumps (switch tables). The tool disassembles the original and refuses anything
else unless --force. Functions that dereference pointers from their arguments crash on
random inputs and report NEEDS-MODEL.

The rebuild must define `sub_<VA>` (or pass --name) with a convention that matches the
original at the call boundary (__fastcall(ecx, edx, stack...) covers thiscall, fastcall and
stack-only functions). It is compiled together with a harness that uses MSVC inline
assembly and __try, so it needs the Visual C++ Toolkit 2003 (cl 13.10): set VC71_BIN to its
bin folder. Writes to <PR2_BUILD>/replay/fuzz/.

Verdicts: PASS n/n, MISMATCH (first differing iteration), NEEDS-MODEL (a side crashed),
COMPILE-FAIL, REFUSED (not position-independent). A PASS on random inputs is evidence, not
proof; promote nothing on fewer than 10 passing functions/cases of a kind.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

import capstone
from capstone import x86 as cx
import pefile

import common as CM
kit_paths = CM.kit_paths

HARNESS = r"""
/* No SDK headers (the VC Toolkit ships no windows.h); declare what we use. */
extern "C" __declspec(dllimport) void* __stdcall VirtualAlloc(void*, unsigned long, unsigned long, unsigned long);
extern "C" int printf(const char*, ...);
extern "C" void* memcpy(void*, const void*, unsigned);
extern "C" int memcmp(const void*, const void*, unsigned);
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_READWRITE 0x04
#include "%(src)s"

static const unsigned char ORIG[] = { %(bytes)s };
static unsigned char* g_orig;

/* set ecx/edx, push nargs dwords (reverse), call fn, restore esp from the frame so cdecl,
   stdcall and thiscall all unwind; return eax. */
static __declspec(naked) unsigned __cdecl call_fn(void* fn, unsigned ecx,
                                                  unsigned edx, unsigned* args, int nargs) {
    __asm {
        push ebp
        mov  ebp, esp
        push ebx
        push esi
        push edi
        mov  esi, [ebp+20]
        mov  ecx, [ebp+24]
    pushloop:
        test ecx, ecx
        jz   pushdone
        mov  eax, [esi+ecx*4-4]
        push eax
        dec  ecx
        jmp  pushloop
    pushdone:
        mov  ecx, [ebp+12]
        mov  edx, [ebp+16]
        mov  esi, [ebp+8]
        xor  eax, eax           ; same entry eax on both sides: an AL/AX-only return then
                                ; leaves identical high bits
        call esi
        lea  esp, [ebp-12]      ; drop pushed args, heal a callee's ret N
        pop  edi
        pop  esi
        pop  ebx
        mov  esp, ebp
        pop  ebp
        ret
    }
}

#define THIS_SZ 4096
#define NARGS 6
#define RETMASK %(mask)s

static unsigned rng_state = %(seed)su;
static unsigned rng(void){ unsigned x=rng_state; x^=x<<13; x^=x>>17; x^=x<<5; rng_state=x; return x; }

int main(void){
    unsigned char *tbuf_o, *tbuf_r, *save;
    unsigned args[NARGS];
    int i, j;
    g_orig = (unsigned char*)VirtualAlloc(0, sizeof(ORIG), MEM_COMMIT|MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    memcpy(g_orig, ORIG, sizeof(ORIG));
    tbuf_o = (unsigned char*)VirtualAlloc(0, THIS_SZ, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE);
    tbuf_r = (unsigned char*)VirtualAlloc(0, THIS_SZ, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE);
    save   = (unsigned char*)VirtualAlloc(0, THIS_SZ, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE);

    for (i = 0; i < %(iters)d; i++) {
        unsigned ro, rr, edx;
        for (j = 0; j < THIS_SZ; j += 4) *(unsigned*)(save+j) = rng();
        for (j = 0; j < NARGS; j++) args[j] = rng();
        edx = rng();
        memcpy(tbuf_o, save, THIS_SZ);
        __try { ro = call_fn(g_orig, (unsigned)tbuf_o, edx, args, NARGS); }
        __except(1) { printf("NEEDS-MODEL crash-orig at %%d\n", i); return 2; }
        memcpy(tbuf_r, save, THIS_SZ);
        __try { rr = call_fn((void*)&%(name)s, (unsigned)tbuf_r, edx, args, NARGS); }
        __except(1) { printf("NEEDS-MODEL crash-recon at %%d\n", i); return 2; }
        /* a function returning `this` (or this+off) gives different absolute pointers on
           the two sides: accept equal returns OR equal relative to the object buffer */
        {
            unsigned rel_o = ro - (unsigned)tbuf_o, rel_r = rr - (unsigned)tbuf_r;
            int retdiff = (RETMASK != 0) && ((ro & RETMASK) != (rr & RETMASK)) && (rel_o != rel_r);
            int bd = memcmp(tbuf_o, tbuf_r, THIS_SZ) != 0;
            if (retdiff || bd) {
                printf("MISMATCH at %%d  ret_o=%%08X ret_r=%%08X  objdiff=%%d\n", i, ro, rr, bd);
                return 1;
            }
        }
    }
    printf("PASS %%d/%%d\n", %(iters)d, %(iters)d);
    return 0;
}
"""


class Image:
    def __init__(self, exe):
        pe = pefile.PE(str(exe), fast_load=True)
        base = pe.OPTIONAL_HEADER.ImageBase
        t = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
        self.lo = base + t.VirtualAddress
        self.data = t.get_data()
        self.img_lo, self.img_hi = base, base + pe.OPTIONAL_HEADER.SizeOfImage
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

    def extent(self, va, limit=0x4000):
        """(byte length up to the function's last instruction, [reasons it is not
        position-independent]) by following the code until a ret/jmp past every forward
        branch target seen."""
        off = va - self.lo
        if not (0 <= off < len(self.data)):
            raise SystemExit("0x%08X is not in .text" % va)
        far, end, problems = va, va, []
        for ins in self.md.disasm(self.data[off:off + limit], va):
            end = ins.address + ins.size
            g = set(ins.groups)
            if cx.X86_GRP_CALL in g:
                problems.append("call at 0x%08X" % ins.address)
            for op in ins.operands:
                if op.type == cx.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0 \
                        and self.img_lo <= (op.mem.disp & 0xFFFFFFFF) < self.img_hi:
                    problems.append("absolute memory operand at 0x%08X (%s %s)"
                                    % (ins.address, ins.mnemonic, ins.op_str))
                if op.type == cx.X86_OP_MEM and op.mem.base == 0 and op.mem.index != 0 \
                        and cx.X86_GRP_JUMP in g:
                    problems.append("jump table at 0x%08X" % ins.address)
            if cx.X86_GRP_JUMP in g and ins.operands and ins.operands[0].type == cx.X86_OP_IMM:
                t = ins.operands[0].imm & 0xFFFFFFFF
                if va <= t < va + limit:
                    far = max(far, t)
            if ins.address >= far and (cx.X86_GRP_RET in g or ins.mnemonic == "jmp"):
                break
        return end - va, sorted(set(problems))

    def bytes_at(self, va, n):
        off = va - self.lo
        return self.data[off:off + n]


def recon_name_and_mask(src, va, name=None, ret_width=None):
    txt = Path(src).read_text(encoding="utf-8", errors="ignore")
    if not name:
        m = re.search(r"\b((?:sub|FUN)_(?:%08X|%08x|%06X|%06x))\s*\(" % (va, va, va, va), txt)
        name = m.group(1) if m else "sub_%08X" % va
    masks = {0: "0", 8: "0xFFu", 16: "0xFFFFu", 32: "0xFFFFFFFFu"}
    if ret_width is not None:
        return name, masks[ret_width]
    d = re.search(r"([A-Za-z_][\w\s\*]*?)\b%s\s*\(" % re.escape(name), txt)
    rt = (d.group(1) if d else "unsigned int").strip()
    rt = re.sub(r'\bextern\b|"C"|__fastcall|__cdecl|__thiscall|__stdcall|static|inline', "", rt).strip()
    if rt == "void":
        return name, "0"
    if "*" in rt:
        return name, "0xFFFFFFFFu"
    if "short" in rt:
        return name, "0xFFFFu"
    if re.search(r"\b(bool|char)\b", rt):
        return name, "0xFFu"
    return name, "0xFFFFFFFFu"


def toolkit_env():
    bin_ = kit_paths.need(kit_paths.VC71_BIN, "the Visual C++ Toolkit 2003 bin folder (cl 13.10)", "VC71_BIN")
    tk = bin_.parent
    env = dict(os.environ)
    env["PATH"] = str(bin_) + os.pathsep + env.get("PATH", "")
    inc = [tk / "include", tk / "PlatformSDK" / "include"]
    lib = [tk / "lib", tk / "PlatformSDK" / "lib"]
    env["INCLUDE"] = ";".join(str(p) for p in inc if p.exists())
    env["LIB"] = ";".join(str(p) for p in lib if p.exists())
    return bin_ / "cl.exe", env


def run(img, va, src, iters, size=None, name=None, ret_width=None, force=False, seed=0x9E3779B9):
    n, problems = img.extent(va)
    if problems and not force:
        return "REFUSED", "; ".join(problems[:3]) + (" ..." if len(problems) > 3 else "")
    n = size or n
    blob = img.bytes_at(va, n + 64)        # margin, so a forward-jump epilogue is included
    name, mask = recon_name_and_mask(src, va, name, ret_width)
    out = CM.outdir("fuzz")
    vahex = "0x%08X" % va
    cfile = out / ("fuzz_%s.cpp" % vahex)
    exe = out / ("fuzz_%s.exe" % vahex)
    cfile.write_text(HARNESS % dict(src=str(Path(src).resolve()).replace("\\", "/"),
                                    bytes=", ".join("0x%02x" % b for b in blob), mask=mask,
                                    name=name, iters=iters, seed=seed & 0xFFFFFFFF),
                     encoding="utf-8", newline="\n")
    cl, env = toolkit_env()
    c = subprocess.run([str(cl), "/nologo", "/O2", "/EHa", str(cfile), "/Fe" + str(exe),
                        "/Fo" + str(cfile) + ".obj", "/link", "/SUBSYSTEM:CONSOLE", "kernel32.lib"],
                       capture_output=True, env=env, text=True, cwd=str(out))
    if c.returncode != 0:
        lines = (c.stdout + c.stderr).strip().splitlines()
        return "COMPILE-FAIL", (lines[-1] if lines else "cl failed")[:160]
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    text = (r.stdout + r.stderr).strip()
    return (text.split()[0] if text else "NO-OUTPUT"), text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def opts(p):
        p.add_argument("--iters", type=int, default=300000, help="random inputs per function (default 300000)")
        p.add_argument("--ret-width", type=int, choices=(0, 8, 16, 32),
                       help="bits of eax compared (0 = void); default from the rebuild's return type")
        p.add_argument("--force", action="store_true", help="fuzz even if the original is not position-independent")
        p.add_argument("--exe", help="exe to read the original bytes from (default PR2_BASE_EXE)")
    p = sub.add_parser("one", help="fuzz one function")
    p.add_argument("va")
    p.add_argument("src", help="the rebuild (.cpp, compiled by cl 13.10)")
    p.add_argument("--size", type=int, help="bytes of original code (default: follow the code)")
    p.add_argument("--name", help="the rebuild's function name (default sub_<VA>)")
    opts(p)
    p = sub.add_parser("sweep", help="fuzz every VA of a list whose source exists")
    p.add_argument("--list", required=True, help="TSV with an addr column, or one hex VA per line")
    p.add_argument("--src-dir", required=True, help="folder with 0x<VA>.cpp files")
    opts(p)
    a = ap.parse_args()
    img = Image(kit_paths.need(Path(a.exe) if a.exe else kit_paths.BASE_EXE, "exe", "PR2_BASE_EXE"))
    if a.cmd == "one":
        verdict, detail = run(img, int(a.va, 16), a.src, a.iters, a.size, a.name, a.ret_width, a.force)
        print(f"[{verdict}] 0x{int(a.va, 16):08X}  {detail}")
        sys.exit(0 if verdict == "PASS" else 1)
    results = []
    vas, rows = CM.load_targets(a.list)
    for va in vas:
        src = Path(a.src_dir) / ("0x%08X.cpp" % va)
        if not src.exists():
            continue
        size = rows[va].get("size", "")
        verdict, detail = run(img, va, src, a.iters, int(size) if size.isdigit() else None,
                              None, a.ret_width, a.force)
        results.append(("0x%08X" % va, verdict, detail))
        print(f"  [{verdict:13}] 0x{va:08X}  {detail}", flush=True)
    from collections import Counter
    print("\n=== %d functions ===" % len(results))
    for v, n in Counter(v for _, v, _ in results).most_common():
        print(f"   {v:13} {n}")
    rep = CM.outdir("fuzz") / "fuzz_report.tsv"
    with open(rep, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["va", "verdict", "detail"])
        w.writerows(results)
    print(f"wrote {rep}")


if __name__ == "__main__":
    main()
