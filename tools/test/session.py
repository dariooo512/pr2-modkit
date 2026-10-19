#!/usr/bin/env python3
"""Scripted game session in the background: start the game under gamehost, start a free-play
game or load a save, and leave it running for cmd.py / console.py / screen.py.

    python tools/test/session.py newgame [--exe PR2_target.exe] [--bp SPEC ...] [--max-hits N]
    python tools/test/session.py load "Quick Save" [--exe ...] [--bp ...]
    python tools/test/session.py stop                       end the game started by gamehost

Before it starts the game it switches the test settings on (windowed, intro videos parked,
no DPI doubling on HiDPI screens) and starts mute_game.ps1 -Watch so the game makes no sound.
It refuses to start while the PC shows the screen saver or the lock screen (Direct3D 8 cannot
start then). `newgame` writes the 60 towns (index = Data\\staedte.ini order, with names, from the
extracted archives, PR2_WORKDIR) to PR2_BUILD/towns.tsv.

Back up the saves first (env.py backup-saves), and take the test lock (testlock.py) if anything
else may use the same test install. The click coordinates assume the 1280x800 window gamehost
keeps; the free-play start was measured on the 1.1.2.3 main menu.
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402
from gamehost import RUNS  # noqa: E402

TOWN_VTABLE = "0x6a6bc0"    # CStadtLager vtable in 1.1.2.3
MENU_FREEPLAY = (1045, 65)  # 1280x800 window coordinates
SETUP_START = (1045, 65)
WELCOME_OK = (605, 519)
MENU_LOAD_FN = "0x526470"   # main-menu controller: load the save named by a std::string
PID_FILE = env.BUILD / "gamehost.pid"


def latest_log():
    return max(RUNS.glob("*/gamehost.log"))


def wait_for(pattern, after=0, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        text = latest_log().read_text(errors="replace")[after:]
        if re.search(pattern, text):
            return text
        time.sleep(0.5)
    raise SystemExit(f"timeout waiting for {pattern!r}")


_host = None          # the running gamehost.py process (launch)


def host_alive():
    return _host is None or _host.poll() is None


def cmd(*args):
    """Send one command to gamehost; fail at once once the host has died."""
    if not host_alive():
        raise SystemExit(f"gamehost is gone (exit {_host.returncode}); see {env.BUILD / 'host_stderr.txt'}")
    out = subprocess.run([sys.executable, str(HERE / "cmd.py"), *map(str, args)],
                         capture_output=True, text=True, errors="replace")
    if "no reply in 60s" in (out.stdout + out.stderr) and not host_alive():
        raise SystemExit(f"gamehost died during '{' '.join(map(str, args))}'")
    return out.stdout


def town_names():
    txt = (env.kit_paths.WORKDIR / "arcs" / "Data" / "staedte.ini").read_text("latin1")
    rows = []
    for name, body in re.findall(r"^\[([^\]]+)\]([^\[]*)", txt, re.M):
        m = re.search(r"^\s*Nation\s*=\s*(\w+)", body, re.M)
        if m and m.group(1) in ("England", "Frankreich", "Holland", "Spanien"):
            rows.append(name)
    return rows


def input_desktop():
    """'Default' normally; 'Screen-saver' or 'Winlogon' when the PC is idle or locked."""
    import ctypes
    u = ctypes.windll.user32
    h = u.OpenInputDesktop(0, False, 0x0100)
    if not h:
        return "Winlogon"
    buf, n = ctypes.create_unicode_buffer(256), ctypes.c_ulong()
    u.GetUserObjectInformationW(h, 2, buf, 512, ctypes.byref(n))
    u.CloseDesktop(h)
    return buf.value


def launch(bps, exe=None, max_hits=None):
    """Start the game under gamehost and wait until the engine is up (main menu)."""
    env.windowed(True)
    env.fast_boot(True)
    env.dpi_aware(True)
    env.mute_sound()
    args = [sys.executable, str(HERE / "gamehost.py")] + (["--exe", exe] if exe else []) \
        + (["--max-hits", str(max_hits)] if max_hits else [])
    for b in bps:
        args += ["--bp", b]
    desk = input_desktop()
    if desk != "Default":
        raise SystemExit(f"the PC's input desktop is {desk!r} (screen saver or locked): "
                         "the game's Direct3D cannot start; rerun when the PC is in use")
    global _host
    env.BUILD.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        err = open(env.BUILD / "host_stderr.txt", "ab")
        start = time.time()
        host = _host = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err)
        PID_FILE.write_text(str(host.pid))
        time.sleep(2)
        while time.time() - start < 30 and not any(
                p.stat().st_mtime >= start - 1 for p in RUNS.glob("*/gamehost.log")):
            time.sleep(0.5)
        # The engine is up when it throws (and catches) its harmless start-up font exception.
        text = wait_for(r"PAVCGenericException|EXIT code=")
        if "PAVCGenericException" in text:
            time.sleep(4)
            return host
        print(f"game exited during start-up (attempt {attempt + 1}); retrying")
        try:
            host.wait(timeout=30)
        except subprocess.TimeoutExpired:
            host.kill()
        time.sleep(5)
    raise SystemExit("game failed to start 3 times")


def scan_towns():
    out = cmd("towns", TOWN_VTABLE)
    return re.findall(r"lager=(0x[0-9a-f]+) nation=(\d+) display=(\d+)", out)


def newgame(bps, exe=None, max_hits=None):
    host = launch(bps, exe, max_hits)
    cmd("click", *MENU_FREEPLAY); time.sleep(3)
    cmd("click", *SETUP_START)
    time.sleep(40)                                  # world generation + town load
    cmd("click", *WELCOME_OK); time.sleep(1)
    lagers = scan_towns()
    try:
        names = town_names()
    except OSError:
        names = [f"town{i}" for i in range(len(lagers))]
    if len(lagers) != len(names):
        print(f"warning: found {len(lagers)} towns, expected {len(names)}")
    tsv = env.BUILD / "towns.tsv"
    tsv.write_text("idx\tname\tlager\tnation\tdisplay\n" + "".join(
        f"{i}\t{n}\t{a}\t{nat}\t{d}\n" for i, (n, (a, nat, d)) in enumerate(zip(names, lagers))))
    print(f"game running (gamehost pid {host.pid}); towns -> {tsv}")


def load(name, bps, exe=None):
    """Launch, load the save `name` from the main menu, wait for the world."""
    host = launch(bps, exe)
    cmd("call", MENU_LOAD_FN, "menu", "s:" + name.replace(" ", "_"))
    time.sleep(45)
    print(f"loaded {name!r} (gamehost pid {host.pid})")


def stop():
    """End the game: killing its debugger (gamehost) ends the debugged game too."""
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        sys.exit("no gamehost pid recorded")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    PID_FILE.unlink(missing_ok=True)
    print(f"stopped gamehost pid {pid} and its game")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("action", choices=["newgame", "load", "stop"])
    ap.add_argument("save", nargs="?", help="load: the save's name")
    ap.add_argument("--exe", help="exe file name in the test install (default PR2_target.exe)")
    ap.add_argument("--bp", action="append", default=[])
    ap.add_argument("--max-hits", type=int)
    a = ap.parse_args(argv)
    if a.action == "newgame":
        newgame(a.bp, a.exe, a.max_hits)
    elif a.action == "load":
        if not a.save:
            ap.error("load needs the save's name")
        load(a.save, a.bp, a.exe)
    else:
        stop()


if __name__ == "__main__":
    main()
