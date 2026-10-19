"""Shared helpers for the live tools: find the running test game, read its memory, start it safely.

Attaching tools (memscan.py, dump_module.py) only read memory. By default they pick the one
running process named PR2_*.exe (the test install's exes); they never pick the Steam PR2.exe,
and refuse any process whose image lives under a Steam library ("steamapps").

The launching tool (crashcatch.py) goes through `game_exe()` + `test_lock()`: the exe must be
inside the test install (PR2_TEST_ENV), not under "steamapps", and not the SteamStub retail exe.
"""
import ctypes as C
import fnmatch
import hashlib
import os
import shutil
import struct
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes as W
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

OUT = kit_paths.BUILD / "live"
DEFAULT_EXE = kit_paths.TEST_EXE                 # setting PR2_TEST_EXE
TEST_PATTERN = "PR2_*.exe"

k = C.WinDLL("kernel32", use_last_error=True)
k.OpenProcess.restype = W.HANDLE
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.CreateToolhelp32Snapshot.restype = W.HANDLE
k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k.CloseHandle.argtypes = [W.HANDLE]
k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k.ReadProcessMemory.restype = W.BOOL
k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
k.QueryFullProcessImageNameW.restype = W.BOOL

PROCESS_VM_READ = 0x10
PROCESS_QUERY_INFORMATION = 0x400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT, MEM_PRIVATE, MEM_IMAGE = 0x1000, 0x20000, 0x1000000
WRITABLE = (0x04, 0x08, 0x40, 0x80)


def outdir(*parts) -> Path:
    p = OUT.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


class PROCESSENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", C.c_size_t), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", C.c_long), ("dwFlags", W.DWORD), ("szExeFile", C.c_wchar * 260)]


class MODULEENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD), ("th32ProcessID", W.DWORD),
                ("GlblcntUsage", W.DWORD), ("ProccntUsage", W.DWORD), ("modBaseAddr", C.c_void_p),
                ("modBaseSize", W.DWORD), ("hModule", C.c_void_p),
                ("szModule", C.c_wchar * 256), ("szExePath", C.c_wchar * 260)]


class MBI(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("_a", W.DWORD), ("RegionSize", C.c_size_t),
                ("State", W.DWORD), ("Protect", W.DWORD), ("Type", W.DWORD), ("_b", W.DWORD)]


k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [W.HANDLE, C.POINTER(PROCESSENTRY32W)]
k.Module32FirstW.argtypes = k.Module32NextW.argtypes = [W.HANDLE, C.POINTER(MODULEENTRY32W)]
k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
k.VirtualQueryEx.restype = C.c_size_t


def processes():
    """[(pid, exe name)] of every running process."""
    snap = k.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    e = PROCESSENTRY32W()
    e.dwSize = C.sizeof(e)
    out = []
    ok = k.Process32FirstW(snap, C.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.szExeFile))
        ok = k.Process32NextW(snap, C.byref(e))
    k.CloseHandle(snap)
    return out


def image_path(pid) -> str:
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    buf = C.create_unicode_buffer(1024)
    n = W.DWORD(len(buf))
    ok = k.QueryFullProcessImageNameW(h, 0, buf, C.byref(n))
    k.CloseHandle(h)
    return buf.value if ok else ""


def _allowed(name, path):
    if name.lower() == "pr2.exe":
        return "it is PR2.exe (the Steam exe name); run the game from the test install"
    if "steamapps" in path.lower():
        return "it runs from a Steam library (steamapps)"
    return None


def pick(process=None, pid=None):
    """-> (pid, name, image path) of the process to attach to (read-only)."""
    procs = processes()
    if pid:
        match = [(p, n) for p, n in procs if p == pid]
    elif process:
        match = [(p, n) for p, n in procs if n.lower() == process.lower()]
    else:
        match = [(p, n) for p, n in procs if fnmatch.fnmatch(n.lower(), TEST_PATTERN.lower())]
    if not match:
        raise SystemExit("no matching process running (%s)" % (pid or process or TEST_PATTERN))
    if len(match) > 1:
        raise SystemExit("several match, pick one with --pid: " + ", ".join(f"{n} pid={p}" for p, n in match))
    p, n = match[0]
    path = image_path(p)
    why = _allowed(n, path)
    if why:
        raise SystemExit(f"refusing to attach to {n} (pid {p}): {why}")
    return p, n, path


def open_read(pid):
    h = k.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        raise SystemExit("OpenProcess(%d) failed err=%d" % (pid, C.get_last_error()))
    return h


def read(h, addr, n):
    """Up to n bytes at addr: when the range runs into an unreadable page, the readable
    prefix (page by page) instead of nothing."""
    buf = (C.c_char * n)()
    got = C.c_size_t()
    if k.ReadProcessMemory(h, C.c_void_p(addr), buf, n, C.byref(got)) or not addr:
        return bytes(buf[:got.value])
    out = b""
    while len(out) < n:
        a = addr + len(out)
        step = min(n - len(out), 0x1000 - (a & 0xFFF))
        if not k.ReadProcessMemory(h, C.c_void_p(a), buf, step, C.byref(got)) or not got.value:
            break
        out += bytes(buf[:got.value])
    return out


def regions(h, private_only=False, cap=32 * 1024 * 1024):
    """[(base, size)] of committed writable regions: private memory (heap, stacks) and,
    unless private_only, the writable sections of loaded modules (their globals). Regions
    over `cap` bytes (textures, sound buffers) are skipped."""
    addr, out, mbi = 0x10000, [], MBI()
    while addr < 0x7FFF0000:
        if not k.VirtualQueryEx(h, C.c_void_p(addr), C.byref(mbi), C.sizeof(mbi)):
            break
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if (mbi.State == MEM_COMMIT and mbi.Protect in WRITABLE and size <= cap
                and (mbi.Type == MEM_PRIVATE or (not private_only and mbi.Type == MEM_IMAGE))):
            out.append((base, size))
        addr = base + size
    return out


def modules(pid):
    """[(base, size, name, path)] via a Toolhelp module snapshot (works for a WOW64 target)."""
    snap = k.CreateToolhelp32Snapshot(0x8 | 0x10, pid)   # SNAPMODULE | SNAPMODULE32
    me = MODULEENTRY32W()
    me.dwSize = C.sizeof(me)
    out = []
    ok = k.Module32FirstW(snap, C.byref(me))
    while ok:
        out.append((me.modBaseAddr or 0, me.modBaseSize, me.szModule, me.szExePath))
        ok = k.Module32NextW(snap, C.byref(me))
    k.CloseHandle(snap)
    return out


# --- starting the game (crashcatch.py) ---------------------------------------------------------
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def game_exe(arg=None) -> Path:
    """The exe to start, checked: inside the test install, not in a Steam library, not the
    SteamStub retail exe."""
    exe = Path(arg) if arg else DEFAULT_EXE
    if not exe.is_absolute():
        exe = kit_paths.TEST_ENV / exe
    exe = exe.resolve()
    if "steamapps" in str(exe).lower():
        raise SystemExit(f"refusing to start {exe}: it is in a Steam library; use the test "
                         "install (PR2_TEST_ENV)")
    env = kit_paths.TEST_ENV.resolve()
    if env not in exe.parents:
        raise SystemExit(f"refusing to start {exe}: it is not inside the test install {env} "
                         "(set PR2_TEST_ENV, or pass --exe with a path inside it)")
    kit_paths.need(exe, "game exe", "PR2_TEST_EXE or --exe")
    if _sha256(exe) == kit_paths.RETAIL_SHA256:
        raise SystemExit(f"refusing to start {exe}: it is the SteamStub retail exe")
    return exe


@contextmanager
def test_lock(what: str, wait: bool = False):
    """Hold the shared test-install lock folder (PR2_TEST_LOCK) for the duration."""
    lock = kit_paths.TEST_LOCK
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                owner = (lock / "owner.txt").read_text().strip()
            except OSError:
                owner = "?"
            if not wait:
                raise SystemExit(f"the test install is in use (lock {lock} held by: {owner}); "
                                 "pass --wait to wait for it")
            print(f"waiting for the test-install lock ({owner}) ...", flush=True)
            time.sleep(30)
        except FileNotFoundError:
            raise SystemExit(f"cannot create the lock {lock}: its parent does not exist (set PR2_TEST_LOCK)")
    try:
        (lock / "owner.txt").write_text(f"{what} (pid {os.getpid()}), {time.ctime()}\n")
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def u32(b, off=0):
    return struct.unpack_from("<I", b, off)[0]
