#!/usr/bin/env python3
"""See the windowed test game in the background, without touching the mouse, keyboard or focus.

    python tools/test/screen.py shot [NAME]        -> PR2_BUILD/shots/NAME.png
    python tools/test/screen.py place [W H]        move the window to the bottom-right corner
    python tools/test/screen.py click X Y          a left click at shot pixel X, Y (through gamehost)
    python tools/test/screen.py key ESC|ENTER|F5|A... [TIMES]   a key press (through gamehost)

Screenshots use PrintWindow, so they work while the window is covered. They are cropped to the
game's own (DPI-virtualised) resolution, so a shot's pixel coordinates are the coordinates
`cmd.py click X Y` takes. Input goes through gamehost only (cmd.py: the host injects it on the
game's UI thread; the game reads DirectInput and ignores window messages): there are no helpers
that focus the game or move the real cursor, and there must never be any.

The game window is found by its process: a running PR2_*.exe of the test install (setting
PR2_TEST_ENV), never the Steam PR2.exe and never a game of another test install. Screenshots
show the game's art: keep them for your own use.

From Python, AFTER_SHOT is a list of functions called with each new screenshot's path (a mod's
suite can archive them; a failing one is reported, never breaks the test).
"""
import argparse
import ctypes as C
import sys
import time
from ctypes import wintypes as W
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

u = C.WinDLL("user32", use_last_error=True)
g = C.WinDLL("gdi32")
C.windll.shcore.SetProcessDpiAwareness(2)
u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
SHOTS = kit_paths.BUILD / "shots"
SWP_NOACTIVATE, SWP_NOZORDER = 0x10, 0x4
VK = {"ESC": 0x1B, "ENTER": 0x0D, "SPACE": 0x20, "TAB": 0x09, "BACK": 0x08,
      "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
      **{f"F{i}": 0x6F + i for i in range(1, 13)}}
AFTER_SHOT = []             # functions called with each new screenshot's path


def owner_path(hwnd):
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    k = C.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    buf, n = C.create_unicode_buffer(520), W.DWORD(520)
    ok = h and k.QueryFullProcessImageNameW(h, 0, buf, C.byref(n))
    if h:
        k.CloseHandle(h)
    return Path(buf.value) if ok else None


def owner_exe(hwnd):
    p = owner_path(hwnd)
    return p.name if p else ""


def is_test_exe(name: str) -> bool:
    n = name.lower()
    return n.startswith("pr2_") and n.endswith(".exe")


def in_test_env(path) -> bool:
    import os
    return path is not None and os.path.normcase(str(path.parent)) == os.path.normcase(
        str(Path(kit_paths.TEST_ENV).resolve()))


def game_window():
    """The test game's top-level window, matched by its process (not by title: an editor open on
    a folder called Port Royale 2 has that title too), in the test install."""
    found = []

    @C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            p = owner_path(hwnd)
            if p is not None and is_test_exe(p.name) and in_test_env(p):
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    if not found:
        sys.exit(f"game window not found (no visible window of a PR2_*.exe in {kit_paths.TEST_ENV})")
    return found[0]


def client_size(hwnd):
    r = W.RECT()
    u.GetClientRect(hwnd, C.byref(r))
    return r.right, r.bottom


def scale(hwnd):
    """Physical pixels per game pixel: >1 only when Windows DPI-virtualises the game."""
    u.GetWindowDpiAwarenessContext.restype = C.c_void_p
    u.GetAwarenessFromDpiAwarenessContext.argtypes = [C.c_void_p]
    aware = u.GetAwarenessFromDpiAwarenessContext(u.GetWindowDpiAwarenessContext(hwnd))
    return u.GetDpiForSystem() / 96 if aware == 0 else 1


def place(w=1152, h=720):
    hwnd = game_window()
    mon = W.RECT()
    u.SystemParametersInfoW(0x30, 0, C.byref(mon), 0)          # work area
    u.SetWindowPos(hwnd, None, mon.right - w, mon.bottom - h, w, h, SWP_NOACTIVATE | SWP_NOZORDER)
    print("client", client_size(hwnd))


def shot(name=None):
    from PIL import Image
    hwnd = game_window()
    w, h = client_size(hwnd)
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 1 | 2)          # client only, render full (DirectX) content
    buf = C.create_string_buffer(w * h * 4)
    bmi = (C.c_uint32 * 10)(40, w, -h, 0x200001, 0, 0, 0, 0, 0, 0)  # BITMAPINFOHEADER, 32bpp top-down
    g.GetDIBits(mdc, bmp, 0, h, buf, bmi, 0)
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd, hdc)
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name or time.strftime('%H%M%S')}.png"
    s = scale(hwnd)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.crop((0, 0, int(w / s), int(h / s))).save(path)
    print(path, (int(w / s), int(h / s)))
    for hook in AFTER_SHOT:
        try:
            hook(path)
        except Exception as e:                  # a hook (an archive) must never break a test
            print(f"after-shot hook {getattr(hook, '__name__', hook)} failed: {e}")
    return path


def _host(*args):
    """One command to the running gamehost (background input: cmd.py)."""
    import subprocess
    out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "cmd.py"), *map(str, args)],
                         capture_output=True, text=True, errors="replace")
    return out.stdout + out.stderr


def click(x, y):
    """Left click at client pixel (x, y) of a shot, through gamehost (no focus, no real mouse)."""
    return _host("click", int(x), int(y))


def key(name, times=1):
    """A key press through gamehost (no focus): ESC, ENTER, F5, ... or a letter."""
    vk = VK[name] if name in VK else ord(name.upper())
    return _host("key", hex(vk), int(times))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("action", choices=["shot", "place", "click", "key"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.action == "shot":
        shot(a.args[0] if a.args else None)
    elif a.action == "place":
        place(*map(int, a.args))
    elif a.action == "click":
        if len(a.args) != 2:
            ap.error("click X Y")
        print(click(int(a.args[0]), int(a.args[1])), end="")
    else:
        if not a.args:
            ap.error("key NAME [TIMES]")
        print(key(a.args[0], int(a.args[1]) if len(a.args) > 1 else 1), end="")


if __name__ == "__main__":
    main()
