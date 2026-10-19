#!/usr/bin/env python3
"""The test-install lock: only one test runs the test install at a time.

    python tools/test/testlock.py status
    python tools/test/testlock.py take "who and why" [--wait MINUTES]
    python tools/test/testlock.py release "who and why"

The lock is a folder next to the test install (setting PR2_TEST_LOCK, default
<test install>.lock) with an owner.txt saying who holds it. `take` creates it atomically
(mkdir), waiting while someone else holds it; `release` removes it only when owner.txt starts
with the same owner text, so nobody removes somebody else's lock. Never delete a lock you do not
own; if a holder crashed, ask them (or check that no game runs) before removing it by hand.

From Python:
    from testlock import held
    with held("my probe"):
        ...                                  # start the game, test, close the game
"""
import argparse
import contextlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

LOCK = kit_paths.TEST_LOCK


def owner():
    try:
        return (LOCK / "owner.txt").read_text(errors="replace").strip()
    except OSError:
        return None


def take(who: str, wait_minutes: float = 60, poll: float = 2) -> None:
    end = time.time() + wait_minutes * 60
    said = False
    while True:
        try:
            LOCK.mkdir(parents=False)
        except FileExistsError:
            if time.time() > end:
                raise SystemExit(f"lock {LOCK} still held by: {owner()!r}")
            if not said:
                print(f"waiting for the lock {LOCK}, held by: {owner()!r}", flush=True)
                said = True
            time.sleep(poll)
            continue
        (LOCK / "owner.txt").write_text(f"{who}, {time.ctime()}\n")
        print(f"lock taken: {LOCK}")
        return


def release(who: str) -> bool:
    cur = owner()
    if cur is None and not LOCK.exists():
        print("lock not held")
        return True
    if cur is None or not cur.startswith(who):
        print(f"not releasing: the lock is held by {cur!r}, not {who!r}")
        return False
    (LOCK / "owner.txt").unlink()
    LOCK.rmdir()
    print(f"lock released: {LOCK}")
    return True


@contextlib.contextmanager
def held(who: str, wait_minutes: float = 60):
    take(who, wait_minutes)
    try:
        yield
    finally:
        release(who)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["status", "take", "release"])
    ap.add_argument("who", nargs="?", help="owner text (take/release)")
    ap.add_argument("--wait", type=float, default=60, help="minutes to wait for the lock (take)")
    a = ap.parse_args(argv)
    if a.action == "status":
        print(f"{LOCK}: " + (f"held by {owner()!r}" if LOCK.exists() else "free"))
        return 0
    if not a.who:
        ap.error("take/release need the owner text")
    if a.action == "take":
        take(a.who, a.wait)
        return 0
    return 0 if release(a.who) else 1


if __name__ == "__main__":
    sys.exit(main())
