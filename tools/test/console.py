#!/usr/bin/env python3
"""Run Lua in the running test game through a mod DLL's dev console.

    python tools/test/console.py --mod MyMod script.lua      (or - for stdin)
    python tools/test/console.py --mod MyMod -e "Hello_Log(Hello_GetGold())"

A mod built from the template (template/dll/main.c) runs <MOD_NAME>.cmd next to the exe once a
frame and deletes it, when <MOD_NAME>.dev exists there (dev mode; never ship that file). The
script is written under a temporary name and renamed, so the DLL never reads half a file. Prints
the lines <MOD_NAME>.log gained during the run (until `CONSOLE status`, or --timeout seconds).
The game must be running in the test install (setting PR2_TEST_ENV) with a game started or
loaded (the Lua state exists only in a game, not in the main menu).
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--mod", required=True, help="the mod's MOD_NAME (its DLL, .log, .dev and .cmd names)")
    ap.add_argument("script", nargs="?", help="a Lua file, or - for stdin")
    ap.add_argument("-e", "--execute", help="Lua code given on the command line")
    ap.add_argument("--timeout", type=float, default=20)
    a = ap.parse_args(argv)
    if a.execute is not None:
        src = a.execute
    elif a.script == "-":
        src = sys.stdin.read()
    elif a.script:
        src = Path(a.script).read_text()
    else:
        ap.error("give a script, - or -e CODE")
    env = kit_paths.TEST_ENV
    if not (env / f"{a.mod}.dev").exists():
        print(f"warning: no {a.mod}.dev in {env}: the console is off", file=sys.stderr)
    log = env / f"{a.mod}.log"
    start = log.stat().st_size if log.exists() else 0
    tmp = env / f"{a.mod}.cmd.tmp"
    tmp.write_text(src)
    os.replace(tmp, env / f"{a.mod}.cmd")
    text, t0 = "", time.time()
    while time.time() - t0 < a.timeout:
        time.sleep(0.2)
        if log.exists():
            text = log.read_bytes()[start:].decode("latin-1")   # byte offsets: the log is CRLF
            if "CONSOLE status" in text:
                break
    print(text, end="")
    if "CONSOLE status" not in text:
        print(f"(no CONSOLE status line within {a.timeout:g} s; is a game running with {a.mod}.dll?)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
