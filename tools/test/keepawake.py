"""Keep the PC awake while background game tests run.

    python tools/test/keepawake.py [hours]      (default 12)
    python tools/test/keepawake.py [hours] --input

--input also resets the PC's input-idle timer every 50 s with a zero-distance mouse move (the
cursor does not move, no window gets a key). That keeps screen savers that run on their own
idle timer from starting (some OEM tools, for example OLED-care screen savers, ignore the Windows
settings below). Use it only while tests run, and stop it afterwards: an OLED panel then shows a
static picture for that long.

The game's Direct3D 8 cannot start while the screen saver or lock screen is up.
While this runs:
  - SetThreadExecutionState keeps the display and system awake;
  - the screen saver is switched off for this login session only
    (SPI_SETSCREENSAVEACTIVE without SPIF_UPDATEINIFILE: nothing is written
    to the user profile, and a logoff or reboot resets it anyway).
On exit (timeout, Ctrl+C or kill) the previous screen-saver state is restored.
"""
import atexit
import ctypes
import signal
import sys
import time

ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
SPI_GETSCREENSAVEACTIVE, SPI_SETSCREENSAVEACTIVE = 0x10, 0x11

k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32


def screensaver_active():
    v = ctypes.c_int()
    u32.SystemParametersInfoW(SPI_GETSCREENSAVEACTIVE, 0, ctypes.byref(v), 0)
    return bool(v.value)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    hours = float(args[0]) if args else 12
    nudge = "--input" in sys.argv
    was_active = screensaver_active()

    def restore(*_):
        u32.SystemParametersInfoW(SPI_SETSCREENSAVEACTIVE, int(was_active), None, 0)
        k32.SetThreadExecutionState(ES_CONTINUOUS)
        print(f"keepawake: restored (screen saver {'on' if was_active else 'off'})", flush=True)
        sys.exit(0)

    atexit.register(lambda: u32.SystemParametersInfoW(SPI_SETSCREENSAVEACTIVE, int(was_active), None, 0))
    signal.signal(signal.SIGINT, restore)
    signal.signal(signal.SIGTERM, restore)
    signal.signal(signal.SIGBREAK, restore)

    u32.SystemParametersInfoW(SPI_SETSCREENSAVEACTIVE, 0, None, 0)       # session only
    print(f"keepawake: on for {hours:g} h (screen saver was {'on' if was_active else 'off'})", flush=True)
    end = time.time() + hours * 3600
    while time.time() < end:
        k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        if nudge:
            u32.mouse_event(0x0001, 0, 0, 0, 0)                          # MOUSEEVENTF_MOVE by (0, 0)
        time.sleep(50 if nudge else 30)
    restore()


if __name__ == "__main__":
    main()
