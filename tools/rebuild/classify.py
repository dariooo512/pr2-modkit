#!/usr/bin/env python3
"""Static classification of functions: what a rebuild of each one involves, and how it
can be verified.

    python classify.py 0x0041C6F0:74 0x005E3720
    python classify.py --list functions.tsv --out _build/rebuild/classified.tsv

Disassembles each function from the exe (capstone) and derives:

  self_contained  no global/image address, no call, no jump out of the body, no x87:
                  the original bytes can be RUN on their own (copied to executable
                  memory), so an execution oracle can compare them with your rebuild
                  (method A, gen_harness.py). Otherwise only a byte or structure
                  comparison is possible (method B, build.py).
  reads_global    a memory operand whose displacement lies inside the image
  imm_image_ref   an immediate that is an image address (a pushed string, a table)
  has_call        any call (direct, import, vtable)
  jumps_out       a jump that leaves the body (tail call, switch through a table)
  uses_x87        floating point
  ret_imm         bytes the function pops on return (ret N)
  stack_args      stack arguments: ret_imm/4, or as many as the code reads (for ret 0)
  conv            'cdecl'  plain ret but stack arguments read: the caller pops them
                  'ecx'    ECX read before it is written: this in ECX (__thiscall), or
                           __fastcall
                  'stack'  otherwise (__stdcall, or no arguments)
  edx_arg         EDX read before written (a real second register argument)
  n_ptr_args      stack arguments that get dereferenced
  derefs_member_ptr  a pointer loaded from the `this` object is dereferenced
  clean_auto      self-contained, no pointer arguments, no member-pointer deref: the
                  class a random-input oracle can test without faulting

Sizes: VA:SIZE on the command line, or a 'size' column in the list (decimal, or hex
with 0x). Without one the size is guessed (pe_image.guess_size); check it.
"""
import argparse
import collections
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402

from capstone import CS_ARCH_X86, CS_MODE_32, Cs, x86  # noqa: E402

FIELDS = ["addr", "size", "size_guessed", "conv", "edx_arg", "method", "self_contained",
          "reads_global", "imm_image_ref", "has_call", "jumps_out", "uses_x87", "ret_imm",
          "stack_args", "n_ptr_args", "derefs_member_ptr", "auto_oracle", "clean_auto"]


def classify(img, va, code):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    a = dict(self_contained=True, reads_global=False, imm_image_ref=False, has_call=False,
             jumps_out=False, uses_x87=False, ret_imm=0, ptr_args=set(), ecx_read=False,
             edx_read=False, derefs_member_ptr=False, decoded=0, stack_args=0)
    lo, hi = va, va + len(code)
    arg_regs = {}             # register holding a stack argument -> its index
    this_regs = {"ecx"}       # registers holding a copy of the incoming ECX
    memberptr_regs = set()    # registers holding a pointer loaded from a this member
    written = set()
    # Stack tracking, to find the arguments of functions that do not pop them (ret 0):
    # delta = bytes pushed since entry, None when unknown (after a call, or in code reached
    # only from a branch whose delta was not seen); ebp_delta = delta at `mov ebp, esp`.
    state = dict(delta=0, ebp=None)
    at_target = {}

    def arg_index(base, disp):
        if base == "ebp" and state["ebp"] is not None:
            off = disp - state["ebp"] - 4
        elif base == "esp" and state["delta"] is not None:
            off = disp - state["delta"] - 4
        else:
            return None
        return off // 4 if 0 <= off < 0x100 and off % 4 == 0 else None

    for ins in md.disasm(code, va):
        a["decoded"] += 1
        m = ins.mnemonic
        ops = ins.op_str.replace(" ", "").split(",")
        if state["delta"] is None and ins.address in at_target:
            state["delta"] = at_target[ins.address]
        if m.startswith("f") or m == "wait":
            a["uses_x87"] = True
        if m == "ret" and ins.op_str.strip():
            try:
                a["ret_imm"] = int(ins.op_str.strip(), 0)
            except ValueError:
                pass
        if m == "call":
            a["has_call"] = True
        is_branch = m.startswith("j") or m.startswith("loop")
        if is_branch:
            for op in ins.operands:
                if op.type in (x86.X86_OP_REG, x86.X86_OP_MEM):
                    a["jumps_out"] = True
                elif op.type == x86.X86_OP_IMM:
                    if not (lo <= op.imm < hi):
                        a["jumps_out"] = True
                    elif state["delta"] is not None:
                        at_target.setdefault(op.imm, state["delta"])
        for op in ins.operands:
            if op.type == x86.X86_OP_MEM:
                mem = op.mem
                # A displacement inside the image is a global access whatever the base or
                # index registers are; a struct field offset never reaches the image base.
                if img.lo <= (mem.disp & 0xFFFFFFFF) < img.hi:
                    a["reads_global"] = True
                if mem.base != 0:
                    bname = ins.reg_name(mem.base)
                    k = arg_index(bname, mem.disp) if mem.index == 0 else None
                    if k is not None and m != "lea":
                        a["stack_args"] = max(a["stack_args"], k + 1)
                    if bname in arg_regs:
                        a["ptr_args"].add(arg_regs[bname])
                    if bname in memberptr_regs:
                        a["derefs_member_ptr"] = True
            elif op.type == x86.X86_OP_IMM and not is_branch and m != "call":
                if img.lo <= (op.imm & 0xFFFFFFFF) < img.hi:
                    a["imm_image_ref"] = True
        regs_read, regs_written = ins.regs_access()
        rd = {ins.reg_name(r) for r in regs_read}
        wr = {ins.reg_name(r) for r in regs_written}
        if m in ("xor", "sub") and len(ops) == 2 and ops[0] == ops[1]:
            rd.discard(ops[0])                     # xor ecx, ecx only writes ecx
        if m == "push" and ops[0] in ("ecx", "edx"):
            rd.discard(ops[0])                     # saved, or a 4-byte local reserved
        if rd & {"ecx", "cx", "cl", "ch"} and "ecx" not in written:
            a["ecx_read"] = True
        if rd & {"edx", "dx", "dl", "dh"} and "edx" not in written:
            a["edx_read"] = True
        for r, parts in (("ecx", {"ecx", "cx", "cl", "ch"}), ("edx", {"edx", "dx", "dl", "dh"})):
            if wr & parts:
                written.add(r)
        # follow stack arguments, the this pointer, and pointers loaded from members
        if m == "mov" and len(ins.operands) == 2:
            dst, src = ins.operands
            if dst.type == x86.X86_OP_REG:
                dn = ins.reg_name(dst.reg)
                role = None
                if src.type == x86.X86_OP_MEM:
                    sb = ins.reg_name(src.mem.base) if src.mem.base else None
                    k = arg_index(sb, src.mem.disp) if src.mem.index == 0 else None
                    if k is not None:
                        role = ("arg", k)
                    elif sb in this_regs and src.mem.index == 0:
                        role = ("memptr", None)
                elif src.type == x86.X86_OP_REG:
                    sn = ins.reg_name(src.reg)
                    if sn in this_regs:
                        role = ("this", None)
                    elif sn in memberptr_regs:
                        role = ("memptr", None)
                    if dn == "ebp" and sn == "esp":
                        state["ebp"] = state["delta"]
                arg_regs.pop(dn, None)
                this_regs.discard(dn)
                memberptr_regs.discard(dn)
                if role and role[0] == "arg":
                    arg_regs[dn] = role[1]
                elif role and role[0] == "memptr":
                    memberptr_regs.add(dn)
                elif role and role[0] == "this":
                    this_regs.add(dn)
        # the stack delta after this instruction
        d = state["delta"]
        if d is not None:
            if m == "push":
                d += 4
            elif m == "pop":
                d -= 4
            elif (m in ("sub", "add") and ops[0] == "esp"
                  and ins.operands[1].type == x86.X86_OP_IMM):
                d += ins.operands[1].imm if m == "sub" else -ins.operands[1].imm
            elif m == "mov" and ops == ["esp", "ebp"] and state["ebp"] is not None:
                d = state["ebp"]
            elif m == "call" or "esp" in wr:
                d = None                           # the callee may pop its arguments
        if m in ("ret", "jmp", "int3"):
            d = None
        state["delta"] = d
    a["self_contained"] = not (a["reads_global"] or a["imm_image_ref"] or a["has_call"]
                               or a["jumps_out"] or a["uses_x87"]) and a["decoded"] > 0
    a["stack_args"] = max(a["stack_args"], a["ret_imm"] // 4)
    # cl pops stack arguments in the callee for __stdcall / __thiscall / __fastcall and
    # leaves them to the caller only for __cdecl: a plain `ret` with arguments read from
    # the stack means __cdecl.
    if a["ret_imm"] == 0 and a["stack_args"] > 0:
        a["conv"] = "cdecl"
    elif a["ecx_read"]:
        a["conv"] = "ecx"
    else:
        a["conv"] = "stack"
    return a


def row_for(img, va, size, guessed):
    code = img.read(va, size)
    a = classify(img, va, code)
    auto = a["self_contained"] and not a["ptr_args"]
    clean = auto and not a["derefs_member_ptr"]
    return dict(addr=f"0x{va:08X}", size=size, size_guessed=int(guessed), conv=a["conv"],
                edx_arg=int(a["edx_read"]), method="A" if a["self_contained"] else "B",
                self_contained=int(a["self_contained"]), reads_global=int(a["reads_global"]),
                imm_image_ref=int(a["imm_image_ref"]), has_call=int(a["has_call"]),
                jumps_out=int(a["jumps_out"]), uses_x87=int(a["uses_x87"]),
                ret_imm=a["ret_imm"], stack_args=a["stack_args"], n_ptr_args=len(a["ptr_args"]),
                derefs_member_ptr=int(a["derefs_member_ptr"]), auto_oracle=int(auto),
                clean_auto=int(clean))


def read_list(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            addr = r.get("addr") or r.get("va") or r.get("address")
            if not addr:
                continue
            size = (r.get("size") or "").strip()
            rows.append((pe_image.parse_va(addr), int(size, 0) if size else None))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("functions", nargs="*", metavar="VA[:SIZE]")
    ap.add_argument("--list", help="TSV with an 'addr' (or 'va') column and optionally 'size'")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="binary to read (default: PR2_BASE_EXE)")
    ap.add_argument("--out", help="write the table here as TSV (default: print it)")
    a = ap.parse_args()

    img = pe_image.Image(kit_paths.need(a.exe, "binary", "PR2_BASE_EXE"))
    todo = []
    for f in a.functions:
        if ":" in f:
            va, size = pe_image.parse_target(f, with_obj=False)
        else:
            va, size = pe_image.parse_va(f), None
        todo.append((va, size))
    if a.list:
        todo += read_list(a.list)
    if not todo:
        ap.error("give functions or --list")

    out, stats = [], collections.Counter()
    for va, size in todo:
        if not img.is_code(va):
            print(f"skip 0x{va:08X}: not in an executable section", file=sys.stderr)
            continue
        guessed = size is None
        r = row_for(img, va, pe_image.guess_size(img, va) if guessed else size, guessed)
        out.append(r)
        stats["functions"] += 1
        stats[f"method_{r['method']}"] += 1
        stats["clean_auto"] += r["clean_auto"]

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, delimiter="\t", fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out)
        print(f"wrote {a.out} ({len(out)} rows)")
    else:
        w = csv.DictWriter(sys.stdout, delimiter="\t", fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print("  ".join(f"{k}={stats[k]}" for k in ("functions", "method_A", "method_B", "clean_auto")),
          file=sys.stderr)


if __name__ == "__main__":
    main()
