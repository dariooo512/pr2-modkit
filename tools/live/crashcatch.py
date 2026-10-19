#!/usr/bin/env python3
"""Crash catcher: start the game under a debugger and say exactly where a C++ exception came from.

    python crashcatch.py                       # <PR2_TEST_ENV>/PR2_target.exe, log to _build/live/crashcatch/
    python crashcatch.py --exe PR2_mymod.exe --throws 200
    python crashcatch.py --throw-helper Walhalla.dll+0x9BB26     # an engine wrapper that throws

Then reproduce the crash in the game. For every C++ exception (0xE06D7363) it logs:
  * FIRST (the game may still catch it) or SECOND chance (unhandled: the real crash);
  * the thrown type (decoded from the MSVC throw info);
  * the THROW SITE: the return address right after the call to _CxxThrowException (or to
    a --throw-helper), as Module+offset: the function that threw;
  * the exception OBJECT: every string inside it and every member that points at a string.
    The engine's exception classes are built as (code, __FILE__, __LINE__), so a member
    pointing at "...\\Something.cpp" with a small integer next to it is printed as a
    FILE:LINE candidate: the source line of the failed check;
  * the call chain: the EBP chain, plus a stack scan for return addresses that follow a call
    instruction, in any loaded module (works through frame-pointer-omitted code).
A second-chance exception of any kind (access violation included) gets registers and the
call chain. Other first-chance exceptions get one line each (deduplicated).

Use tools/test/gamehost.py for breakpoints, input and everyday runs; use this when a
crash or a caught throw needs its origin pinned down.

Safety: starts only an exe inside the test install (PR2_TEST_ENV; refuses "steamapps" and
the SteamStub exe), takes the shared test-install lock, shows the window without
activating it, and passes every exception on to the game unchanged.

Limits: decoding assumes MSVC's 32-bit throw info (VC7.1, the game and engine). The
FILE:LINE candidates are heuristic (strings and small integers near each other); the
type name and throw site are exact. The first-chance log is capped by --throws.
"""
import argparse
import ctypes as C
import struct
import time
from ctypes import wintypes as W
from pathlib import Path

import proc

k = proc.k
k.WaitForDebugEvent.argtypes = [C.c_void_p, W.DWORD]
k.ContinueDebugEvent.argtypes = [W.DWORD, W.DWORD, W.DWORD]
k.OpenThread.restype = W.HANDLE
k.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
k.Wow64GetThreadContext.argtypes = [W.HANDLE, C.c_void_p]
k.DebugSetProcessKillOnExit.argtypes = [W.BOOL]

DEBUG_ONLY_THIS_PROCESS = 0x2
EXCEPTION, CREATE_PROCESS, EXIT_PROCESS, LOAD_DLL = 1, 3, 5, 6
DBG_CONTINUE, DBG_NOT_HANDLED = 0x00010002, 0x80010001
CXX = 0xE06D7363
CXX_MAGIC = (0x19930520, 0x19930521, 0x19930522)
BP_CODES = (0x80000003, 0x4000001F)
WOW64_CONTEXT_FULL = 0x10007
THREAD_ALL_ACCESS = 0x1FFFFF
SOURCE_EXT = (".cpp", ".c", ".h", ".hpp", ".inl", ".cxx")


class EXCEPTION_RECORD(C.Structure):
    _fields_ = [("Code", W.DWORD), ("Flags", W.DWORD), ("Record", C.c_void_p),
                ("Address", C.c_void_p), ("NumParams", W.DWORD), ("Info", C.c_size_t * 15)]


class EXCEPTION_DEBUG_INFO(C.Structure):
    _fields_ = [("Record", EXCEPTION_RECORD), ("FirstChance", W.DWORD)]


class CREATE_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [("hFile", W.HANDLE), ("hProcess", W.HANDLE), ("hThread", W.HANDLE),
                ("Base", C.c_void_p), ("DbgOff", W.DWORD), ("DbgSize", W.DWORD),
                ("Tls", C.c_void_p), ("Start", C.c_void_p), ("ImageName", C.c_void_p),
                ("Unicode", W.WORD)]


class LOAD_DLL_DEBUG_INFO(C.Structure):
    _fields_ = [("hFile", W.HANDLE), ("Base", C.c_void_p), ("DbgOff", W.DWORD),
                ("DbgSize", W.DWORD), ("ImageName", C.c_void_p), ("Unicode", W.WORD)]


class EXIT_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [("ExitCode", W.DWORD)]


class DEBUG_EVENT(C.Structure):
    class _U(C.Union):
        _fields_ = [("Exception", EXCEPTION_DEBUG_INFO), ("CreateProcess", CREATE_PROCESS_DEBUG_INFO),
                    ("LoadDll", LOAD_DLL_DEBUG_INFO), ("ExitProcess", EXIT_PROCESS_DEBUG_INFO),
                    ("_pad", C.c_byte * 256)]
    _fields_ = [("Code", W.DWORD), ("Pid", W.DWORD), ("Tid", W.DWORD), ("u", _U)]


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", W.DWORD), ("r", W.LPWSTR), ("desk", W.LPWSTR), ("title", W.LPWSTR),
                ("pos", W.DWORD * 7), ("flags", W.DWORD), ("show", W.WORD), ("cbR", W.WORD),
                ("lpR", C.c_void_p), ("hIn", W.HANDLE), ("hOut", W.HANDLE), ("hErr", W.HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("Pid", W.DWORD), ("Tid", W.DWORD)]


k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                             C.c_void_p, W.LPCWSTR, C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION)]
k.CreateProcessW.restype = W.BOOL


class Catcher:
    def __init__(self, exe, log, max_throws, per_site, helpers):
        self.exe, self.log, self.max_throws, self.per_site = exe, log, max_throws, per_site
        self.helper_specs = helpers           # ["Module.dll+0xRVA", ...]
        self.h = None
        self.mods = []                        # (base, size, name)
        self.throw_fns = None                 # absolute addresses that throw (resolved lazily)
        self.cxx_logged = 0
        self.seen = {}                        # dedupe key -> count

    # --- memory -----------------------------------------------------------------------
    def read(self, a, n):
        return proc.read(self.h, a, n) if a else b""

    def u32(self, a):
        b = self.read(a, 4)
        return struct.unpack("<I", b)[0] if len(b) == 4 else None

    def cstr(self, a, n=256):
        z = self.read(a, n).split(b"\0")[0]
        return z.decode("latin1") if len(z) >= 4 and all(32 <= c < 127 for c in z) else None

    def where(self, a):
        for base, size, name in self.mods:
            if base <= a < base + size:
                return f"{name}+0x{a - base:X}"
        return f"0x{a:X}"

    def in_module(self, a):
        return any(base <= a < base + size for base, size, _ in self.mods)

    def add_module(self, base, name):
        size = 0x1000
        e = self.u32(base + 0x3C)
        if e is not None and self.read(base + e, 4) == b"PE\0\0":
            size = self.u32(base + e + 24 + 56) or size
        self.mods.append((base, size, name))

    def export(self, base, want):
        e = self.u32(base + 0x3C)
        if e is None:
            return None
        rva = self.u32(base + e + 24 + 96)
        if not rva:
            return None
        ex = base + rva
        n = self.u32(ex + 0x18) or 0
        if not 0 < n < 100000:
            return None
        funcs, names, ords = (base + (self.u32(ex + o) or 0) for o in (0x1C, 0x20, 0x24))
        tbl = self.read(names, 4 * n)
        for i in range(len(tbl) // 4):
            s = self.read(base + struct.unpack_from("<I", tbl, 4 * i)[0], len(want) + 1)
            if s == want.encode() + b"\0":
                o = struct.unpack("<H", self.read(ords + 2 * i, 2))[0]
                return base + (self.u32(funcs + 4 * o) or 0)
        return None

    def resolve_throwers(self):
        """_CxxThrowException of the loaded C runtime DLLs, plus the --throw-helper sites."""
        fns = {}
        for base, _size, name in self.mods:
            if not name.lower().startswith(("msvcr", "vcruntime")):
                continue
            a = self.export(base, "_CxxThrowException")
            if a:
                fns[a] = f"{name}!_CxxThrowException"
        for spec in self.helper_specs:
            mname, _, off = spec.partition("+")
            for base, _size, name in self.mods:
                if name.lower() == mname.lower():
                    fns[base + int(off, 16)] = f"{name}+{off} (helper)"
        self.throw_fns = fns
        if set(fns.values()) != getattr(self, "_logged_throwers", None):
            self._logged_throwers = set(fns.values())
            self.log("throw functions: " + (", ".join(sorted(fns.values())) or
                                            "none found (statically linked CRT? pass --throw-helper)"))

    # --- threads and stacks ------------------------------------------------------------
    def context(self, tid):
        h = k.OpenThread(THREAD_ALL_ACCESS, False, tid)
        buf = (C.c_byte * 716)()
        struct.pack_into("<I", buf, 0, WOW64_CONTEXT_FULL)
        ok = h and k.Wow64GetThreadContext(h, buf)
        if h:
            k.CloseHandle(h)
        if not ok:
            return None
        raw = bytes(buf)
        regs = dict(zip(("edi", "esi", "ebx", "edx", "ecx", "eax", "ebp", "eip"),
                        struct.unpack_from("<8I", raw, 156)))
        regs["esp"] = struct.unpack_from("<I", raw, 196)[0]
        return regs

    def call_target(self, ret):
        """If the instruction before `ret` is a call: (kind, target or None)."""
        pre = self.read(ret - 6, 6)
        if len(pre) < 6:
            return None
        if pre[1] == 0xE8:                                         # call rel32
            return "rel", (ret + struct.unpack_from("<i", pre, 2)[0]) & 0xFFFFFFFF
        if pre[0] == 0xFF and pre[1] == 0x15:                      # call [abs32]
            return "ind", self.u32(struct.unpack_from("<I", pre, 2)[0])
        if pre[0] == 0xFF and 0x90 <= pre[1] <= 0x97:              # call [reg+disp32]
            return "reg", None
        if pre[3] == 0xFF and 0x50 <= pre[4] <= 0x57:              # call [reg+disp8]
            return "reg", None
        if pre[4] == 0xFF and 0xD0 <= pre[5] <= 0xD7:              # call reg
            return "reg", None
        return None

    def thunk(self, a):
        """Follow a `jmp [abs32]` import thunk."""
        b = self.read(a, 6)
        if len(b) == 6 and b[0] == 0xFF and b[1] == 0x25:
            return self.u32(struct.unpack_from("<I", b, 2)[0])
        return a

    def throw_site(self, esp):
        if self.throw_fns is None:
            self.resolve_throwers()
        stk = self.read(esp, 0x3000)
        for i in range(0, len(stk) - 3, 4):
            v = struct.unpack_from("<I", stk, i)[0]
            if not self.in_module(v):
                continue
            ct = self.call_target(v)
            if ct and ct[1] is not None:
                t = self.thunk(ct[1])
                if t in self.throw_fns or ct[1] in self.throw_fns:
                    return v, self.throw_fns.get(t) or self.throw_fns[ct[1]]
        return None

    def chain(self, regs, depth=24):
        out = []
        eb = regs["ebp"]
        frames = []
        for _ in range(30):
            d = self.read(eb, 8)
            if len(d) < 8:
                break
            nxt, ret = struct.unpack("<II", d)
            if ret and self.in_module(ret):
                frames.append(self.where(ret))
            if nxt <= eb or nxt - eb > 0x100000:
                break
            eb = nxt
        out.append("  ebp chain: " + (" ".join(frames) or "(none)"))
        stk = self.read(regs["esp"], 0x2000)
        rets = []
        for i in range(0, len(stk) - 3, 4):
            v = struct.unpack_from("<I", stk, i)[0]
            if self.in_module(v) and self.call_target(v):
                rets.append(f"{self.where(v)}")
                if len(rets) >= depth:
                    break
        out.append("  stack scan (returns after a call): " + (" ".join(rets) or "(none)"))
        return out

    # --- C++ exceptions -----------------------------------------------------------------
    def cxx(self, rec):
        """(type name, thrown object address) from an MSVC throw."""
        info = [rec.Info[i] & 0xFFFFFFFF for i in range(4)]
        if rec.NumParams < 3 or info[0] not in CXX_MAGIC:
            return None, None
        try:
            cta = self.u32(info[2] + 0x0C)
            ct = self.u32(cta + 4)
            td = self.u32(ct + 4)
            name = self.read(td + 8, 160).split(b"\0")[0].decode("latin1", "ignore")
        except TypeError:
            name = "?"
        return name or "?", info[1]

    def describe_object(self, obj, type_name):
        out = []
        if type_name.startswith((".PA", ".PB")):          # a pointer to an object was thrown
            ptr = self.u32(obj)
            out.append(f"  object: pointer slot 0x{obj:X} -> 0x{(ptr or 0):X}")
            obj = ptr or 0
        elif not type_name.startswith(".?A"):              # a scalar (int, char*, ...)
            v = self.u32(obj)
            s = self.cstr(v) if v and 0x10000 <= v < 0x7FFF0000 else None
            out.append(f"  value: 0x{(v or 0):X}" + (f' -> "{s}"' if s else ""))
            return out
        raw = self.read(obj, 0x200)
        if not raw:
            out.append("  object: unreadable")
            return out
        out.append(f"  object @ 0x{obj:X}: {raw[:0x40].hex(' ')}")
        i = 0
        while i < len(raw):                                   # strings stored inside it
            if 32 <= raw[i] < 127:
                j = i
                while j < len(raw) and 32 <= raw[j] < 127:
                    j += 1
                if j - i >= 4:
                    out.append(f'    obj+0x{i:X}: "{raw[i:j].decode("latin1")}"')
                i = j
            else:
                i += 1
        words = struct.unpack_from("<%dI" % (len(raw) // 4), raw)
        cands = []
        for n, w in enumerate(words):                         # members pointing at strings
            s = self.cstr(w) if 0x10000 <= w < 0x7FFF0000 else None
            if not s:
                continue
            out.append(f'    [obj+0x{4 * n:X}] -> "{s}"')
            if s.lower().endswith(SOURCE_EXT):            # nearest members first
                near = [words[m] for d in (1, -1, 2, -2, 3, -3) for m in [n + d]
                        if 0 <= m < len(words) and 0 < words[m] < 100000]
                cands += [f"{s}:{ln}" for ln in near]
        if cands:
            out.append("  FILE:LINE candidates: " + ", ".join(dict.fromkeys(cands)))
        return out

    # --- events ----------------------------------------------------------------------------
    def on_exception(self, ev):
        rec, first = ev.u.Exception.Record, ev.u.Exception.FirstChance
        code, addr = rec.Code, (rec.Address or 0) & 0xFFFFFFFF
        if code in BP_CODES:                  # the loader breakpoints (native and WOW64)
            return DBG_CONTINUE
        if code == CXX and first and self.cxx_logged >= self.max_throws:
            return DBG_NOT_HANDLED
        regs = self.context(ev.Tid)
        if code == CXX:
            tname, obj = self.cxx(rec)
            site = self.throw_site(regs["esp"]) if regs else None
            site_s = f"{self.where(site[0])} (calls {site[1]})" if site else "?"
            if first:
                key = (tname, site[0] if site else addr)
                self.seen[key] = self.seen.get(key, 0) + 1
                if self.seen[key] > self.per_site:
                    return DBG_NOT_HANDLED
                self.cxx_logged += 1
                self.log(f"\n--- FIRST  C++ throw #{self.cxx_logged} type={tname} throw site={site_s} ---")
            else:
                self.log(f"\n########## SECOND C++ exception type={tname} throw site={site_s}  "
                         f"<-- UNHANDLED CRASH ##########")
            if obj:
                for ln in self.describe_object(obj, tname or ""):
                    self.log(ln)
            if regs:
                if not first:
                    self.log("  regs " + " ".join(f"{r}={v:08x}" for r, v in regs.items()))
                for ln in self.chain(regs, 24 if not first else 12):
                    self.log(ln)
            return DBG_NOT_HANDLED
        if first:
            key = (code, addr)
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] <= 3:
                self.log(f"FIRST  0x{code:08X} at {self.where(addr)}")
            return DBG_NOT_HANDLED
        self.log(f"\n########## SECOND 0x{code:08X} at {self.where(addr)}  <-- UNHANDLED CRASH ##########")
        if code == 0xC0000005 and rec.NumParams >= 2:
            self.log(f"  access violation: {'write' if rec.Info[0] == 1 else 'read'} "
                     f"of 0x{rec.Info[1] & 0xFFFFFFFF:X}")
        if regs:
            self.log("  regs " + " ".join(f"{r}={v:08x}" for r, v in regs.items()))
            for ln in self.chain(regs):
                self.log(ln)
        return DBG_NOT_HANDLED

    def modpath(self, hfile):
        buf = C.create_unicode_buffer(520)
        if hfile and k.GetFinalPathNameByHandleW(hfile, buf, 520, 0):
            return buf.value
        return "?"

    def run(self):
        si, pi = STARTUPINFO(), PROCESS_INFORMATION()
        si.cb = C.sizeof(si)
        si.flags, si.show = 0x1, 4            # STARTF_USESHOWWINDOW, SW_SHOWNOACTIVATE
        k.DebugSetProcessKillOnExit(True)
        if not k.CreateProcessW(str(self.exe), None, None, None, False, DEBUG_ONLY_THIS_PROCESS,
                                None, str(self.exe.parent), C.byref(si), C.byref(pi)):
            self.log(f"CreateProcess failed: {C.get_last_error()}")
            return 2
        self.log(f"launched pid={pi.Pid} {self.exe}; reproduce the crash in the game")
        ev = DEBUG_EVENT()
        t0 = time.time()
        try:
            while True:
                if not k.WaitForDebugEvent(C.byref(ev), 1000):
                    continue
                status = DBG_CONTINUE
                if ev.Code == CREATE_PROCESS:
                    self.h = ev.u.CreateProcess.hProcess
                    self.add_module(ev.u.CreateProcess.Base or 0x400000, self.exe.name)
                    k.CloseHandle(ev.u.CreateProcess.hFile)
                elif ev.Code == LOAD_DLL:
                    self.add_module(ev.u.LoadDll.Base or 0, Path(self.modpath(ev.u.LoadDll.hFile)).name)
                    self.throw_fns = None             # re-resolve with the new module
                    k.CloseHandle(ev.u.LoadDll.hFile)
                elif ev.Code == EXCEPTION:
                    status = self.on_exception(ev)
                elif ev.Code == EXIT_PROCESS:
                    self.log(f"\n=== process exited, code 0x{ev.u.ExitProcess.ExitCode:X}, "
                             f"after {time.time() - t0:.0f}s; {self.cxx_logged} C++ throw(s) logged ===")
                    k.ContinueDebugEvent(ev.Pid, ev.Tid, status)
                    return 0
                k.ContinueDebugEvent(ev.Pid, ev.Tid, status)
        except KeyboardInterrupt:
            self.log("interrupted: the game is closed with the debugger")
            k.TerminateProcess(pi.hProcess, 0)
            return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", help="the game exe inside the test install "
                                  "(default PR2_TEST_EXE or <PR2_TEST_ENV>/PR2_target.exe)")
    ap.add_argument("--throws", type=int, default=80, help="first-chance C++ throws to log (default 80)")
    ap.add_argument("--per-site", type=int, default=3, help="log each (type, throw site) at most N times (default 3)")
    ap.add_argument("--throw-helper", action="append", default=[], metavar="MODULE+0xRVA",
                    help="a function that throws on behalf of its caller (repeatable)")
    ap.add_argument("--log", help="log file (default _build/live/crashcatch/<time>.log)")
    ap.add_argument("--wait", action="store_true", help="wait for the test-install lock")
    a = ap.parse_args()
    for spec in a.throw_helper:
        mname, plus, off = spec.partition("+")
        try:
            int(off, 16)
        except ValueError:
            plus = ""
        if not plus:
            raise SystemExit(f"--throw-helper {spec}: expected Module.dll+0xRVA")
    exe = proc.game_exe(a.exe)
    logp = Path(a.log) if a.log else proc.outdir("crashcatch") / time.strftime("%Y%m%d-%H%M%S.log")
    f = open(logp, "w", buffering=1, encoding="utf-8")

    def log(m):
        f.write(m + "\n")
        print(m, flush=True)

    log(f"log {logp}")
    with proc.test_lock("crashcatch.py", wait=a.wait):
        rc = Catcher(exe, log, a.throws, a.per_site, a.throw_helper).run()
    f.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
