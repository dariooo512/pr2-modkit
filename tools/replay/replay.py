#!/usr/bin/env python3
"""Replay recorded cases inside a fresh, suspended game process and compare to the recording.

A from-scratch harness cannot own address 0x400000 with the engine DLLs loaded, so replay
borrows the real game: start the exe from the test install under the debugger, stop at the
exe entry point (loader done, imports resolved, the CRT not yet run), overlay the recorded
pages at their real addresses, set the recorded entry registers, put a SENTINEL return
address on the stack and run the function. Its `ret` faults on the sentinel; the debugger
reads the result and compares it to the recording. The game's own logic never runs.

    python replay.py selftest <session>              # original code vs its own recording
    python replay.py case <case.pkl> [--dll recon.dll]
    python replay.py recon <session> --dll-dir DIR   # each case whose DIR/recon_0x<VA>.dll exists

With a DLL (built by slice_src.py) the replayer LoadLibrary's it into the suspended game
first (while the heap and loader are still consistent), resolves the export whose name
contains fn<VA>, and runs that instead of the original.

Contract: the return value (eax, masked to --ret-width; edx:eax for 64; 0 = void, not
checked) plus deterministic writes to the pages of the object (ecx) and of pointer arguments.
Writes to globals, the allocator and anything else are NOT compared, and a differing word
that is only an allocation-dependent pointer or debug-CRT fill counts as noise.

Verdicts: PASS, FAIL-MISMATCH, NEEDS-MODEL (faulted, exited, or the capture was missing
memory), ERROR. A self-test that does not PASS means the case cannot judge a rebuild.
Promote nothing on fewer than 10 passing cases.
"""
import argparse
import os
import pickle
import struct
import ctypes as C
from pathlib import Path

import common as CM
import recorder as R

k = R.k
SENTINEL = 0xC0FFEE00          # kernel-space address: a ret to it always faults
SENTINEL2 = 0xC0FFEE10         # return marker for the in-child LoadLibraryA call
OBJ_SPAN = 0x4000              # bytes from an object/out-param pointer treated as its region
DBG = bool(os.environ.get("REPLAY_DBG"))

R_OFF = dict(Edi=156, Esi=160, Ebx=164, Edx=168, Ecx=172, Eax=176,
             Ebp=180, Eip=184, EFlags=192, Esp=196)


def _reg(ctx_bytes, name):
    return struct.unpack_from("<I", ctx_bytes, R_OFF[name])[0]


def overlay_page(h, addr, data):
    n = len(data)
    rwx = R.PAGE_EXECUTE_READWRITE
    # reserve the whole 64K block first (a reserve base is rounded down to 64K), then commit
    # the exact page; both are no-ops when already done
    k.VirtualAllocEx(h, C.c_void_p(addr & ~0xFFFF), 0x10000, R.MEM_RESERVE, rwx)
    k.VirtualAllocEx(h, C.c_void_p(addr), n, R.MEM_COMMIT, rwx)
    R.vprotect_ex(h, addr, n)
    R.wpm(h, addr, data)
    return R.rpm(h, addr, n) == data


def load_case(path):
    """Load a case; a full-mode case is rebuilt from the session keyframe plus its delta."""
    with open(path, "rb") as f:
        case = pickle.load(f)
    if "entry_pages" not in case and "entry_delta" in case:
        with open(Path(path).parent / "_keyframe.pkl", "rb") as f:
            kf = pickle.load(f)
        kf.update(case["entry_delta"])
        case["entry_pages"] = kf
    return case


def replay_case(exe, case, recon_dll=None, ret_width=32, timeout_ms=20000):
    pi = R.launch(exe)
    entry_ep = R.IMG["entry"]
    de = R.DEBUG_EVENT()
    hproc = None
    threads = {}
    modules = []
    ep_bp = {}
    hijacked = False
    target = case["va"]
    verdict = ("ERROR", "no result")
    pre = {}
    ev = 0
    while ev < 200000:
        ev += 1
        if not k.WaitForDebugEvent(C.byref(de), timeout_ms):
            verdict = ("ERROR", "debug timeout")
            break
        code, tid, status = de.dwDebugEventCode, de.dwThreadId, R.DBG_CONTINUE
        if code == R.CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            hproc = cpi.hProcess
            threads[tid] = cpi.hThread
            modules.append(cpi.lpBaseOfImage)
            R.set_int3(hproc, entry_ep, ep_bp)
        elif code == R.LOAD_DLL_DEBUG_EVENT:
            modules.append(de.u.LoadDll.lpBaseOfDll)
        elif code == R.CREATE_THREAD_DEBUG_EVENT:
            threads[tid] = de.u.CreateThread.hThread
        elif code == R.EXIT_THREAD_DEBUG_EVENT:
            threads.pop(tid, None)
        elif code == R.EXIT_PROCESS_DEBUG_EVENT:
            verdict = ("NEEDS-MODEL", "process exited during replay")
            k.ContinueDebugEvent(de.dwProcessId, tid, R.DBG_CONTINUE)
            break
        elif code == R.EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            hT = threads.get(tid)
            if not hijacked and xcode in R.BP_CODES and addr == entry_ep and hT:
                R.clear_int3(hproc, entry_ep, ep_bp)
                # 1. load the recon DLL first, while heap and loader state are still live
                if recon_dll:
                    fp = _inject_recon(hproc, hT, de, modules, recon_dll, case["va"])
                    tid = de.dwThreadId          # de now holds the SENTINEL2 fault event
                    hT = threads.get(tid)
                    if fp is None:
                        verdict = ("NEEDS-MODEL", "recon injection/resolve failed")
                        k.ContinueDebugEvent(de.dwProcessId, tid, status)
                        break
                    target = fp
                # 2. freeze every other thread so only the target runs
                for t, hh in threads.items():
                    if t != tid:
                        k.SuspendThread(hh)
                # 3. overlay the recorded memory at its real addresses
                fails = [a for a, b in case["entry_pages"].items() if not overlay_page(hproc, a, b)]
                esp = _reg(case["entry_ctx"], "Esp")
                R.wpm(hproc, esp, struct.pack("<I", SENTINEL))
                # the ACTUAL pre-state, so overlay imperfections cancel out: only the call's
                # own change is judged against the recorded one
                pre = {a: R.rpm(hproc, a, len(b)) for a, b in case["entry_pages"].items()}
                if DBG:
                    print("[dbg] overlay fails=%d/%d" % (len(fails), len(case["entry_pages"])))
                ctx = R.WOW64_CONTEXT.from_buffer_copy(case["entry_ctx"])
                ctx.ContextFlags = R.WOW64_CONTEXT_FULLREGS
                ctx.Eip = target
                R.set_ctx(hT, ctx)
                hijacked = True
            elif hijacked and xcode == R.EXCEPTION_ACCESS_VIOLATION and addr == SENTINEL and hT:
                verdict = _compare(hproc, case, R.get_ctx(hT), pre, ret_width)
                break
            elif hijacked and xcode == R.EXCEPTION_ACCESS_VIOLATION:
                info1 = er.ExceptionInformation[1] if er.NumberParameters >= 2 else 0
                verdict = ("NEEDS-MODEL", "fault eip=0x%08X addr=0x%X (capture insufficient)"
                           % (addr, (info1 or 0) & 0xFFFFFFFF))
                break
            elif xcode not in R.BP_CODES:
                status = R.DBG_EXCEPTION_NOT_HANDLED
        k.ContinueDebugEvent(de.dwProcessId, tid, status)
    R._finish(pi)
    return verdict


def _roi_pages(case):
    """The pages of the object (ecx/edx) and of any pointer-looking stack argument."""
    ec = case["entry_ctx"]
    esp = _reg(ec, "Esp")
    anchors = [_reg(ec, "Ecx"), _reg(ec, "Edx")]
    ep = case["entry_pages"]
    for i in range(1, 8):
        a = esp + 4 * i
        pg = ep.get(a & ~0xFFF)
        if pg and (a & 0xFFF) <= len(pg) - 4:
            anchors.append(struct.unpack_from("<I", pg, a & 0xFFF)[0])
    roi = set()
    for v in anchors:
        if 0x10000 <= v < 0x7FFF0000 and (v & ~0xFFF) in ep:
            for p in range(v & ~0xFFF, v + OBJ_SPAN, 0x1000):
                if p in ep:
                    roi.add(p)
    return roi


def _plausible_ptr(v):
    return 0x10000 <= v < 0x7FFF0000


# MSVC debug-CRT fill words (uninitialised, no-man's-land, freed): never a behavioural output
_DEBUG_FILL = {0xFEEEFEEE, 0xCDCDCDCD, 0xBAADF00D, 0xABABABAB, 0xFDFDFDFD, 0xDDDDDDDD, 0xCCCCCCCC}


def _alloc_divergence(post_pg, oracle_pg, entry_pages):
    """True iff every differing word is noise: a fresh-allocation pointer on the replay side
    (into a page not captured at entry) against a pointer on the recorded side, or debug
    fill on either side."""
    if oracle_pg is None or len(post_pg) != 0x1000 or len(oracle_pg) != 0x1000:
        return False
    words = sorted({i & ~3 for i in range(0x1000) if post_pg[i] != oracle_pg[i]})
    if not words:
        return False
    for w in words:
        pv = int.from_bytes(post_pg[w:w + 4], "little")
        ov = int.from_bytes(oracle_pg[w:w + 4], "little")
        if pv in _DEBUG_FILL or ov in _DEBUG_FILL:
            continue
        if _plausible_ptr(pv) and _plausible_ptr(ov) and (pv & ~0xFFF) not in entry_pages:
            continue
        return False
    return True


def _compare(hproc, case, ctx, pre, ret_width):
    ws = case["write_set"]
    entry_pages = case["entry_pages"]
    # the active stack frame is not output (we overwrote the return slot; locals are transient)
    esp = _reg(case["entry_ctx"], "Esp")
    slo, shi = (esp - 0x20000) & ~0xFFF, (esp + 0x2000) & ~0xFFF
    roi = {a for a in _roi_pages(case) if not (slo <= a <= shi)}
    post = {a: R.rpm(hproc, a, 0x1000) for a in roi}
    wmis, alloc_nd = [], 0
    for a in roi:
        if post[a] == pre.get(a):
            continue
        if any(R.rpm(hproc, a, 0x1000) != post[a] for _ in range(4)):
            continue                         # volatile (hardware-updated) -> not output
        if post[a] != ws.get(a, pre.get(a)):
            if _alloc_divergence(post[a], ws.get(a), entry_pages):
                alloc_nd += 1
                continue
            wmis.append(a)
    if not ret_width:
        eax_ok, ret_str = True, "void"
    else:
        mask = (1 << min(ret_width, 32)) - 1
        got = (ctx.Eax & mask) if ctx else 0
        want = _reg(case["exit_ctx"], "Eax") & mask
        eax_ok = ctx is not None and got == want
        if ret_width == 64 and eax_ok:
            eax_ok = ctx.Edx == _reg(case["exit_ctx"], "Edx")
        if (not eax_ok and ret_width == 32 and _plausible_ptr(got) and _plausible_ptr(want)
                and (got & ~0xFFF) not in entry_pages and (want & ~0xFFF) in entry_pages):
            eax_ok = True
            ret_str = "eax32 alloc-nondet(got 0x%X want 0x%X)" % (got, want)
        else:
            ret_str = "eax%d %s(got 0x%X want 0x%X)" % (ret_width, "ok" if eax_ok else "BAD", got, want)
    nd = (" alloc_nd=%d" % alloc_nd) if alloc_nd else ""
    if eax_ok and not wmis:
        return ("PASS", "%s obj_pages=%d%s ok" % (ret_str, len(roi), nd))
    return ("FAIL-MISMATCH", "%s obj_mismatch=%d e.g.=%s%s"
            % (ret_str, len(wmis), ["0x%08X" % x for x in wmis[:5]], nd))


# --- recon injection ----------------------------------------------------------------------
def _rcstr(h, addr, maxlen=512):
    out = b""
    while len(out) < maxlen:
        chunk = R.rpm(h, addr + len(out), 32)
        if not chunk:
            break
        i = chunk.find(b"\x00")
        if i >= 0:
            return out + chunk[:i]
        out += chunk
    return out


def _u32r(h, a):
    b = R.rpm(h, a, 4)
    return int.from_bytes(b, "little") if len(b) == 4 else None


def remote_export(h, base, name=None, substr=None):
    """Address of the export of module `base` named `name`, or whose name contains `substr`
    (read from the child's memory: a 32-bit LoadLibraryA can't be resolved from 64-bit Python)."""
    base = base & 0xFFFFFFFF if base else 0
    if not base or R.rpm(h, base, 2) != b"MZ":
        return None
    pe = base + (_u32r(h, base + 0x3C) or 0)
    if R.rpm(h, pe, 4) != b"PE\x00\x00":
        return None
    exp_rva = _u32r(h, pe + 24 + 96)
    if not exp_rva:
        return None
    exp = base + exp_rva
    nnames = _u32r(h, exp + 0x18) or 0
    if not (0 < nnames <= 100000):
        return None
    af = base + (_u32r(h, exp + 0x1C) or 0)
    an = base + (_u32r(h, exp + 0x20) or 0)
    ao = base + (_u32r(h, exp + 0x24) or 0)
    names = R.rpm(h, an, 4 * nnames)
    for i in range(min(nnames, len(names) // 4)):
        s = _rcstr(h, base + int.from_bytes(names[4 * i:4 * i + 4], "little")).decode("ascii", "ignore")
        if (name and s == name) or (substr and substr in s):
            ordl = int.from_bytes(R.rpm(h, ao + 2 * i, 2), "little")
            return base + (_u32r(h, af + 4 * ordl) or 0)
    return None


def _pump_until_sentinel(hproc, hT, de, modules, sentinel, max_ev=200000):
    """Continue the pending event and pump until the main thread faults on `sentinel`;
    return eax there (the event is left pending for the caller). New threads are frozen."""
    k.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, R.DBG_CONTINUE)
    for _ in range(max_ev):
        if not k.WaitForDebugEvent(C.byref(de), 20000):
            return None
        code, tid, st = de.dwDebugEventCode, de.dwThreadId, R.DBG_CONTINUE
        if code == R.LOAD_DLL_DEBUG_EVENT:
            modules.append(de.u.LoadDll.lpBaseOfDll)
        elif code == R.CREATE_THREAD_DEBUG_EVENT:
            k.SuspendThread(de.u.CreateThread.hThread)
        elif code == R.EXIT_PROCESS_DEBUG_EVENT:
            return None
        elif code == R.EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            if xcode == R.EXCEPTION_ACCESS_VIOLATION and addr == sentinel:
                ctx = R.get_ctx(hT)
                return (ctx.Eax & 0xFFFFFFFF) if ctx else None
            if xcode not in R.BP_CODES and xcode not in R.SS_CODES:
                st = R.DBG_EXCEPTION_NOT_HANDLED
        k.ContinueDebugEvent(de.dwProcessId, tid, st)
    return None


def _inject_recon(hproc, hT, de, modules, recon_dll, va):
    """Drive the stopped main thread to call the child's own LoadLibraryA(recon_dll) with a
    SENTINEL2 return, then resolve the export containing fn<VA>. -> its address or None."""
    llA = next((a for a in (remote_export(hproc, b, name="LoadLibraryA") for b in modules) if a), None)
    if not llA:
        return None
    path = str(Path(recon_dll).resolve()).encode("mbcs", "ignore") + b"\x00"
    scratch = R.valloc_ex(hproc, 0, len(path) + 16)
    if not scratch:
        return None
    R.wpm(hproc, scratch, path)
    save = R.get_ctx(hT)
    if save is None:
        return None
    sp = (save.Esp - 0x800) & ~0xF
    R.wpm(hproc, sp, struct.pack("<II", SENTINEL2, scratch))
    cc = R.WOW64_CONTEXT.from_buffer_copy(bytes(save))
    cc.ContextFlags = R.WOW64_CONTEXT_FULLREGS
    cc.Eip = llA & 0xFFFFFFFF
    cc.Esp = sp
    R.set_ctx(hT, cc)
    hmod = _pump_until_sentinel(hproc, hT, de, modules, SENTINEL2)
    if DBG:
        print("[dbg] LoadLibraryA -> 0x%X" % (hmod or 0))
    if not hmod:
        return None
    return remote_export(hproc, hmod, substr="fn" + CM.norm_va(va))


# --- command line ---------------------------------------------------------------------------
def _widths(a):
    table = {}
    if a.ret_widths:
        for va, r in CM.load_targets(a.ret_widths)[1].items():
            w = r.get("ret_width", "").strip()
            if w.isdigit():
                table[va] = int(w)
    return lambda va: table.get(va, a.ret_width)


def run_session(exe, session, width, dll_dir=None):
    cases = sorted(Path(session).glob("case_*.pkl"))
    if not cases:
        raise SystemExit(f"no cases in {session}")
    tally = {}
    for cp in cases:
        case = load_case(cp)
        dll = None
        if dll_dir:
            dll = Path(dll_dir) / ("recon_0x%s.dll" % CM.norm_va(case["va"]))
            if not dll.exists():
                continue
        tag, detail = replay_case(exe, case, dll, width(case["va"]))
        tally[tag] = tally.get(tag, 0) + 1
        print("[%-13s] 0x%08X  %s  (%s)" % (tag, case["va"], detail, cp.name), flush=True)
    print("\n%s: %s" % ("recon" if dll_dir else "self-test",
                        ", ".join("%s=%d" % kv for kv in sorted(tally.items())) or "no case had a DLL"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_args(p):
        p.add_argument("--exe", help="the game exe inside the test install "
                                     "(default PR2_TEST_EXE or <PR2_TEST_ENV>/PR2_target.exe); "
                                     "must be the exe the cases were recorded with")
        p.add_argument("--wait", action="store_true", help="wait for the test-install lock")
        p.add_argument("--ret-width", type=int, default=32, choices=(0, 8, 16, 32, 64),
                       help="bits of eax that are the return value (0 = void; default 32)")
        p.add_argument("--ret-widths", help="TSV with addr and ret_width columns, per function")

    p = sub.add_parser("selftest", help="run the ORIGINAL code against every case of a session")
    common_args(p)
    p.add_argument("session")
    p = sub.add_parser("case", help="replay one case (original code, or --dll)")
    common_args(p)
    p.add_argument("case")
    p.add_argument("--dll", help="recon DLL exporting fn<VA> (from slice_src.py)")
    p = sub.add_parser("recon", help="replay a session against recon DLLs")
    common_args(p)
    p.add_argument("session")
    p.add_argument("--dll-dir", default=str(CM.OUT / "recon"),
                   help="folder of recon_0x<VA>.dll files (default _build/replay/recon)")
    a = ap.parse_args()
    exe = CM.game_exe(a.exe)
    width = _widths(a)
    k.DebugSetProcessKillOnExit(True)
    with CM.test_lock(f"replay.py {a.cmd}", wait=a.wait):
        if a.cmd == "selftest":
            run_session(exe, a.session, width)
        elif a.cmd == "recon":
            run_session(exe, a.session, width, dll_dir=a.dll_dir)
        else:
            case = load_case(a.case)
            tag, detail = replay_case(exe, case, a.dll, width(case["va"]))
            print(tag, "-", detail)


if __name__ == "__main__":
    main()
