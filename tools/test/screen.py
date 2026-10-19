#!/usr/bin/env python3
"""See the windowed test game in the background, without touching the mouse, keyboard or focus.

    python tools/test/screen.py shot [NAME]        -> PR2_BUILD/shots/NAME.png
    python tools/test/screen.py place [W H]        move the window to the bottom-right corner

Screenshots use PrintWindow, so they work while the window is covered. They are cropped to the
game's own (DPI-virtualised) resolution, so a shot's pixel coordinates are the coordinates
`cmd.py click X Y` takes. For input use cmd.py (gamehost injects it on the game's UI thread);
the game reads DirectInput and ignores window messages, and this tool never sends input.

The game window is found by its process: any running PR2_*.exe (a test exe), never the Steam
PR2.exe. Screenshots show the game's art: keep them for your own use.
"""
import argparse
import ctypes as C
import sys
import time
from ctypes import wintypes as W
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

u = C.WinDLL("user32", use_last_error=True)
g = C.WinDLL("gdi32")
C.windll.shcore.SetProcessDpiAwareness(2)
u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
SHOTS = kit_paths.BUILD / "shots"
SWP_NOACTIVATE, SWP_NOZORDER = 0x10, 0x4


def owner_exe(hwnd):
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    k = C.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    buf, n = C.create_unicode_buffer(520), W.DWORD(520)
    ok = h and k.QueryFullProcessImageNameW(h, 0, buf, C.byref(n))
    if h:
        k.CloseHandle(h)
    return Path(buf.value).name if ok else ""


def is_test_exe(name: str) -> bool:
    n = name.lower()
    return n.startswith("pr2_") and n.endswith(".exe")


def game_window():
    """The test game's top-level window, matched by its process (not by title)."""
    found = []

    @C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and is_test_exe(owner_exe(hwnd)):
            found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    if not found:
        sys.exit("game window not found (no visible window of a PR2_*.exe)")
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
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("action", choices=["shot", "place"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.action == "shot":
        shot(a.args[0] if a.args else None)
    else:
        place(*map(int, a.args))


if __name__ == "__main__":
    main()
