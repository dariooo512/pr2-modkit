#!/usr/bin/env python3
"""Write the skeleton of a rebuilt function from its disassembly: the plumbing you would
otherwise get wrong, so only the body is left to write.

    python scaffold.py 0x0041C6F0 --size 74                  # print it
    python scaffold.py 0x0041C6F0 --size 74 --out my/0x0041C6F0.cpp

The skeleton has
  - the signature: `extern "C" <ret> __fastcall sub_<VA>(void* ecx, void* edx, ...)`
    with one `unsigned int` per stack argument (ret N / 4); a __thiscall method is
    written as __fastcall with the object in ECX and a dummy EDX; a function that
    returns with a plain `ret` but reads stack arguments gets `__cdecl` instead;
  - "return this" when the function starts with `mov eax, ecx`;
  - an extern declaration for every callee (sub_<VA>) and global (DAT_<VA>) the code
    references, imported functions marked with their DLL and name;
  - the disassembly as comments, to write the body against.

It compiles as is (the body is a stub). Fill it in and iterate with build.py.

The output is made from YOUR copy of the exe and contains its disassembly: keep it in
your own work folder, do not publish it. Never overwrites a file unless --force.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify  # noqa: E402
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402

from capstone import CS_ARCH_X86, CS_MODE_32, Cs  # noqa: E402


def scaffold(img, va, size, guessed=False):
    code = img.read(va, size)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    ins = list(md.disasm(code, va))
    if not ins:
        raise SystemExit(f"nothing decodes at 0x{va:08X}")
    attrs = classify.classify(img, va, code)
    nargs = attrs["stack_args"]
    cdecl = attrs["conv"] == "cdecl"
    return_this = not cdecl and ins[0].mnemonic == "mov" and ins[0].op_str == "eax, ecx"
    imports = img.imports()

    callees, datas = set(), set()
    for i in ins:
        is_branch = i.mnemonic == "call" or i.mnemonic.startswith("j")
        tokens = [int(t, 16) for t in re.findall(r"0x[0-9a-fA-F]+", i.op_str)]
        if is_branch and "[" not in i.op_str:
            for addr in tokens:                     # direct call / jump
                if img.is_code(addr) and not (va <= addr < va + size):
                    callees.add(addr)
        else:
            for addr in tokens:                     # [global], [reg*4 + table], push address
                if img.lo <= addr < img.hi:
                    datas.add(addr)

    conv = {"ecx": "ECX = this (write __thiscall as __fastcall)",
            "cdecl": "__cdecl (the caller pops the arguments)",
            "stack": "stack arguments only"}[attrs["conv"]]
    ret_type = "void*" if return_this else "int"
    hexva = f"{va:08X}"
    L = [f"// 0x{hexva}  {size} B{' (size guessed: check it)' if guessed else ''}  "
         f"{conv}  ret {attrs['ret_imm']}",
         "// Built with: python build.py <this file> --va 0x%s --size %d" % (hexva, size)]
    if return_this:
        L.append("// starts with 'mov eax, ecx': it returns this (void*, return ecx)")
    if attrs["edx_read"]:
        L.append("// EDX is read before it is written: a real second register argument")
    if attrs["uses_x87"]:
        L.append("// uses x87: float constants the code loads from memory are DAT_<VA> "
                 "globals (extern \"C\" const float / const double), not literals")
    if callees or datas:
        L.append('extern "C" {')
    for c in sorted(callees):
        L.append(f"  int __fastcall sub_{c:08X}(void* ecx, void* edx);   // callee: fix the "
                 "arguments and convention")
    for d in sorted(datas):
        if d in imports:
            if re.search(r"@@[A-Z][A-D]E", imports[d]):    # a C++ method: __thiscall
                how = (f"(*(RET (__fastcall**)(void* obj))&DAT_{d:08X})(obj) puts obj in ECX "
                       "without touching EDX (no arguments); with arguments see README, imported methods")
            else:
                how = f"(*(RET (__stdcall**)(ARGS))&DAT_{d:08X})(...)"
            L.append(f"  extern void* DAT_{d:08X};   // import slot {imports[d]}: call as {how}")
        else:
            L.append(f"  extern unsigned char DAT_{d:08X}[];   // global at 0x{d:08X}: give it its type")
    if callees or datas:
        L.append("}")
    stack = [f"unsigned int a{i}" for i in range(nargs)]
    if cdecl:
        L.append(f'extern "C" {ret_type} __cdecl sub_{hexva}({", ".join(stack)})')
        L.append("{")
    else:
        L.append(f'extern "C" {ret_type} __fastcall sub_{hexva}('
                 f'{", ".join(["void* ecx", "void* edx"] + stack)})')
        L.append("{")
        L.append("    (void)edx;")
    L += [f"    (void)a{i};" for i in range(nargs)]
    if attrs["conv"] == "ecx":
        L.append("    // members: *(unsigned int*)((char*)ecx + 0xNN), *(unsigned short*)..., "
                 "*(unsigned char*)...")
    L.append("    // TODO: write the body against the disassembly below")
    L.append("    return ecx;" if return_this else "    return 0;")
    L.append("}")
    L.append("")
    L.append("// --- disassembly (from your exe) ---")
    for i in ins:
        L.append(f"//  {i.address - va:>4X}: {i.mnemonic} {i.op_str}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("va", help="function VA")
    ap.add_argument("--size", type=lambda s: int(s, 0),
                    help="the function length in bytes (default: guessed)")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="binary to read (default: PR2_BASE_EXE)")
    ap.add_argument("--out", help="write the skeleton here (default: print it)")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    a = ap.parse_args()

    img = pe_image.Image(kit_paths.need(a.exe, "binary", "PR2_BASE_EXE"))
    va = pe_image.parse_va(a.va)
    if not img.is_code(va):
        raise SystemExit(f"0x{va:08X} is not in an executable section")
    size = a.size or pe_image.guess_size(img, va)
    text = scaffold(img, va, size, guessed=a.size is None)
    if a.out:
        out = Path(a.out)
        if out.exists() and not a.force:
            raise SystemExit(f"exists, not overwriting (use --force): {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
