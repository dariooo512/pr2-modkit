#!/usr/bin/env python3
"""Record real calls of game functions from a running game (Win32 debug API, WOW64).

Starts the game from the test install under a debugger, puts an INT3 on the entry of every
target function and, per call, captures the entry registers, the memory the call can see and
the pages it changed (its write set). replay.py re-runs those cases later.

The most useful mode is the DIFFERENTIAL CAPTURE, which finds the code one action runs:

    python functions.py                                  # 1. a target list (once)
    python recorder.py census --targets _build/replay/functions.tsv
        # 2. BASELINE: play to the main menu (or wherever your action starts), do nothing
        #    else, quit the game. Every function that fired is written to baseline.tsv.
    python recorder.py record-new --targets _build/replay/functions.tsv
        # 3. ACTION: same start, now do the one thing (buy a ship, open a screen...),
        #    then quit. Only targets the baseline never ran are armed, so everything that
        #    fires belongs to your action -> session_<pid>/new_targets.tsv (+ state cases).

Other commands:
    probe        start the game, stop at the loader breakpoint (checks the debugger works)
    trace        log the first N distinct targets hit, in call order (no capture)
    record       capture cases for given VAs or a target list
    new-targets  rebuild new_targets.tsv for an existing session

Every command that starts the game takes the shared test-install lock, starts the exe given
by --exe (default PR2_TEST_EXE or <PR2_TEST_ENV>/PR2_target.exe; never a Steam folder) with
its window not activated, and writes below <PR2_BUILD>/replay/.

Limits: a full session corpus grows huge (tens of GB for a long create-game run; light
capture, the default for record-new, keeps it to MBs per case). Sibling threads are frozen
during a captured call, so the game runs slowly while a hot function is armed; every target
disarms after --per-fn cases.
"""
import argparse
import collections
import copy
import csv
import ctypes as C
import glob
import json
import pickle
import time
from ctypes import wintypes as W
from pathlib import Path

import common as CM

k = C.WinDLL("kernel32", use_last_error=True)
HANDLE = C.c_void_p

# --- creation / continue / event constants ---------------------------------
DEBUG_ONLY_THIS_PROCESS = 0x00000002
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
STARTF_USESHOWWINDOW = 0x1
SW_SHOWNOACTIVATE = 4

EXCEPTION_DEBUG_EVENT = 1
CREATE_THREAD_DEBUG_EVENT = 2
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_THREAD_DEBUG_EVENT = 4
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
UNLOAD_DLL_DEBUG_EVENT = 7
OUTPUT_DEBUG_STRING_EVENT = 8
RIP_EVENT = 9
_EVNAME = {1: "EXCEPTION", 2: "CREATE_THREAD", 3: "CREATE_PROCESS", 4: "EXIT_THREAD",
           5: "EXIT_PROCESS", 6: "LOAD_DLL", 7: "UNLOAD_DLL", 8: "OUTPUT_DEBUG_STRING", 9: "RIP"}

EXCEPTION_BREAKPOINT = 0x80000003
EXCEPTION_SINGLE_STEP = 0x80000004
EXCEPTION_ACCESS_VIOLATION = 0xC0000005
# WOW64: a 32-bit int3 / single-step can surface under the x86 status codes. Accept both.
STATUS_WX86_BREAKPOINT = 0x4000001F
STATUS_WX86_SINGLE_STEP = 0x4000001E
BP_CODES = (EXCEPTION_BREAKPOINT, STATUS_WX86_BREAKPOINT)
SS_CODES = (EXCEPTION_SINGLE_STEP, STATUS_WX86_SINGLE_STEP)

TF = 0x100  # EFlags trap flag

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_IMAGE = 0x1000000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_EXECUTE_READWRITE = 0x40
WRITABLE_PROT = (0x04, 0x08, 0x40, 0x80)  # RW, WC, ERW, EWC
PAGESZ = 0x1000
KUSER_SHARED_DATA = 0x7FFE0000            # kernel-updated clock page: never a function output

WOW64_CONTEXT_i386 = 0x00010000
WOW64_CONTEXT_FULLREGS = WOW64_CONTEXT_i386 | 0x01 | 0x02 | 0x04 | 0x08 | 0x20

# The image range of the running exe; set by launch() from its PE header.
IMG = {"lo": 0x400000, "hi": 0xA00000, "entry": 0}


# --- structures ------------------------------------------------------------
class STARTUPINFO(C.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", C.c_wchar_p),
                ("lpDesktop", C.c_wchar_p), ("lpTitle", C.c_wchar_p),
                ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD),
                ("dwYSize", W.DWORD), ("dwXCountChars", W.DWORD),
                ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD),
                ("cbReserved2", W.WORD), ("lpReserved2", C.c_void_p),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE),
                ("hStdError", HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


class EXCEPTION_RECORD(C.Structure):
    _fields_ = [("ExceptionCode", W.DWORD), ("ExceptionFlags", W.DWORD),
                ("ExceptionRecord", C.c_void_p), ("ExceptionAddress", C.c_void_p),
                ("NumberParameters", W.DWORD), ("ExceptionInformation", C.c_void_p * 15)]


class EXCEPTION_DEBUG_INFO(C.Structure):
    _fields_ = [("ExceptionRecord", EXCEPTION_RECORD), ("dwFirstChance", W.DWORD)]


class CREATE_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [("hFile", HANDLE), ("hProcess", HANDLE), ("hThread", HANDLE),
                ("lpBaseOfImage", C.c_void_p), ("dwDebugInfoFileOffset", W.DWORD),
                ("nDebugInfoSize", W.DWORD), ("lpThreadLocalBase", C.c_void_p),
                ("lpStartAddress", C.c_void_p), ("lpImageName", C.c_void_p),
                ("fUnicode", W.WORD)]


class CREATE_THREAD_DEBUG_INFO(C.Structure):
    _fields_ = [("hThread", HANDLE), ("lpThreadLocalBase", C.c_void_p),
                ("lpStartAddress", C.c_void_p)]


class EXIT_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [("dwExitCode", W.DWORD)]


class LOAD_DLL_DEBUG_INFO(C.Structure):
    _fields_ = [("hFile", HANDLE), ("lpBaseOfDll", C.c_void_p),
                ("dwDebugInfoFileOffset", W.DWORD), ("nDebugInfoSize", W.DWORD),
                ("lpImageName", C.c_void_p), ("fUnicode", W.WORD)]


class _DEU(C.Union):
    _fields_ = [("Exception", EXCEPTION_DEBUG_INFO),
                ("CreateThread", CREATE_THREAD_DEBUG_INFO),
                ("CreateProcessInfo", CREATE_PROCESS_DEBUG_INFO),
                ("ExitProcess", EXIT_PROCESS_DEBUG_INFO),
                ("LoadDll", LOAD_DLL_DEBUG_INFO),
                ("_pad", C.c_byte * 256)]


class DEBUG_EVENT(C.Structure):
    _fields_ = [("dwDebugEventCode", W.DWORD), ("dwProcessId", W.DWORD),
                ("dwThreadId", W.DWORD), ("u", _DEU)]


class FLOATING_SAVE_AREA(C.Structure):
    _fields_ = [("ControlWord", W.DWORD), ("StatusWord", W.DWORD),
                ("TagWord", W.DWORD), ("ErrorOffset", W.DWORD),
                ("ErrorSelector", W.DWORD), ("DataOffset", W.DWORD),
                ("DataSelector", W.DWORD), ("RegisterArea", C.c_byte * 80),
                ("Cr0NpxState", W.DWORD)]


class WOW64_CONTEXT(C.Structure):
    _fields_ = [("ContextFlags", W.DWORD),
                ("Dr0", W.DWORD), ("Dr1", W.DWORD), ("Dr2", W.DWORD),
                ("Dr3", W.DWORD), ("Dr6", W.DWORD), ("Dr7", W.DWORD),
                ("FloatSave", FLOATING_SAVE_AREA),
                ("SegGs", W.DWORD), ("SegFs", W.DWORD), ("SegEs", W.DWORD),
                ("SegDs", W.DWORD),
                ("Edi", W.DWORD), ("Esi", W.DWORD), ("Ebx", W.DWORD),
                ("Edx", W.DWORD), ("Ecx", W.DWORD), ("Eax", W.DWORD),
                ("Ebp", W.DWORD), ("Eip", W.DWORD), ("SegCs", W.DWORD),
                ("EFlags", W.DWORD), ("Esp", W.DWORD), ("SegSs", W.DWORD),
                ("ExtendedRegisters", C.c_byte * 512)]


class MBI(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("__a", W.DWORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD), ("__b", W.DWORD)]


# --- prototypes ------------------------------------------------------------
k.CreateProcessW.argtypes = [C.c_wchar_p, C.c_wchar_p, C.c_void_p, C.c_void_p,
                             W.BOOL, W.DWORD, C.c_void_p, C.c_wchar_p,
                             C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION)]
k.CreateProcessW.restype = W.BOOL
k.WaitForDebugEvent.argtypes = [C.POINTER(DEBUG_EVENT), W.DWORD]
k.WaitForDebugEvent.restype = W.BOOL
k.ContinueDebugEvent.argtypes = [W.DWORD, W.DWORD, W.DWORD]
k.ContinueDebugEvent.restype = W.BOOL
k.TerminateProcess.argtypes = [HANDLE, W.UINT]
k.TerminateProcess.restype = W.BOOL
k.CloseHandle.argtypes = [HANDLE]
k.DebugSetProcessKillOnExit.argtypes = [W.BOOL]
k.DebugSetProcessKillOnExit.restype = W.BOOL
k.SuspendThread.argtypes = [HANDLE]
k.SuspendThread.restype = W.DWORD
k.ResumeThread.argtypes = [HANDLE]
k.ResumeThread.restype = W.DWORD
k.Wow64GetThreadContext.argtypes = [HANDLE, C.POINTER(WOW64_CONTEXT)]
k.Wow64GetThreadContext.restype = W.BOOL
k.Wow64SetThreadContext.argtypes = [HANDLE, C.POINTER(WOW64_CONTEXT)]
k.Wow64SetThreadContext.restype = W.BOOL
k.ReadProcessMemory.argtypes = [HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k.ReadProcessMemory.restype = W.BOOL
k.WriteProcessMemory.argtypes = [HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k.WriteProcessMemory.restype = W.BOOL
k.VirtualQueryEx.argtypes = [HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
k.VirtualQueryEx.restype = C.c_size_t
k.FlushInstructionCache.argtypes = [HANDLE, C.c_void_p, C.c_size_t]
k.FlushInstructionCache.restype = W.BOOL
k.VirtualAllocEx.argtypes = [HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]
k.VirtualAllocEx.restype = C.c_void_p
k.VirtualProtectEx.argtypes = [HANDLE, C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)]
k.VirtualProtectEx.restype = W.BOOL


def valloc_ex(h, addr, size):
    return k.VirtualAllocEx(h, C.c_void_p(addr), size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)


def vprotect_ex(h, addr, size, prot=PAGE_EXECUTE_READWRITE):
    old = W.DWORD()
    return bool(k.VirtualProtectEx(h, C.c_void_p(addr), size, prot, C.byref(old)))


# --- raw memory helpers ----------------------------------------------------
def rpm(h, addr, n):
    buf = (C.c_char * n)()
    got = C.c_size_t()
    if k.ReadProcessMemory(h, C.c_void_p(addr), buf, n, C.byref(got)) and got.value:
        return bytes(buf[:got.value])
    return b""


def wpm(h, addr, data):
    n = C.c_size_t()
    ok = k.WriteProcessMemory(h, C.c_void_p(addr), data, len(data), C.byref(n))
    if ok:
        k.FlushInstructionCache(h, C.c_void_p(addr), len(data))
    return bool(ok)


def read_byte(h, addr):
    b = rpm(h, addr, 1)
    return b[0] if b else None


def read_u32(h, addr):
    b = rpm(h, addr, 4)
    return int.from_bytes(b, "little") if len(b) == 4 else None


def set_int3(h, addr, saved):
    """Write 0xCC at addr, recording the original byte into dict `saved`."""
    if addr in saved:
        return
    ob = read_byte(h, addr)
    if ob is None:
        return
    saved[addr] = ob
    wpm(h, addr, b"\xCC")


def clear_int3(h, addr, saved):
    if addr in saved:
        wpm(h, addr, bytes([saved.pop(addr)]))


def enum_committed(h, cap_region=256 * 1024 * 1024):
    """Yield (base, size, prot, type) for committed, readable, non-guard regions."""
    addr = 0
    mbi = MBI()
    while addr < 0x7FFF0000:
        if not k.VirtualQueryEx(h, C.c_void_p(addr), C.byref(mbi), C.sizeof(mbi)):
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        prot = mbi.Protect
        if (mbi.State == MEM_COMMIT and prot != PAGE_NOACCESS
                and not (prot & PAGE_GUARD) and size <= cap_region):
            yield base, size, prot, mbi.Type
        addr = base + size
        if addr == 0:
            break


def snapshot(h):
    """Full capture: every committed writable page plus read-only NON-image pages. Read-only
    image pages (code, .rdata) are skipped: the replay process maps the same binaries.
    -> {page_base: 4K bytes}. Hundreds of MB per snapshot."""
    pages = {}
    for base, size, prot, typ in enum_committed(h):
        if base == KUSER_SHARED_DATA:
            continue
        if prot not in WRITABLE_PROT and typ == MEM_IMAGE:
            continue
        data = rpm(h, base, size)
        for off in range(0, len(data), PAGESZ):
            pages[base + off] = data[off:off + PAGESZ]
    return pages


ROI_BUDGET = 1024       # max pointer-closure pages a light_snap captures (4 MB); the image's
                        # writable pages and the stack window come on top of it


def light_snap(h, ctx, budget=ROI_BUDGET):
    """Bounded capture: the exe's writable pages (its globals), a stack window around esp,
    and a pointer closure from ecx/edx and the stack arguments (the object graph the call
    navigates), bounded to `budget` pages. Misses heap the call never points at.
    -> {page_base: 4K bytes}."""
    pages = {}
    for base, size, prot, typ in enum_committed(h, cap_region=2 * 1024 * 1024):
        if base == KUSER_SHARED_DATA:
            continue
        if prot in WRITABLE_PROT and IMG["lo"] <= base < IMG["hi"]:
            data = rpm(h, base, size)
            for off in range(0, len(data), PAGESZ):
                pages[base + off] = data[off:off + PAGESZ]
    esp = ctx.Esp
    for p in range(esp & ~0xFFF, esp + 0x2000, PAGESZ):
        if p not in pages:
            d = rpm(h, p, PAGESZ)
            if d:
                pages[p] = d
    queue = collections.deque([ctx.Ecx & 0xFFFFFFFF, ctx.Edx & 0xFFFFFFFF])
    sd = rpm(h, esp, 0x80) or b""
    for i in range(0, len(sd) - 3, 4):
        queue.append(int.from_bytes(sd[i:i + 4], "little"))
    seen = set()
    closure_n = 0
    while queue and closure_n < budget:
        v = queue.popleft()
        if v in seen or not (0x10000 <= v < 0x7FFF0000):
            continue
        seen.add(v)
        pg = v & ~0xFFF
        if pg in pages:
            continue
        d = rpm(h, pg, PAGESZ)
        if not d:
            continue
        pages[pg] = d
        closure_n += 1
        nxt = pg + PAGESZ                    # an object spanning the boundary stays whole
        if nxt not in pages:
            dn = rpm(h, nxt, PAGESZ)
            if dn:
                pages[nxt] = dn
        for i in range(0, len(d) - 3, 4):
            w = int.from_bytes(d[i:i + 4], "little")
            if 0x10000 <= w < 0x7FFF0000:
                queue.append(w)
    return pages


def diff_pages(old, new):
    """{page: bytes} for pages that are new or changed from old -> new."""
    return {p: b for p, b in new.items() if old.get(p) != b}


def launch(exe):
    """Start `exe` (already checked by common.game_exe) under DEBUG_ONLY_THIS_PROCESS from its
    own folder, its window shown without activation. Returns PROCESS_INFORMATION."""
    base, size, entry, _ = CM.image_layout(exe)
    IMG.update(lo=base, hi=base + size, entry=entry)
    si = STARTUPINFO()
    si.cb = C.sizeof(si)
    si.dwFlags, si.wShowWindow = STARTF_USESHOWWINDOW, SW_SHOWNOACTIVATE
    pi = PROCESS_INFORMATION()
    if not k.CreateProcessW(str(exe), None, None, None, False, DEBUG_ONLY_THIS_PROCESS,
                            None, str(exe.parent), C.byref(si), C.byref(pi)):
        raise OSError("CreateProcessW failed err=%d" % C.get_last_error())
    return pi


def get_ctx(hT):
    ctx = WOW64_CONTEXT()
    ctx.ContextFlags = WOW64_CONTEXT_FULLREGS
    return ctx if k.Wow64GetThreadContext(hT, C.byref(ctx)) else None


def set_ctx(hT, ctx):
    return bool(k.Wow64SetThreadContext(hT, C.byref(ctx)))


def _finish(pi):
    try:
        k.TerminateProcess(pi.hProcess, 0)
        k.CloseHandle(pi.hThread)
        k.CloseHandle(pi.hProcess)
    except Exception:
        pass


# --- trace -------------------------------------------------------------------------------
def trace(exe, targets, max_hits=40, idle_timeouts=3):
    """Log the first `max_hits` distinct targets hit, in call order. No capture."""
    k.DebugSetProcessKillOnExit(True)
    pi = launch(exe)
    print("launched pid=%d  instrumenting %d targets (trace)" % (pi.dwProcessId, len(targets)), flush=True)
    hproc, threads, entry_bps, rearm, hits = None, {}, {}, {}, []
    seen = collections.Counter()
    de = DEBUG_EVENT()
    timeouts = 0
    while len(hits) < max_hits and timeouts < idle_timeouts:
        if not k.WaitForDebugEvent(C.byref(de), 8000):
            timeouts += 1
            print("  (idle %d/%d)" % (timeouts, idle_timeouts), flush=True)
            continue
        timeouts = 0
        code, tid, status = de.dwDebugEventCode, de.dwThreadId, DBG_CONTINUE
        if code == CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            hproc = cpi.hProcess
            threads[tid] = cpi.hThread
            for va in targets:
                set_int3(hproc, va, entry_bps)
            print("armed %d entry bps" % len(entry_bps), flush=True)
        elif code == CREATE_THREAD_DEBUG_EVENT:
            threads[tid] = de.u.CreateThread.hThread
        elif code == EXIT_THREAD_DEBUG_EVENT:
            threads.pop(tid, None)
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("process exited code=%d" % de.u.ExitProcess.dwExitCode, flush=True)
            k.ContinueDebugEvent(de.dwProcessId, tid, DBG_CONTINUE)
            break
        elif code == EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            hT = threads.get(tid)
            if xcode in SS_CODES:
                if tid in rearm and hT:
                    wpm(hproc, rearm.pop(tid), b"\xCC")
                    c = get_ctx(hT)
                    if c:
                        c.EFlags &= ~TF
                        set_ctx(hT, c)
            elif xcode in BP_CODES and addr in entry_bps and hT:
                seen[addr] += 1
                if seen[addr] == 1:
                    hits.append(addr)
                    print("  hit #%d  fn=0x%08X  (tid=%d)" % (len(hits), addr, tid), flush=True)
                ctx = get_ctx(hT)
                if ctx:
                    wpm(hproc, addr, bytes([entry_bps[addr]]))
                    ctx.Eip = addr
                    ctx.EFlags |= TF
                    set_ctx(hT, ctx)
                    rearm[tid] = addr
            elif xcode not in BP_CODES:
                status = DBG_EXCEPTION_NOT_HANDLED
        k.ContinueDebugEvent(de.dwProcessId, tid, status)
    print("trace: %d distinct fns hit, %d total bp hits" % (len(seen), sum(seen.values())), flush=True)
    _finish(pi)
    return hits


# --- census (run 1 of the differential capture) ---------------------------------------------
def census(exe, targets, outfile, idle_min=30):
    """Arm `targets`; on each function's FIRST hit record its VA and disarm it (one-shot, so
    the game stays fast). Ends when you quit the game (or after `idle_min` minutes without a
    new hit). Writes the fired VAs, in first-hit order, to `outfile`."""
    targets = set(targets)
    k.DebugSetProcessKillOnExit(True)
    pi = launch(exe)
    print("launched pid=%d  CENSUS of %d targets -- play to your starting point, do nothing "
          "else, then QUIT the game" % (pi.dwProcessId, len(targets)), flush=True)
    hproc, threads, entry_bps = None, {}, {}
    fired, fired_set = [], set()
    de = DEBUG_EVENT()
    idle_count = 0
    idle_limit = max(2, int(idle_min * 60 / 15))
    while True:
        if not k.WaitForDebugEvent(C.byref(de), 15000):
            idle_count += 1
            if idle_count % 2 == 0:
                print("  (idle %d/%d; quit the game when done, %d fns so far)"
                      % (idle_count, idle_limit, len(fired)), flush=True)
            if idle_count >= idle_limit:
                print("idle limit reached -> stop", flush=True)
                break
            continue
        idle_count = 0
        code, tid, status = de.dwDebugEventCode, de.dwThreadId, DBG_CONTINUE
        if code == CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            hproc = cpi.hProcess
            threads[tid] = cpi.hThread
            for va in targets:
                set_int3(hproc, va, entry_bps)
            print("armed %d/%d entry bps" % (len(entry_bps), len(targets)), flush=True)
        elif code == CREATE_THREAD_DEBUG_EVENT:
            threads[tid] = de.u.CreateThread.hThread
        elif code == EXIT_THREAD_DEBUG_EVENT:
            threads.pop(tid, None)
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("process exited code=%d (census done)" % de.u.ExitProcess.dwExitCode, flush=True)
            k.ContinueDebugEvent(de.dwProcessId, tid, DBG_CONTINUE)
            break
        elif code == EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            hT = threads.get(tid)
            if xcode in BP_CODES and addr in entry_bps and hT:
                if addr not in fired_set:
                    fired_set.add(addr)
                    fired.append(addr)
                    if len(fired) % 200 == 0:
                        print("  %d fns fired so far" % len(fired), flush=True)
                ctx = get_ctx(hT)
                if ctx:
                    # one-shot: restore the byte, rewind Eip, drop the bp
                    wpm(hproc, addr, bytes([entry_bps[addr]]))
                    ctx.Eip = addr
                    set_ctx(hT, ctx)
                    del entry_bps[addr]
            elif xcode not in BP_CODES:
                status = DBG_EXCEPTION_NOT_HANDLED
        k.ContinueDebugEvent(de.dwProcessId, tid, status)
    with open(outfile, "w", newline="\n") as f:
        f.write("addr\torder\n")
        for i, va in enumerate(fired):
            f.write("0x%08X\t%d\n" % (va, i))
    print("census: %d distinct fns fired (the baseline) -> %s" % (len(fired), outfile), flush=True)
    _finish(pi)
    return outfile


def emit_new_targets(session, baseline=None, rows=None, out=None):
    """From a record-new session, write new_targets.tsv: every function that fired and is
    not in the baseline, with captured=1 when a replayable case was recorded and the extra
    columns of the target list. Sorted captured first, then smallest."""
    session = Path(session)
    baseline = baseline or set()
    rows = rows or {}
    fired = set()
    fp = session / "fired.tsv"
    if fp.exists():
        fired = CM.load_va_set(fp)
    captured = set()
    for cp in session.glob("case_*.pkl"):
        try:
            captured.add(int(cp.stem.rsplit("_", 1)[1], 16))
        except (IndexError, ValueError):
            pass
    if not fired:                    # session killed before fired.tsv: salvage from the cases
        fired = set(captured)
    fired -= baseline
    extra = []
    for r in rows.values():
        extra += [c for c in r if c not in ("addr", "va") and c not in extra]
    out_rows = []
    for va in fired:
        r = rows.get(va, {})
        out_rows.append(dict({"addr": "0x%08X" % va, "captured": int(va in captured)},
                             **{c: r.get(c, "") for c in extra}))

    def size(x):
        s = str(x.get("size", "")).strip()
        return int(s) if s.isdigit() else 1 << 30
    out_rows.sort(key=lambda x: (-x["captured"], size(x), x["addr"]))
    out = Path(out) if out else session / "new_targets.tsv"
    with open(out, "w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["addr", "captured"] + extra, delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)
    print("new targets: %d fired outside the baseline, %d with a state case -> %s"
          % (len(out_rows), len(captured & fired), out), flush=True)
    return out


# --- record -------------------------------------------------------------------------------
def record(exe, targets, corpus, max_cases=6, per_fn_cap=2, max_depth=6,
           max_events=5_000_000, light=False, idle_stop=True, arm_delay=0, again=False):
    """Instrument `targets` and capture self-contained cases {entry_ctx, exit_ctx,
    entry_pages | entry_delta, write_set}, one pickle per case in corpus/session_<pid>/.

    Per invocation (not outermost only), bounded by per_fn_cap (cases per VA), max_depth
    (open captured frames per thread) and max_cases (total). Light mode stores each case's
    bounded pages directly; full mode stores one keyframe per session plus per-case deltas."""
    targets = set(targets)
    if not again:
        have = {va for va in targets
                if glob.glob(str(Path(corpus) / "*" / ("case_*_0x%08X.pkl" % va)))}
        if have:
            targets -= have
            print("skip %d target(s) that already have cases (--again records them anyway); "
                  "%d left" % (len(have), len(targets)), flush=True)
    if not targets:
        print("record: every target already has cases -- nothing to record", flush=True)
        return None
    k.DebugSetProcessKillOnExit(True)
    pi = launch(exe)
    print("launched pid=%d  instrumenting %d targets" % (pi.dwProcessId, len(targets)), flush=True)

    hproc = None
    threads = {}                 # tid -> hThread
    entry_bps = {}               # addr -> original byte
    ret_bps = {}                 # addr -> [original byte, refcount]
    rearm = {}                   # tid -> (addr, kind)
    callstack = {}               # tid -> [frame...]   (captured frames only)
    suspended = set()            # sibling tids frozen during a captured frame
    fn_count = collections.Counter()
    fired = set()                # every target whose entry bp fired
    keyframe = [None]
    cases = []
    de = DEBUG_EVENT()
    ev = 0
    idle_count = 0
    armed = [False]
    t0 = [0.0]

    def arm_all():
        for va in targets:
            set_int3(hproc, va, entry_bps)
        armed[0] = True
        print("armed %d/%d entry bps" % (len(entry_bps), len(targets)), flush=True)

    def arm_ret(retaddr):
        rb = ret_bps.get(retaddr)
        if rb:
            rb[1] += 1
        else:
            ob = read_byte(hproc, retaddr)
            ret_bps[retaddr] = [ob if ob is not None else 0x90, 1]
            wpm(hproc, retaddr, b"\xCC")

    # every case is written the moment it is captured, so a killed session keeps its corpus
    out = Path(corpus) / ("session_%d" % pi.dwProcessId)
    out.mkdir(parents=True, exist_ok=True)
    kf_written = [False]

    def persist(idx, case):
        if not light and not kf_written[0] and keyframe[0] is not None:
            with open(out / "_keyframe.pkl", "wb") as f:
                pickle.dump(keyframe[0], f)
            kf_written[0] = True
        with open(out / ("case_%04d_0x%08X.pkl" % (idx, case["va"])), "wb") as f:
            pickle.dump(case, f)

    while ev < max_events and len(cases) < max_cases:
        ev += 1
        if not armed[0] and arm_delay > 0:
            if hproc is not None and (time.time() - t0[0]) >= arm_delay:
                arm_all()
            timeout = 300
        else:
            timeout = 15000
        if not k.WaitForDebugEvent(C.byref(de), timeout):
            if not armed[0] and arm_delay > 0:
                continue
            if not idle_stop:
                # interactive play: a quiet screen is not "done"; only quitting ends capture
                idle_count += 1
                if idle_count <= 120:                         # about 30 minutes
                    if idle_count % 4 == 0:
                        print("  (idle %d; quit the game when done, %d cases so far)"
                              % (idle_count, len(cases)), flush=True)
                    continue
            print("idle -> stop", flush=True)
            break
        idle_count = 0
        code, tid, status = de.dwDebugEventCode, de.dwThreadId, DBG_CONTINUE

        if code == CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            hproc = cpi.hProcess
            threads[tid] = cpi.hThread
            if arm_delay <= 0:
                arm_all()
            else:
                t0[0] = time.time()
                print("waiting %.0fs before arming ..." % arm_delay, flush=True)
        elif code == CREATE_THREAD_DEBUG_EVENT:
            threads[tid] = de.u.CreateThread.hThread
        elif code == EXIT_THREAD_DEBUG_EVENT:
            threads.pop(tid, None)
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("process exited code=%d" % de.u.ExitProcess.dwExitCode, flush=True)
            k.ContinueDebugEvent(de.dwProcessId, tid, DBG_CONTINUE)
            break
        elif code == EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            hT = threads.get(tid)

            if xcode in SS_CODES:
                if tid in rearm and hT:
                    a, kind = rearm.pop(tid)
                    if kind == "entry":
                        wpm(hproc, a, b"\xCC")
                    elif ret_bps.get(a, [0, 0])[1] > 0:
                        wpm(hproc, a, b"\xCC")
                    else:
                        ret_bps.pop(a, None)
                    c = get_ctx(hT)
                    if c:
                        c.EFlags &= ~TF
                        set_ctx(hT, c)

            elif xcode in BP_CODES and addr in entry_bps and hT:
                fired.add(addr)
                ctx = get_ctx(hT)
                if ctx:
                    depth = len(callstack.get(tid, []))
                    if len(cases) < max_cases and depth < max_depth and fn_count[addr] < per_fn_cap:
                        ec = copy.deepcopy(ctx)
                        ec.Eip = addr
                        entry_esp = ctx.Esp
                        retaddr = read_u32(hproc, entry_esp)
                        if retaddr:
                            if depth == 0:
                                # outermost captured frame: freeze every other thread so the
                                # write set holds this call's writes only
                                for t, hh in threads.items():
                                    if t != tid and t not in suspended:
                                        k.SuspendThread(hh)
                                        suspended.add(t)
                            ep = light_snap(hproc, ec) if light else snapshot(hproc)
                            if not light and keyframe[0] is None:
                                keyframe[0] = ep
                            callstack.setdefault(tid, []).append(
                                {"va": addr, "entry_esp": entry_esp, "retaddr": retaddr,
                                 "entry_ctx": bytes(ec), "entry_pages": ep})
                            fn_count[addr] += 1
                            arm_ret(retaddr)
                    wpm(hproc, addr, bytes([entry_bps[addr]]))
                    ctx.Eip = addr
                    # one-shot disarm once a target has its cases: otherwise every hot
                    # function single-steps forever and the game hangs
                    if fn_count[addr] >= per_fn_cap or len(cases) >= max_cases:
                        set_ctx(hT, ctx)
                        del entry_bps[addr]
                    else:
                        ctx.EFlags |= TF
                        set_ctx(hT, ctx)
                        rearm[tid] = (addr, "entry")

            elif xcode in BP_CODES and addr in ret_bps and hT:
                ctx = get_ctx(hT)
                if ctx:
                    st = callstack.get(tid)
                    if st and st[-1]["retaddr"] == addr and ctx.Esp >= st[-1]["entry_esp"] + 4:
                        fr = st.pop()
                        xc = copy.deepcopy(ctx)
                        xc.Eip = addr
                        if light:
                            post = {p: rpm(hproc, p, len(b)) for p, b in fr["entry_pages"].items()}
                            wset = {p: d for p, d in post.items() if d and d != fr["entry_pages"][p]}
                            edelta = fr["entry_pages"]
                            cases.append({"va": fr["va"], "retaddr": addr,
                                          "entry_ctx": fr["entry_ctx"], "exit_ctx": bytes(xc),
                                          "entry_pages": fr["entry_pages"], "write_set": wset})
                        else:
                            wset = diff_pages(fr["entry_pages"], snapshot(hproc))
                            edelta = diff_pages(keyframe[0], fr["entry_pages"])
                            cases.append({"va": fr["va"], "retaddr": addr,
                                          "entry_ctx": fr["entry_ctx"], "exit_ctx": bytes(xc),
                                          "entry_delta": edelta, "write_set": wset})
                        persist(len(cases) - 1, cases[-1])
                        fr["entry_pages"] = None
                        ret_bps[addr][1] -= 1
                        if not st:        # outermost frame closed -> unfreeze siblings
                            for t in list(suspended):
                                hh = threads.get(t)
                                if hh:
                                    k.ResumeThread(hh)
                            suspended.clear()
                        ec = WOW64_CONTEXT.from_buffer_copy(fr["entry_ctx"])
                        print("  case %d: fn=0x%08X ret=0x%08X ecx=0x%08X pages=%d writePages=%d "
                              "eax=0x%08X" % (len(cases), fr["va"], addr, ec.Ecx, len(edelta),
                                              len(wset), xc.Eax), flush=True)
                    wpm(hproc, addr, bytes([ret_bps[addr][0]]))
                    ctx.Eip = addr
                    ctx.EFlags |= TF
                    set_ctx(hT, ctx)
                    rearm[tid] = (addr, "ret")

            elif xcode not in BP_CODES:
                status = DBG_EXCEPTION_NOT_HANDLED

        k.ContinueDebugEvent(de.dwProcessId, tid, status)

    with open(out / "fired.tsv", "w", newline="\n") as f:
        f.write("addr\n")
        for va in sorted(fired):
            f.write("0x%08X\n" % va)
    by_fn = collections.Counter(c["va"] for c in cases)
    with open(out / "meta.json", "w") as f:
        json.dump({"exe": exe.name, "n_cases": len(cases), "n_fired": len(fired),
                   "targets": len(targets), "events": ev, "light": light,
                   "by_fn": {"0x%08X" % v: n for v, n in by_fn.items()}}, f, indent=2)
    print("captured %d cases (%d distinct fns) over %d events -> %s"
          % (len(cases), len(by_fn), ev, out), flush=True)
    _finish(pi)
    return out


# --- probe ----------------------------------------------------------------------------------
def probe(exe, max_events=400):
    """Start the game, run the debug loop to the loader breakpoint, read the WOW64 context,
    terminate. Proves the debugger works on this machine."""
    k.DebugSetProcessKillOnExit(True)
    pi = launch(exe)
    print("launched pid=%d tid=%d" % (pi.dwProcessId, pi.dwThreadId))
    de = DEBUG_EVENT()
    main_thread = None
    counts = {}
    for _ in range(max_events):
        if not k.WaitForDebugEvent(C.byref(de), 10000):
            print("WaitForDebugEvent timeout/err=%d -> stop" % C.get_last_error())
            break
        code = de.dwDebugEventCode
        counts[_EVNAME.get(code, code)] = counts.get(_EVNAME.get(code, code), 0) + 1
        status = DBG_CONTINUE
        if code == CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            main_thread = cpi.hThread
            print("CREATE_PROCESS  image base = 0x%X" % (cpi.lpBaseOfImage or 0))
        elif code == EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xc = er.ExceptionCode & 0xFFFFFFFF
            print("EXCEPTION code=0x%08X addr=0x%X firstChance=%d"
                  % (xc, er.ExceptionAddress or 0, de.u.Exception.dwFirstChance))
            if xc in BP_CODES:
                ctx = get_ctx(main_thread) if main_thread else None
                if ctx:
                    print("  WOW64 ctx OK: Eip=0x%08X Esp=0x%08X Eax=0x%08X" % (ctx.Eip, ctx.Esp, ctx.Eax))
                else:
                    print("  Wow64GetThreadContext FAILED err=%d" % C.get_last_error())
                print("reached the loader breakpoint -> terminating the game")
                k.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_CONTINUE)
                break
            status = DBG_EXCEPTION_NOT_HANDLED
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("EXIT_PROCESS code=%d" % de.u.ExitProcess.dwExitCode)
            k.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_CONTINUE)
            break
        k.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, status)
    print("event counts:", counts)
    _finish(pi)


# --- command line ---------------------------------------------------------------------------
def _targets(a, need=True):
    vas, rows = [], {}
    if getattr(a, "targets", None):
        vas, rows = CM.load_targets(a.targets)
    for v in getattr(a, "va", None) or []:
        va = int(v, 16)
        if va not in rows:
            vas.append(va)
            rows[va] = {"addr": "0x%08X" % va}
    excl = CM.load_va_set(getattr(a, "exclude", None))
    vas = [v for v in vas if v not in excl]
    if need and not vas:
        raise SystemExit("no targets: pass --targets FILE (see functions.py) or VAs")
    return vas, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def game_args(p):
        p.add_argument("--exe", help="the game exe inside the test install "
                                     "(default PR2_TEST_EXE or <PR2_TEST_ENV>/PR2_target.exe)")
        p.add_argument("--wait", action="store_true", help="wait for the test-install lock instead of failing")

    def target_args(p, vas=False):
        p.add_argument("--targets", help="target list: TSV with an addr column, or one hex VA per line")
        p.add_argument("--exclude", help="VAs to leave out (same formats)")
        if vas:
            p.add_argument("va", nargs="*", help="extra target VAs (hex)")

    p = sub.add_parser("probe", help="start the game, stop at the loader breakpoint")
    game_args(p)
    p = sub.add_parser("trace", help="log the first N distinct targets hit (no capture)")
    game_args(p)
    target_args(p, vas=True)
    p.add_argument("--max", type=int, default=40, help="distinct hits to log (default 40)")
    p = sub.add_parser("census", help="run 1: record the baseline set of functions that fire")
    game_args(p)
    target_args(p)
    p.add_argument("--out", help="baseline file (default _build/replay/baseline.tsv)")
    p.add_argument("--idle-min", type=float, default=30, help="stop after N idle minutes (default 30)")
    p = sub.add_parser("record", help="capture cases for VAs or a target list")
    game_args(p)
    target_args(p, vas=True)
    p.add_argument("--max", type=int, default=6, help="total cases (default 6)")
    p.add_argument("--per-fn", type=int, default=2, help="cases per function (default 2)")
    p.add_argument("--light", action="store_true", help="bounded capture instead of full snapshots")
    p.add_argument("--interactive", action="store_true", help="do not stop when the game is idle; quit the game to end")
    p.add_argument("--arm-after", type=float, default=0, help="arm only after N seconds (skip startup)")
    p.add_argument("--again", action="store_true", help="record targets that already have cases")
    p = sub.add_parser("record-new", help="run 2: record only targets the baseline never ran")
    game_args(p)
    target_args(p)
    p.add_argument("--baseline", help="baseline file from census (default _build/replay/baseline.tsv)")
    p.add_argument("--max", type=int, default=3000, help="total cases (default 3000)")
    p.add_argument("--per-fn", type=int, default=2, help="cases per function (default 2)")
    p.add_argument("--full", action="store_true", help="full snapshots instead of light capture (heavy)")
    p = sub.add_parser("new-targets", help="rebuild new_targets.tsv for a session (offline)")
    p.add_argument("session", help="a corpus session folder")
    p.add_argument("--baseline", help="baseline file (default _build/replay/baseline.tsv)")
    p.add_argument("--targets", help="target list whose extra columns annotate the output")
    a = ap.parse_args()

    default_baseline = CM.OUT / "baseline.tsv"
    if a.cmd == "new-targets":
        rows = CM.load_targets(a.targets)[1] if a.targets else {}
        emit_new_targets(a.session, CM.load_va_set(a.baseline or default_baseline), rows)
        return
    exe = CM.game_exe(a.exe)
    with CM.test_lock(f"recorder.py {a.cmd}", wait=a.wait):
        if a.cmd == "probe":
            probe(exe)
        elif a.cmd == "trace":
            trace(exe, _targets(a)[0], max_hits=a.max)
        elif a.cmd == "census":
            out = Path(a.out) if a.out else default_baseline
            CM.outdir()
            census(exe, _targets(a)[0], out, idle_min=a.idle_min)
        elif a.cmd == "record":
            record(exe, _targets(a)[0], CM.outdir("corpus"), max_cases=a.max, per_fn_cap=a.per_fn,
                   light=a.light, idle_stop=not a.interactive, arm_delay=a.arm_after, again=a.again)
        elif a.cmd == "record-new":
            vas, rows = _targets(a)
            bpath = Path(a.baseline) if a.baseline else default_baseline
            baseline = CM.load_va_set(bpath) if bpath.exists() else set()
            if not baseline:
                print("WARNING: no baseline at %s -- run `census` first. Recording ALL %d targets."
                      % (bpath, len(vas)), flush=True)
            armed = [v for v in vas if v not in baseline]
            print("record-new: %d targets - %d in the baseline = %d armed"
                  % (len(vas), len(vas) - len(armed), len(armed)), flush=True)
            session = record(exe, armed, CM.outdir("corpus"), max_cases=a.max, per_fn_cap=a.per_fn,
                             light=not a.full, idle_stop=False, again=True)
            if session:
                emit_new_targets(session, baseline, rows)


if __name__ == "__main__":
    main()
