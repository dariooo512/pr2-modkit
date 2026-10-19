"""Launch the game under a debugger: classify every exception, log breakpoints.

    python tools/test/gamehost.py [--exe PR2_target.exe]
        [--bp VA=read,read,...] ...  [--max-hits N]

Starts an exe of the test install (setting PR2_TEST_ENV; --exe is a file name in it, default
the unmodified DRM-free PR2_target.exe) under a debugger. It refuses PR2.exe (the Steam build
cannot start without Steam) and anything inside a Steam library.

A read is `reg[+off]:len` (bytes at that address) or `reg` (register value).
Example, watching AddToNation write a town's nation and display bytes:
    --bp 0x5C9DD7=esi,esi+0x358:2,esp+0x1c:4

Writes PR2_BUILD/runs/<timestamp>/gamehost.log. Every exception
is tagged FIRST (the game may still handle it) or SECOND (unhandled = a real
crash); C++ exceptions get their thrown type name decoded. Process exit code is
logged, so "the game closed" and "the game crashed" can't be confused.

The game window is kept at 1280x800 in the bottom-right corner of the screen from its first
frame (hooks on CreateWindowEx/SetWindowPos/MoveWindow) and is started without activation.
Input and function calls are injected on the game's UI thread through A2dLib's own handlers
(cmd.py click/key/call), so the real mouse, keyboard and focus are never used. Switch the game
to windowed mode first (env.py windowed on): a full-screen start takes over the screen.
"""
import argparse
import ctypes as C
import struct
import sys
import time
from ctypes import wintypes as W
from pathlib import Path

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

C.windll.shcore.SetProcessDpiAwareness(2)   # physical pixels, like the game will use
k = C.WinDLL("kernel32", use_last_error=True)
k.OpenThread.restype = W.HANDLE
k.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]

DEBUG_ONLY_THIS_PROCESS = 0x2
EXCEPTION, CREATE_THREAD, CREATE_PROCESS, EXIT_THREAD, EXIT_PROCESS, LOAD_DLL = 1, 2, 3, 4, 5, 6
DBG_CONTINUE, DBG_NOT_HANDLED = 0x00010002, 0x80010001
BP_CODES = {0x80000003, 0x4000001F}        # native / WOW64 int3
SS_CODES = {0x80000004, 0x4000001E}        # native / WOW64 single step
CXX = 0xE06D7363
THREAD_ALL_ACCESS = 0x1FFFFF
WOW64_CONTEXT_FULL = 0x10007
TRAP_FLAG = 0x100


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
                    ("LoadDll", LOAD_DLL_DEBUG_INFO), ("ExitProcess", EXIT_PROCESS_DEBUG_INFO)]
    _fields_ = [("Code", W.DWORD), ("Pid", W.DWORD), ("Tid", W.DWORD), ("u", _U)]


class WOW64_CONTEXT(C.Structure):
    _fields_ = [("ContextFlags", W.DWORD), ("Dr", W.DWORD * 6), ("FloatSave", C.c_byte * 112),
                ("SegGs", W.DWORD), ("SegFs", W.DWORD), ("SegEs", W.DWORD), ("SegDs", W.DWORD),
                ("Edi", W.DWORD), ("Esi", W.DWORD), ("Ebx", W.DWORD), ("Edx", W.DWORD),
                ("Ecx", W.DWORD), ("Eax", W.DWORD), ("Ebp", W.DWORD), ("Eip", W.DWORD),
                ("SegCs", W.DWORD), ("EFlags", W.DWORD), ("Esp", W.DWORD), ("SegSs", W.DWORD),
                ("Ext", C.c_byte * 512)]


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", W.DWORD), ("r", W.LPWSTR), ("desk", W.LPWSTR), ("title", W.LPWSTR),
                ("pos", W.DWORD * 7), ("flags", W.DWORD), ("show", W.WORD), ("cbR", W.WORD),
                ("lpR", C.c_void_p), ("hIn", W.HANDLE), ("hOut", W.HANDLE), ("hErr", W.HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("Pid", W.DWORD), ("Tid", W.DWORD)]


CMD_FILE = kit_paths.BUILD / "gamehost.cmd"
RUNS = kit_paths.BUILD / "runs"
REGS = ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "eip")
TOWN_LAGER_VTABLE = 0  # filled in once observed; pass it to `towns` until then


class Breakpoint:
    def __init__(self, spec, fn=None):
        va, _, reads = spec.partition("=")
        self.va = int(va, 16)
        self.reads = [r for r in reads.split(",") if r]
        self.orig = None
        self.hits = 0
        self.fn = fn          # callback(ctx) instead of logging; never expires


class Host:
    def __init__(self, exe, bps, max_hits, log):
        self.exe, self.bps, self.max_hits, self.log = exe, {b.va: b for b in bps}, max_hits, log
        self.hproc = None
        self.mods = []            # (base, name)
        self.rearm = {}           # tid -> bp va to re-insert after single step
        self.first_seen = {}      # (code, addr, type) -> count

    # --- memory ---------------------------------------------------------------
    def read(self, addr, n):
        buf, got = C.create_string_buffer(n), C.c_size_t()
        if not k.ReadProcessMemory(self.hproc, C.c_void_p(addr), buf, n, C.byref(got)):
            return None
        return buf.raw[:got.value]

    def write(self, addr, data):
        old = W.DWORD()
        k.VirtualProtectEx(self.hproc, C.c_void_p(addr), len(data), 0x40, C.byref(old))
        k.WriteProcessMemory(self.hproc, C.c_void_p(addr), data, len(data), None)
        k.VirtualProtectEx(self.hproc, C.c_void_p(addr), len(data), old.value, C.byref(old))
        k.FlushInstructionCache(self.hproc, C.c_void_p(addr), len(data))

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else None

    def cstr(self, addr, n=96):
        b = self.read(addr, n)
        return b.split(b"\0")[0].decode("latin1") if b else "?"

    def where(self, addr):
        best = None
        for base, name in self.mods:
            if base <= addr and (best is None or base > best[0]):
                best = (base, name)
        return f"{best[1]}+{addr - best[0]:#x}" if best else f"{addr:#x}"

    # --- threads --------------------------------------------------------------
    def ctx(self, tid):
        h = k.OpenThread(THREAD_ALL_ACCESS, False, tid)
        c = WOW64_CONTEXT()
        c.ContextFlags = WOW64_CONTEXT_FULL
        k.Wow64GetThreadContext(h, C.byref(c))
        return h, c

    def set_ctx(self, h, c):
        k.Wow64SetThreadContext(h, C.byref(c))
        k.CloseHandle(h)

    # --- breakpoints ------------------------------------------------------------
    def arm(self, bp):
        if bp.orig is None:
            bp.orig = self.read(bp.va, 1)
        self.write(bp.va, b"\xCC")

    def eval_read(self, c, spec):
        if spec.startswith(("str1@", "str2@")):           # C string via 1 or 2 pointer hops
            hops, expr = int(spec[3]), spec[5:]
            reg, _, off = expr.partition("+")
            p = getattr(c, reg.capitalize()) + (int(off, 16) if off else 0)
            for _ in range(hops):
                p = self.u32(p) or 0
            return f"{spec}='{self.cstr(p, 80) if p else ''}'"
        expr, _, n = spec.partition(":")
        reg, _, off = expr.partition("+")
        val = getattr(c, reg.capitalize())
        if not n:
            return f"{reg}={val:#x}"
        addr = val + (int(off, 16) if off else 0)
        data = self.read(addr, int(n))
        return f"[{expr}]={data.hex() if data else '??'}"

    def on_bp(self, tid, va):
        bp = self.bps[va]
        h, c = self.ctx(tid)
        bp.hits += 1
        if bp.fn:
            bp.fn(c)
        else:
            self.log(f"BP {va:#x} hit#{bp.hits} tid={tid} " + " ".join(self.eval_read(c, r) for r in bp.reads))
        self.write(va, bp.orig)
        c.Eip = va
        if bp.fn or bp.hits < self.max_hits:
            c.EFlags |= TRAP_FLAG
            self.rearm[tid] = va
        else:
            self.log(f"BP {va:#x} reached max hits ({self.max_hits}); disarmed")
        self.set_ctx(h, c)

    # --- background input: call the UI library's Win32 input handlers ------------
    # CWalhallaClient::InputProcessor runs once per frame on the UI thread; when
    # input is queued we break there, call MsgMouseMove / MsgLButtonDown / ...
    # on that thread (return lands on a padding int3 = TRAP), then restore the
    # thread exactly. One queued "frame" of calls per InputProcessor pass.
    A2D_BASE = 0x10000000
    A2D = {"hook": 0x1002F2C0, "trap": 0x10021F20, "move": 0x1002DED0, "wheel": 0x1002DEE0,
           "ldown": 0x1002DE40, "lup": 0x1002DE50, "kdown": 0x1002DEF0, "kup": 0x1002DF00}

    def on_load_a2d(self, base):
        self.a2d = {k_: v - self.A2D_BASE + base for k_, v in self.A2D.items()}
        self.inject_frames, self.inject_state, self.hook_orig = [], None, None

    def queue_input(self, frames):
        if not getattr(self, "a2d", None):
            raise RuntimeError("A2dLib not loaded yet")
        self.inject_frames.extend(frames)
        self.arm_hook()

    def arm_hook(self):
        if self.hook_orig is None:
            self.hook_orig = self.read(self.a2d["hook"], 1)
        self.write(self.a2d["hook"], b"\xCC")

    def on_hook(self, tid):
        h, c = self.ctx(tid)
        self.write(self.a2d["hook"], self.hook_orig)          # disarm while we work
        saved = WOW64_CONTEXT.from_buffer_copy(bytes(c))
        saved.Eip = self.a2d["hook"]
        self.inject_state = [tid, saved, list(self.inject_frames.pop(0))]
        self.next_call(h)

    def next_call(self, h):
        tid, saved, calls = self.inject_state
        if not calls:                                          # frame done: resume the game
            done = WOW64_CONTEXT.from_buffer_copy(bytes(saved))
            if self.inject_frames:                             # more input next frame
                done.EFlags |= TRAP_FLAG
                self.rearm_hook = tid
            self.inject_state = None
            self.set_ctx(h, done)
            return
        name, args, *this = calls.pop(0)
        c = WOW64_CONTEXT.from_buffer_copy(bytes(saved))
        esp = (saved.Esp - 0x800) & ~0xF
        data, words = esp + 0x100, []
        for a in args:
            if isinstance(a, bytes):                 # by-pointer blob
                self.write(data, a)
                words.append(data)
                data += (len(a) + 3) & ~3
            elif isinstance(a, str):                 # pointer to an STLport std::string {p, end, cap}
                text = a.encode("latin1") + b"\0"
                self.write(data, text)
                p, n = data, len(text) - 1
                data += (len(text) + 3) & ~3
                self.write(data, struct.pack("<III", p, p + n, p + n + 1))
                words.append(data)
                data += 12
            else:
                words.append(a & 0xFFFFFFFF)
        self.write(esp, struct.pack(f"<{1 + len(words)}I", self.a2d["trap"], *words))
        target = self.a2d[name] if isinstance(name, str) else name
        c.Esp, c.Eip = esp, target
        c.Ecx = this[0] if this else saved.Ecx       # default: the CWalhallaClient
        self.inject_state.append(target)
        self.set_ctx(h, c)

    def cmd_click(self, x, y):
        pt = struct.pack("<ii", int(x), int(y))
        self.queue_input([[("move", [pt])], [("ldown", [pt])], [("lup", [pt])]])

    def cmd_call(self, va, this, *args):
        """Call a game function on the UI thread: call 0xVA THIS [ARG...]
        THIS: 0xADDR, 0 or `menu` (the main-menu controller *(*0x6E5958+0xE8)).
        ARG: integer, or s:Name_With_Underscores for a const std::string* argument."""
        if this == "menu":
            this = self.u32(self.u32(0x6E5958) + 0xE8)
        else:
            this = int(this, 0)
        conv = [a[2:].replace("_", " ") if a.startswith("s:") else int(a, 0) for a in args]
        self.log(f"  queue call {int(va, 16):#x} this={this:#x} args={conv}")
        self.queue_input([[(int(va, 16), conv, this)]])

    def cmd_move(self, x, y):
        self.queue_input([[("move", [struct.pack("<ii", int(x), int(y))])]])

    def cmd_wheel(self, x, y, delta="-120", times="1"):
        """Mouse wheel over (x, y): CW32WalhallaClient::MsgWheel(const CPoint&, short); -120 = one notch down."""
        pt = struct.pack("<ii", int(x), int(y))
        frames = [[("move", [pt])]]
        for _ in range(int(times)):
            frames.append([("wheel", [pt, int(delta) & 0xFFFF])])
        self.queue_input(frames)

    def cmd_key(self, vk, times="1"):
        vk = int(vk, 0)
        scan = C.windll.user32.MapVirtualKeyW(vk, 0)
        for _ in range(int(times)):
            self.queue_input([[("kdown", [vk, 1, scan])], [("kup", [vk, 1, scan | 0xC000])]])

    # --- keep the game window in the bottom-right corner from its first frame ----
    def on_load_user32(self, base, path):
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        rva = {e.name.decode(): e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
        hooks = {"CreateWindowExA": self.hook_create, "CreateWindowExW": self.hook_create,
                 "SetWindowPos": self.hook_setpos, "MoveWindow": self.hook_move}
        for name, fn in hooks.items():
            bp = Breakpoint(f"{base + rva[name]:#x}", fn)
            self.bps[bp.va] = bp
            self.arm(bp)
        self.log("window placement hooks armed (game window stays bottom-right)")

    def on_load_kernel32(self, base, path):
        """Log who calls ExitProcess (e.g. Lua's exit(1) on an unprotected error)."""
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        rva = {e.name.decode(): e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
        bp = Breakpoint(f"{base + rva['ExitProcess']:#x}", self.hook_exit)
        self.bps[bp.va] = bp
        self.arm(bp)

    def hook_exit(self, c):
        stack = self.read(c.Esp, 0x200) or b""
        frames = [self.where(v) for (v,) in struct.iter_unpack("<I", stack[:len(stack) & ~3])
                  if any(b <= v < b + 0x1000000 for b, _ in self.mods)]
        self.log(f"ExitProcess(code={self.u32(c.Esp + 4)}) stack: {' '.join(frames[:24])}")

    WINDOW = (1280, 800)    # outer size of the game window, physical pixels

    def corner(self, w, h):
        """Bottom-right of the work area for a window of the fixed WINDOW size."""
        w, h = self.WINDOW
        wa = W.RECT()
        C.windll.user32.SystemParametersInfoW(0x30, 0, C.byref(wa), 0)
        return wa.right - w, wa.bottom - h

    def arg(self, c, i):
        return self.u32(c.Esp + 4 * i)

    def set_arg(self, c, i, v):
        self.write(c.Esp + 4 * i, struct.pack("<i", v))

    def hook_create(self, c):   # (exstyle, class, name, style, X, Y, W, H, parent, ...)
        w, h, parent = (self.arg(c, i) for i in (7, 8, 9))
        if parent == 0 and w not in (None, 0x80000000) and w > 200:
            x, y = self.corner(w, h)
            self.set_arg(c, 5, x); self.set_arg(c, 6, y)
            self.set_arg(c, 7, self.WINDOW[0]); self.set_arg(c, 8, self.WINDOW[1])

    def hook_setpos(self, c):   # (hwnd, after, X, Y, cx, cy, flags)
        hwnd, cx, cy, flags = (self.arg(c, i) for i in (1, 5, 6, 7))
        if (flags & 0x3) == 0x3 or C.windll.user32.GetParent(hwnd):  # z-order only / child
            return
        self.set_arg(c, 7, flags & ~0x2)                         # we always set the position
        x, y = self.corner(cx, cy)
        self.set_arg(c, 3, x); self.set_arg(c, 4, y)
        self.set_arg(c, 5, self.WINDOW[0]); self.set_arg(c, 6, self.WINDOW[1])
        self.set_arg(c, 7, flags & ~0x1)                         # we always set the size

    def hook_move(self, c):     # (hwnd, X, Y, W, H, repaint)
        hwnd, w, h = (self.arg(c, i) for i in (1, 4, 5))
        if not C.windll.user32.GetParent(hwnd):
            x, y = self.corner(w, h)
            self.set_arg(c, 2, x); self.set_arg(c, 3, y)
            self.set_arg(c, 4, self.WINDOW[0]); self.set_arg(c, 5, self.WINDOW[1])

    # --- exceptions ---------------------------------------------------------------
    def cxx_type(self, rec):
        try:
            throw_info = rec.Info[2] & 0xFFFFFFFF
            cta = self.u32(throw_info + 12)
            first = self.u32(cta + 4)
            tdesc = self.u32(first + 4)
            return self.cstr(tdesc + 8)
        except TypeError:
            return "?"

    def on_exception(self, ev):
        rec, first = ev.u.Exception.Record, ev.u.Exception.FirstChance
        code, addr = rec.Code, rec.Address or 0
        a2d = getattr(self, "a2d", None)
        if a2d and code in BP_CODES and addr == a2d["hook"] and self.inject_frames:
            self.on_hook(ev.Tid)
            return DBG_CONTINUE
        if a2d and code in BP_CODES and addr == a2d["trap"] and self.inject_state \
                and self.inject_state[0] == ev.Tid:
            h, c = self.ctx(ev.Tid)
            if len(self.inject_state) > 3 and not isinstance(self.inject_state[-1], str):
                target = self.inject_state.pop()
                if target not in self.a2d.values():
                    self.log(f"CALL {target:#x} returned eax={c.Eax:#x}")
            self.next_call(h)
            return DBG_CONTINUE
        if code in SS_CODES and getattr(self, "rearm_hook", None) == ev.Tid:
            self.rearm_hook = None
            self.arm_hook()
            return DBG_CONTINUE
        if code in BP_CODES and addr in self.bps:
            self.on_bp(ev.Tid, addr)
            return DBG_CONTINUE
        if code in BP_CODES:                      # loader breakpoints
            if not getattr(self, "_armed", False) and code == 0x4000001F:
                for bp in self.bps.values():
                    self.arm(bp)
                self._armed = True
                self.log(f"armed {len(self.bps)} breakpoint(s)")
            return DBG_CONTINUE
        if code in SS_CODES and ev.Tid in self.rearm:
            bp = self.bps[self.rearm.pop(ev.Tid)]
            self.arm(bp)
            return DBG_CONTINUE
        what = self.cxx_type(rec) if code == CXX else ""
        if first:
            key = (code, addr, what)
            self.first_seen[key] = self.first_seen.get(key, 0) + 1
            if self.first_seen[key] <= 3:
                self.log(f"FIRST  {code:#010x} at {self.where(addr)} {what}")
            return DBG_NOT_HANDLED
        h, c = self.ctx(ev.Tid)
        k.CloseHandle(h)
        regs = " ".join(f"{r}={getattr(c, r.capitalize()):08x}" for r in REGS)
        self.log(f"SECOND {code:#010x} at {self.where(addr)} {what}  <-- UNHANDLED CRASH")
        self.log(f"       {regs}")
        # Likely return addresses: stack dwords that point into the game's .text
        # right after a call instruction.
        stack = self.read(c.Esp, 0x400) or b""
        rets = []
        for (v,) in struct.iter_unpack("<I", stack[:len(stack) & ~3]):
            if 0x401000 <= v < 0x67F000:
                prev = self.read(v - 5, 5) or b""
                if prev[:1] == b"\xE8" or (self.read(v - 6, 2) or b"")[:1] == b"\xFF":
                    rets.append(f"{v:#x}")
        self.log(f"       call stack (likely returns): {' '.join(rets[:16])}")
        return DBG_NOT_HANDLED

    # --- live commands (see cmd.py) ----------------------------------------------
    def poll_commands(self):
        if not (self.cmd_file.exists() and self.hproc):
            return
        try:                                          # a cloud-sync client may hold the file for a moment
            lines = self.cmd_file.read_text().splitlines()
            self.cmd_file.unlink()
        except OSError:
            return                                    # try again at the next poll
        for line in filter(None, (l.strip() for l in lines)):
            self.log(f"CMD {line}")
            try:
                op, *args = line.split()
                getattr(self, f"cmd_{op}")(*args)
            except Exception as e:  # a bad command must never kill the session
                self.log(f"CMD error: {e!r}")
        self.done_file.write_text(str(time.time()))

    def regions(self):
        mbi = (C.c_byte * 48)()
        addr = 0x10000
        while addr < 0x7FFF0000:
            if not k.VirtualQueryEx(self.hproc, C.c_void_p(addr), mbi, 48):
                break
            base, size = struct.unpack_from("<QQ", mbi, 0)[0], struct.unpack_from("<Q", mbi, 24)[0]
            state, protect, typ = struct.unpack_from("<III", mbi, 32)
            if state == 0x1000 and typ == 0x20000 and protect in (0x04, 0x40):  # private RW
                yield base, size
            addr = base + size

    def cmd_bp(self, spec):
        """Arm a logging breakpoint at runtime: bp 0xVA=reg,reg+off:len,..."""
        bp = Breakpoint(spec)
        old = self.bps.get(bp.va)
        if old is not None:            # re-arm: keep the true original byte, reset the count
            bp.orig = old.orig
        self.bps[bp.va] = bp
        self.arm(bp)

    def cmd_read(self, addr, n="64"):
        data = self.read(int(addr, 16), int(n, 0))
        self.log(f"  {int(addr, 16):#x}: {data.hex(' ') if data else '??'}")

    def cmd_write(self, addr, hexbytes):
        self.write(int(addr, 16), bytes.fromhex(hexbytes))
        self.cmd_read(addr, str(len(hexbytes) // 2))

    def cmd_towns(self, vtable=f"{TOWN_LAGER_VTABLE:#x}"):
        """Find every CStadtLager by its vtable; print nation bytes and the town name."""
        vt = struct.pack("<I", int(vtable, 16))
        found = []
        for base, size in self.regions():
            data = self.read(base, size) or b""
            i = data.find(vt)
            while i != -1:
                if i % 4 == 0 and i + 0x35A <= len(data):
                    nat, disp = data[i + 0x358], data[i + 0x359]
                    if nat in (0, 1, 2, 3, 4, 5, 0xFF):
                        found.append((base + i, nat, disp))
                i = data.find(vt, i + 1)
        for addr, nat, disp in found:
            self.log(f"  town lager={addr:#x} nation={nat} display={disp} name={self.town_name(addr)}")
        self.log(f"  {len(found)} town object(s)")

    def town_name(self, lager):
        # The town (CStadt) starts 0x38 before its CStadtLager; take the first
        # pointer in its header that points at a plausible name string.
        head = self.read(lager - 0x38, 0x38) or b""
        for off in range(0, len(head), 4):
            p = struct.unpack_from("<I", head, off)[0]
            s = self.cstr(p, 32) if 0x10000 < p < 0x7FFF0000 else ""
            if 3 <= len(s) <= 24 and s.isprintable() and s[:1].isupper():
                return f"{s} (CStadt+{off:#x})"
        return "?"

    def cmd_setnation(self, lager, nation, display):
        a = int(lager, 16) + 0x358
        self.write(a, bytes([int(nation), int(display)]))
        self.cmd_read(f"{a:#x}", "2")

    def cmd_playertown(self, lager, display):
        """Vanilla 'Your Own Town' state (0x61BDE0 + 0x624D30): nation byte 5,
        display power, and flag bits 0x600000 at CStadtLager+8 (bit 22 = player town)."""
        a = int(lager, 16)
        flags = self.u32(a + 8)
        self.write(a + 8, struct.pack("<I", flags | 0x600000))
        self.cmd_setnation(lager, "5", display)
        self.log(f"  flags {flags:#010x} -> {flags | 0x600000:#010x}")

    def modpath(self, hfile):
        buf = C.create_unicode_buffer(520)
        if hfile and k.GetFinalPathNameByHandleW(hfile, buf, 520, 0):
            return buf.value
        return "?"

    def run(self):
        si, pi = STARTUPINFO(), PROCESS_INFORMATION()
        si.cb = C.sizeof(si)
        si.flags, si.show = 0x1, 4          # STARTF_USESHOWWINDOW, SW_SHOWNOACTIVATE
        if not k.CreateProcessW(str(self.exe), None, None, None, False, DEBUG_ONLY_THIS_PROCESS,
                                None, str(self.exe.parent), C.byref(si), C.byref(pi)):
            self.log(f"CreateProcess failed: {C.get_last_error()}")
            return 2
        self.log(f"launched pid={pi.Pid} {self.exe}")
        ev = DEBUG_EVENT()
        t0 = time.time()
        while True:
            if not k.WaitForDebugEvent(C.byref(ev), 100):
                self.poll_commands()
                continue
            status = DBG_CONTINUE
            if ev.Code == CREATE_PROCESS:
                self.hproc = ev.u.CreateProcess.hProcess
                self.mods.append((ev.u.CreateProcess.Base or 0x400000, self.exe.name))
                k.CloseHandle(ev.u.CreateProcess.hFile)
            elif ev.Code == LOAD_DLL:
                path = self.modpath(ev.u.LoadDll.hFile)
                name = Path(path).name
                self.mods.append((ev.u.LoadDll.Base, name))
                if name.lower() == "a2dlib-2.20a.dll":
                    self.on_load_a2d(ev.u.LoadDll.Base)
                if path.lower().endswith(r"syswow64\kernel32.dll"):
                    self.on_load_kernel32(ev.u.LoadDll.Base, path.removeprefix("\\\\?\\"))
                if path.lower().endswith(r"syswow64\user32.dll"):
                    self.on_load_user32(ev.u.LoadDll.Base, path.removeprefix("\\\\?\\"))
                k.CloseHandle(ev.u.LoadDll.hFile)
            elif ev.Code == EXCEPTION:
                status = self.on_exception(ev)
            elif ev.Code == EXIT_PROCESS:
                code = ev.u.ExitProcess.ExitCode
                self.log(f"EXIT code={code:#x} after {time.time() - t0:.0f}s")
                self.log("first-chance summary: " + ", ".join(
                    f"{c:#x}@{self.where(a)} {t} x{n}" for (c, a, t), n in self.first_seen.items()))
                k.ContinueDebugEvent(ev.Pid, ev.Tid, status)
                return 0
            k.ContinueDebugEvent(ev.Pid, ev.Tid, status)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--exe", default="PR2_target.exe", help="exe file name in the test install")
    ap.add_argument("--bp", action="append", default=[], help="VA=read,read,... (repeatable)")
    ap.add_argument("--max-hits", type=int, default=200, help="disarm a breakpoint after N hits")
    a = ap.parse_args()
    exe = kit_paths.TEST_ENV / a.exe
    if exe.name.lower() == "pr2.exe" or any(p.lower() == "steamapps" for p in exe.resolve().parts):
        sys.exit(f"refusing to start {exe}: use a test exe in a test install (env.py)")
    if not exe.exists():
        sys.exit(f"no {exe}; run env.py setup / deploy first")
    run_dir = RUNS / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    logf = open(run_dir / "gamehost.log", "w", buffering=1)

    def log(m):
        line = f"{time.strftime('%H:%M:%S')} {m}"
        logf.write(line + "\n")
        print(line, flush=True)

    log(f"run dir {run_dir}")
    host = Host(exe, [Breakpoint(s) for s in a.bp], a.max_hits, log)
    host.cmd_file, host.done_file = CMD_FILE, CMD_FILE.with_suffix(".done")
    CMD_FILE.unlink(missing_ok=True)
    return host.run()



if __name__ == "__main__":
    sys.exit(main())
