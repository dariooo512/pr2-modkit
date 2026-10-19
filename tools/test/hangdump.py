"""Where is a hung game waiting? Dump every thread's EIP and stack return addresses.

    python tools/test/hangdump.py [--pid N] [--exe PR2_target.exe] [--words 4096]

Without --pid or --exe it picks the first running PR2_*.exe (a test exe, never the Steam PR2.exe).

Works from outside the debugger (gamehost owns the debug port): each thread is
suspended, its WOW64 context read, up to --words stack dwords scanned for values
that point into a loaded module's image, and resumed. Prints module+offset for
EIP and for every candidate return address, plus the CPU time of each thread,
so a deadlock (all threads in a wait, CPU flat) can be told from a busy loop.
"""
import argparse
import ctypes as C
import struct
import sys
from ctypes import wintypes as W

k = C.WinDLL("kernel32", use_last_error=True)
psapi = C.WinDLL("psapi", use_last_error=True)
k.OpenProcess.restype = W.HANDLE
k.OpenThread.restype = W.HANDLE
k.CreateToolhelp32Snapshot.restype = W.HANDLE
TH32CS_SNAPTHREAD, TH32CS_SNAPPROCESS = 0x4, 0x2
PROCESS_ALL = 0x1F0FFF
THREAD_ALL = 0x1FFFFF
WOW64_CONTEXT_FULL = 0x10007


class THREADENTRY32(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ThreadID", W.DWORD),
                ("th32OwnerProcessID", W.DWORD), ("tpBasePri", W.LONG), ("tpDeltaPri", W.LONG),
                ("dwFlags", W.DWORD)]


class PROCESSENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", C.c_size_t), ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG), ("dwFlags", W.DWORD),
                ("szExeFile", W.WCHAR * 260)]


class WOW64_CONTEXT(C.Structure):
    _fields_ = [("ContextFlags", W.DWORD), ("Dr", W.DWORD * 6), ("FloatSave", C.c_byte * 112),
                ("SegGs", W.DWORD), ("SegFs", W.DWORD), ("SegEs", W.DWORD), ("SegDs", W.DWORD),
                ("Edi", W.DWORD), ("Esi", W.DWORD), ("Ebx", W.DWORD), ("Edx", W.DWORD),
                ("Ecx", W.DWORD), ("Eax", W.DWORD), ("Ebp", W.DWORD), ("Eip", W.DWORD),
                ("SegCs", W.DWORD), ("EFlags", W.DWORD), ("Esp", W.DWORD), ("SegSs", W.DWORD),
                ("Ext", C.c_byte * 512)]


def find_test_exe():
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W()
    e.dwSize = C.sizeof(e)
    ok = k.Process32FirstW(snap, C.byref(e))
    while ok:
        n = e.szExeFile.lower()
        if n.startswith("pr2_") and n.endswith(".exe"):
            k.CloseHandle(snap)
            return e.szExeFile
        ok = k.Process32NextW(snap, C.byref(e))
    k.CloseHandle(snap)
    return None


def find_pid(exe):
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W()
    e.dwSize = C.sizeof(e)
    ok = k.Process32FirstW(snap, C.byref(e))
    while ok:
        if e.szExeFile.lower() == exe.lower():
            k.CloseHandle(snap)
            return e.th32ProcessID
        ok = k.Process32NextW(snap, C.byref(e))
    k.CloseHandle(snap)
    return None


def threads(pid):
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    e = THREADENTRY32()
    e.dwSize = C.sizeof(e)
    out = []
    ok = k.Thread32First(snap, C.byref(e))
    while ok:
        if e.th32OwnerProcessID == pid:
            out.append(e.th32ThreadID)
        ok = k.Thread32Next(snap, C.byref(e))
    k.CloseHandle(snap)
    return out


def modules(hp):
    arr = (W.HMODULE * 1024)()
    need = W.DWORD()
    psapi.EnumProcessModulesEx(hp, arr, C.sizeof(arr), C.byref(need), 0x1)   # LIST_MODULES_32BIT
    mods = []
    for i in range(need.value // C.sizeof(W.HMODULE)):
        base = arr[i]
        if not base:
            continue
        name = C.create_unicode_buffer(260)
        psapi.GetModuleBaseNameW(hp, W.HMODULE(base), name, 260)

        class MI(C.Structure):
            _fields_ = [("base", C.c_void_p), ("size", W.DWORD), ("entry", C.c_void_p)]
        mi = MI()
        psapi.GetModuleInformation(hp, W.HMODULE(base), C.byref(mi), C.sizeof(mi))
        mods.append((base, base + mi.size, name.value))
    return sorted(mods)


def where(mods, a):
    for lo, hi, n in mods:
        if lo <= a < hi:
            return f"{n}+{a - lo:#x}" if not n.lower().endswith(".exe") else f"{n}:{a:#010x}"
    return None


def read(hp, addr, n):
    buf = C.create_string_buffer(n)
    got = C.c_size_t()
    k.ReadProcessMemory(hp, C.c_void_p(addr), buf, n, C.byref(got))
    return buf.raw[:got.value]


def ft(f):
    return (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pid", type=int)
    ap.add_argument("--exe", help="process name (default: the first running PR2_*.exe)")
    ap.add_argument("--words", type=int, default=2048)
    a = ap.parse_args()
    exe = a.exe or find_test_exe()
    pid = a.pid or (find_pid(exe) if exe else None)
    if not pid:
        sys.exit(f"{exe or 'no PR2_*.exe'} not running")
    hp = k.OpenProcess(PROCESS_ALL, False, pid)
    mods = modules(hp)
    print(f"pid {pid}: {len(mods)} modules")
    for tid in threads(pid):
        ht = k.OpenThread(THREAD_ALL, False, tid)
        if not ht:
            print(f"thread {tid}: cannot open")
            continue
        times = [W.FILETIME() for _ in range(4)]
        k.GetThreadTimes(ht, *[C.byref(t) for t in times])
        k.Wow64SuspendThread(ht)
        c = WOW64_CONTEXT()
        c.ContextFlags = WOW64_CONTEXT_FULL
        k.Wow64GetThreadContext(ht, C.byref(c))
        stack = read(hp, c.Esp, a.words * 4)
        k.ResumeThread(ht)
        k.CloseHandle(ht)
        print(f"\n== thread {tid}  cpu user {ft(times[3]):.2f}s kernel {ft(times[2]):.2f}s  "
              f"eip {where(mods, c.Eip) or hex(c.Eip)}  esp {c.Esp:#x}")
        print(f"   eax {c.Eax:#x} ebx {c.Ebx:#x} ecx {c.Ecx:#x} edx {c.Edx:#x} esi {c.Esi:#x} edi {c.Edi:#x} ebp {c.Ebp:#x}")
        n = 0
        for i in range(0, len(stack) - 3, 4):
            v = struct.unpack_from("<I", stack, i)[0]
            w = where(mods, v)
            if w and n < 60:
                # a return address follows a call: check the byte(s) before it look like a call
                pre = read(hp, v - 6, 6)
                if len(pre) == 6 and (pre[1] == 0xE8 or pre[0] == 0xFF or pre[4] == 0xFF or pre[3] == 0xFF):
                    print(f"   [esp+{i:#06x}] {w}")
                    n += 1


if __name__ == "__main__":
    main()
