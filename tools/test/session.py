#!/usr/bin/env python3
"""Scripted game session in the background: start the game under gamehost, start a free-play
game or load a save, and leave it running for cmd.py / console.py / screen.py.

    python tools/test/session.py newgame [--exe PR2_target.exe] [--town N] [--bp SPEC ...] [--max-hits N]
    python tools/test/session.py load "Quick Save" [--exe ...] [--bp ...]
    python tools/test/session.py stop                       end the game, remove the DPI flag, restore options.dat

Before it starts the game it switches the test settings on (windowed, intro videos parked,
no DPI doubling on HiDPI screens: the flag is set for the start and removed again once the game
runs) and starts mute_game.ps1 -Watch so the game makes no sound. It refuses to start while the
PC shows the screen saver or the lock screen (Direct3D 8 cannot start then). `newgame` writes the
60 towns (index = Data\\staedte.ini order, with names, from the extracted archives, PR2_WORKDIR)
to PR2_BUILD/towns.tsv.

Waits are readiness signals where there are any, not fixed sleeps: the main menu's state byte
(read from game memory through gamehost), and, when a mod DLL writes them, lines of the mod's
log (LOG and MARKERS below: a mod's wrapper sets them; read from the byte offset of the log taken
just before the step, so only this run's lines count). Without a mod's markers a new game and a
load wait fixed times (NEWGAME_WAIT, LOAD_WAIT). Every wait has a timeout that stops the run with
what was missing. `--town N` (staedte.ini index) makes the free-play setup start there: it
remembers the last start town in Save\\options.dat, so set_start_town() writes it there first
(the original is kept in PR2_BACKUP_DIR and put back by restore_options() and by `stop`).

From Python (a suite):
    import session
    session.newgame([], "PR2_mymod.exe")    # or session.load("Quick Save", [])
    session.cmd("read", "0x700500", "4")    # in-process, no Python start per command
    session.quicksave()
    session.cleanup()                       # in a finally: end the game, DPI flag, options.dat

Back up the saves first (env.py backup-saves), and take the test lock (testlock.py) if anything
else may use the same test install. The click coordinates assume the 1280x800 window gamehost
keeps; the free-play start was measured on the 1.1.2.3 main menu.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402
from gamehost import CMD_FILE, RUNS  # noqa: E402

TOWN_VTABLE = "0x6a6bc0"    # CStadtLager vtable in 1.1.2.3
MENU_FREEPLAY = (1045, 65)  # 1280x800 window coordinates
SETUP_START = (1045, 65)
WELCOME_OK = (605, 519)
MENU_LOAD_FN = "0x526470"   # main-menu controller: load the save named by a std::string
QUICKSAVE_FN = "0x57EE90"   # QuickSave(this = the game object 0x700500)
APP = 0x6E5958              # *APP = the app object; *(app + 0xE8) = the main-menu controller
MENU_STATE, MENU_SETUP = 0x3C, 0x1C   # menu state (1 main menu, 6 free-game setup, 12 loading); setup screen
START_TOWN = 0x701EAC       # the settings block's start town (written by the setup screen's Commit)
RUNNING = 0x700500 + 0xE44  # the sim clock runs while this byte is 1 (0 while a modal letter holds it)
PID_FILE = env.BUILD / "gamehost.pid"
SETTLE = float(os.environ.get("PR2_SETTLE", "1"))   # seconds the world runs after it is ready

# A mod's readiness signals (a mod's wrapper sets them; see Sovereign Nations' tools/sn_session.py):
LOG = None                  # Path of the mod DLL's log next to the exe (recreated at every launch)
MARKERS = {}                # "started": [regex] the generator started (after the setup's Start click)
#                             "ready":   [regex] the new game is generated
#                             "world":   [regex] the world is up (the welcome letter comes next)
#                             "loaded":  [([regex, ...], timeout s), ...] steps of a finished load
DEFAULT_TOWN = None         # newgame's start town when none is given (None: what options.dat remembers)
STRICT_TOWNS = False        # newgame: stop when the town scan does not find every staedte.ini town
NEWGAME_WAIT = 40           # s without markers: world generation + town load
LOAD_WAIT = 45              # s without markers: a load

_host = None                # the running gamehost.py process (launch)
_run_log = None             # this launch's gamehost.log


# ---------------------------------------------------------------- the logs --
def latest_log():
    if _run_log is not None:
        return _run_log
    return max(RUNS.glob("*/gamehost.log"))


def wait_for(pattern, after=0, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        text = latest_log().read_text(errors="replace")[after:]
        if re.search(pattern, text):
            return text
        time.sleep(0.25)
    raise SystemExit(f"timeout waiting for {pattern!r}")


def log_mark():
    """Byte offset of the end of the mod's log (the log is CRLF: always slice by bytes)."""
    try:
        return LOG.stat().st_size
    except (OSError, AttributeError):
        return 0


def log_since(mark):
    try:
        data = LOG.read_bytes()
    except (OSError, AttributeError):
        return ""
    return data[mark:].decode("latin-1")


def wait_log(patterns, mark, timeout, what):
    """Wait until every regex in `patterns` has appeared in the mod's log after byte offset `mark`
    (each one after the previous one's match, in order). Fails at once when the game is gone, and
    after `timeout` s with the patterns still missing and the log's tail."""
    t0 = time.time()
    while True:
        text, pos, missing = log_since(mark), 0, []
        for p in patterns:
            m = re.compile(p).search(text, pos)
            if not m:
                missing.append(p)
                break
            pos = m.end()
        if not missing:
            return text
        if not host_alive() or "EXIT code=" in latest_log().read_text(errors="replace"):
            raise SystemExit(f"{what}: the game exited while waiting for {missing[0]!r}")
        if time.time() - t0 > timeout:
            tail = text[-500:].replace("\r\n", " | ")
            name = LOG.name if LOG else "the log"
            raise SystemExit(f"{what}: {missing[0]!r} not in {name} within {timeout:.0f}s (log tail: {tail})")
        time.sleep(0.2)


# ------------------------------------------------------- talking to gamehost --
def host_alive():
    return _host is None or _host.poll() is None


def cmd(*args, timeout=60):
    """Send one command to gamehost and return its log lines (what cmd.py prints), in-process.
    Once the host has died, fail at once instead of waiting 60 s per call (a dead host also
    takes the game with it)."""
    if not host_alive():
        raise SystemExit(f"gamehost is gone (exit {_host.returncode}); see {env.BUILD / 'host_stderr.txt'}")
    line = " ".join(map(str, args))
    done = CMD_FILE.with_suffix(".done")
    done.unlink(missing_ok=True)
    log = latest_log()
    start = log.stat().st_size
    CMD_FILE.write_text(line + "\n")
    t0 = time.time()
    while not done.exists():
        if time.time() - t0 > timeout:
            if not host_alive():
                raise SystemExit(f"gamehost died during '{line}'")
            print(f"no reply in {timeout}s to '{line}' (is gamehost.py running?)", flush=True)
            return ""
        time.sleep(0.02)
    with open(log, encoding="utf-8", errors="replace") as f:
        f.seek(start)
        return f.read()


def u32(addr):
    """A dword of game memory (None if unreadable)."""
    m = re.search(r": ((?:[0-9a-f]{2} ){3}[0-9a-f]{2})", cmd("read", f"{addr:#x}", "4"))
    return int.from_bytes(bytes.fromhex(m.group(1)), "little") if m else None


def menu_ptr():
    app = u32(APP)
    return u32(app + 0xE8) if app else None


def menu_state():
    """(main-menu controller, its state, its free-game setup screen); Nones while it does not exist."""
    menu = menu_ptr()
    if not menu:
        return None, None, None
    return menu, u32(menu + MENU_STATE), u32(menu + MENU_SETUP)


def wait_menu(state, timeout, what, setup=False):
    t0, st = time.time(), None
    while time.time() - t0 < timeout:
        menu, st, scr = menu_state()
        if st == state and (scr or not setup):
            return menu
        time.sleep(0.2)
    raise SystemExit(f"{what}: the main menu did not reach state {state} within {timeout}s (state {st})")


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


def _runs():
    return {p.parent.name for p in RUNS.glob("*/gamehost.log")}


def launch(bps, exe=None, max_hits=None):
    """Start the game under gamehost and wait until the main menu is up.
    Always enforces the test settings first (windowed, intro skipped, no DPI doubling): a
    fullscreen launch would take over the user's screen. The DPI flag is only needed while the
    process starts, so it is removed again before this returns."""
    env.windowed(True)
    env.fast_boot(True)
    env.mute_sound()                  # no sound from test games
    args = [sys.executable, str(HERE / "gamehost.py")] + (["--exe", exe] if exe else []) \
        + (["--max-hits", str(max_hits)] if max_hits else [])
    for b in bps:
        args += ["--bp", b]
    desk = input_desktop()
    if desk != "Default":
        raise SystemExit(f"the PC's input desktop is {desk!r} (screen saver or locked): "
                         "the game's Direct3D cannot start; rerun when the PC is in use")
    env.dpi_aware(True, also=[env.ENV / (exe or env.ENV_EXE.name)])
    try:
        return _start(args)
    finally:
        env.dpi_aware(False)          # read at process start: the running game keeps it


def _start(args):
    global _host, _run_log
    env.BUILD.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        before = _runs()
        _run_log = None
        err = open(env.BUILD / "host_stderr.txt", "ab")      # a host crash leaves its traceback here
        host = _host = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err)
        PID_FILE.write_text(str(host.pid))
        t0 = time.time()
        while time.time() - t0 < 30:                   # this launch's run dir (never an older one's log)
            new = _runs() - before
            if new:
                _run_log = RUNS / max(new) / "gamehost.log"
                break
            if host.poll() is not None:
                break
            time.sleep(0.1)
        if _run_log is None:
            raise SystemExit(f"gamehost did not start (see {env.BUILD / 'host_stderr.txt'})")
        # engine up (the benign font throw); a start-up D3D failure right after the previous
        # instance was killed exits within seconds -> retry
        text = wait_for(r"PAVCGenericException|EXIT code=")
        if "PAVCGenericException" in text:
            try:
                wait_menu(1, 30, "launch")            # the main menu controller exists and is idle
            except SystemExit:
                if not host_alive() or "EXIT code=" in latest_log().read_text(errors="replace"):
                    print(f"game exited during start-up (attempt {attempt + 1}); retrying")
                    time.sleep(2)
                    continue
                raise
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


# ------------------------------------------------------------ the start town --
OPTIONS = env.SAVE_DIR / "options.dat"
OPTIONS_ORIG = env.BACKUPS / "options.dat.before-test"


def _start_town_offset(data):
    """Byte offset of the remembered free-play start town in options.dat, or None.
    Layout (reader 0x5BD1A0): 'OPT2', version 0x88E, a 0x84-byte block, a string (u32 len + bytes),
    two wide strings (u32 len + 2*len bytes), then four u32-length-prefixed fields read into the
    options object at +0x131, +0x130, +0x132 (the start town, staedte.ini index) and +0x133."""
    if len(data) < 12 or data[:4] != b"OPT2" or int.from_bytes(data[4:8], "little") != 0x88E:
        return None
    pos = 8 + 0x84
    for unit in (1, 2, 2):
        if pos + 4 > len(data):
            return None
        n = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4 + (unit * n if 0 < n < 0x10000 else 0)
    for field in range(3):                            # +0x131, +0x130, then +0x132
        if pos + 4 > len(data):
            return None
        n = int.from_bytes(data[pos:pos + 4], "little")
        if field == 2:
            return pos + 4 if n == 1 and pos + 5 <= len(data) else None
        pos += 4 + (n if 0 < n < 0x10000 else 0)


def set_start_town(town):
    """Make the free-play setup pre-select `town` (it remembers the last start town in options.dat).
    The first change keeps the original as PR2_BACKUP_DIR/options.dat.before-test until restore_options()."""
    try:
        data = OPTIONS.read_bytes()
    except OSError:
        print("no options.dat: the game picks its default start town", flush=True)
        return None
    off = _start_town_offset(data)
    if off is None:
        print("options.dat has an unknown layout: start town left as it is", flush=True)
        return None
    if data[off] != town:
        if not OPTIONS_ORIG.exists():
            OPTIONS_ORIG.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OPTIONS, OPTIONS_ORIG)
        OPTIONS.write_bytes(data[:off] + bytes([town]) + data[off + 1:])
        print(f"options.dat: free-play start town {data[off]} -> {town}", flush=True)
    return data[off]


def restore_options():
    if OPTIONS_ORIG.exists():
        shutil.copy2(OPTIONS_ORIG, OPTIONS)
        OPTIONS_ORIG.unlink()
        print("options.dat restored", flush=True)


# ------------------------------------------------------------- start a game --
_DEFAULT = object()


def newgame(bps, exe=None, max_hits=None, town=_DEFAULT):
    """New free-play game, at `town` (staedte.ini index; default DEFAULT_TOWN) if given."""
    if town is _DEFAULT:
        town = DEFAULT_TOWN
    if town is not None:
        set_start_town(town)
    host = launch(bps, exe, max_hits)
    # The two buttons share a position: click again only while the menu is provably still on the
    # same screen (a main menu still sliding in can miss a click), never once it moved on.
    for _ in range(5):
        cmd("click", *MENU_FREEPLAY)
        try:
            wait_menu(6, 8, "free game", setup=True)
            break
        except SystemExit:
            if menu_state()[1] != 1:
                raise
    else:
        raise SystemExit("free game: the setup screen did not open")
    mark = log_mark()
    for _ in range(5):
        cmd("click", *SETUP_START)
        try:
            if MARKERS.get("started"):
                wait_log(MARKERS["started"], mark, 8, "start game")     # e.g. the world reset
            else:
                _wait_left_setup(8)
            break
        except SystemExit:
            if menu_state()[1] != 6:
                raise
    else:
        raise SystemExit("start game: the generator did not start")
    if MARKERS.get("ready"):
        wait_log(MARKERS["ready"], mark, 180, "new game")
    else:
        time.sleep(NEWGAME_WAIT)
    got = u32(START_TOWN)
    if town is not None and got != town:
        raise SystemExit(f"new game started at town {got}, not {town} (options.dat start town not applied)")
    close_welcome(mark)
    lagers = scan_towns()
    try:
        names = town_names()
    except OSError:
        names = [f"town{i}" for i in range(len(lagers))]
    if len(lagers) != len(names):
        if STRICT_TOWNS:
            raise SystemExit(f"found {len(lagers)} towns, expected {len(names)}")
        print(f"warning: found {len(lagers)} towns, expected {len(names)}")
    tsv = env.BUILD / "towns.tsv"
    tsv.write_text("idx\tname\tlager\tnation\tdisplay\n" + "".join(
        f"{i}\t{n}\t{a}\t{nat}\t{d}\n" for i, (n, (a, nat, d)) in enumerate(zip(names, lagers))))
    print(f"game running (gamehost pid {host.pid}); towns -> {tsv}")


def _wait_left_setup(timeout):
    """Without a mod's marker: the setup screen's Start was taken once the menu leaves state 6."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = menu_state()[1]
        if st != 6:
            return
        time.sleep(0.2)
    raise SystemExit("start game: the menu is still on the setup screen")


def run_flag():
    """The sim clock's running byte [0x700500+0xE44]: 0 while a modal letter (or a pause) holds the game."""
    v = u32(RUNNING)
    return v & 0xFF if v is not None else None


def close_welcome(mark):
    """After the new game is generated the engine pre-simulates the first weeks behind the loading
    screen; then the world comes up and the welcome letter stops the clock (running byte 1 for a
    moment, then 0 until OK is pressed, then 1). Press OK while the letter holds the clock, until
    the clock runs."""
    if MARKERS.get("world"):
        wait_log(MARKERS["world"], mark, 180, "new game")
    t0 = time.time()
    while run_flag() != 0:
        if time.time() - t0 > 15:                     # no letter seen: press where it would be, as before
            print("welcome letter not seen (game clock running); pressing OK once anyway", flush=True)
            cmd("click", *WELCOME_OK)
            time.sleep(1)
            return
        time.sleep(0.2)
    for _ in range(5):
        cmd("click", *WELCOME_OK)
        t1 = time.time()
        while time.time() - t1 < 3:
            time.sleep(0.2)
            if run_flag() == 1:
                time.sleep(SETTLE)
                return
    raise SystemExit("new game: the welcome letter did not close (game clock still stopped after 5 presses of OK)")


def quicksave(timeout=30):
    """QuickSave and return once 'Quick Save.pr2' has been rewritten and stopped changing
    (instead of a fixed pause before the game is killed or the file copied)."""
    qs = env.SAVE_DIR / "Quick Save.pr2"
    before = qs.stat().st_mtime_ns if qs.exists() else 0
    cmd("call", QUICKSAVE_FN, "0x700500")
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        time.sleep(0.25)
        try:
            st = qs.stat()
        except OSError:
            continue
        now = (st.st_mtime_ns, st.st_size)
        if st.st_mtime_ns != before and st.st_size > 0:
            if now == last:
                time.sleep(0.5)
                return qs
            last = now
    raise SystemExit("QuickSave did not write 'Quick Save.pr2'")


def load(name, bps, exe=None):
    """Launch, load the save `name` from the main menu, wait for the world."""
    host = launch(bps, exe)
    mark = log_mark()
    cmd("call", MENU_LOAD_FN, "menu", "s:" + name.replace(" ", "_"))
    wait_loaded(mark, name)
    print(f"loaded {name!r} (gamehost pid {host.pid})")


def wait_loaded(mark, name):
    """With a mod's markers: every step of MARKERS["loaded"] in the log; else LOAD_WAIT s."""
    steps = MARKERS.get("loaded")
    if not steps:
        time.sleep(LOAD_WAIT)
        return
    for patterns, timeout in steps:
        wait_log(patterns, mark, timeout, f"load {name!r}")
    time.sleep(SETTLE)


# ------------------------------------------------------------------ the end --
def stop():
    """End the game: killing its debugger (gamehost) ends the debugged game too."""
    pid = _host.pid if _host is not None and _host.poll() is None else None
    if pid is None:
        try:
            pid = int(PID_FILE.read_text())
        except (OSError, ValueError):
            pid = None
    if pid is not None:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        print(f"stopped gamehost pid {pid} and its game")
    else:
        print("no gamehost pid recorded")
    PID_FILE.unlink(missing_ok=True)


def cleanup():
    """The end of a test run (call it in a finally): end the game, remove the DPI flags the
    tools added, and put options.dat back. The saves and settings.cfg come back with
    env.restore_saves() / env.restore_settings()."""
    stop()
    env.dpi_aware(False)
    restore_options()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("action", choices=["newgame", "load", "stop"])
    ap.add_argument("save", nargs="?", help="load: the save's name")
    ap.add_argument("--exe", help="exe file name in the test install (default PR2_target.exe)")
    ap.add_argument("--town", type=int, default=_DEFAULT, help="newgame: the start town (staedte.ini index)")
    ap.add_argument("--bp", action="append", default=[])
    ap.add_argument("--max-hits", type=int)
    a = ap.parse_args(argv)
    if a.action == "newgame":
        newgame(a.bp, a.exe, a.max_hits, a.town)
    elif a.action == "load":
        if not a.save:
            ap.error("load needs the save's name")
        load(a.save, a.bp, a.exe)
    else:
        cleanup()


if __name__ == "__main__":
    main()
