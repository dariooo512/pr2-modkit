#!/usr/bin/env python3
"""Which functions actually run in a game session: one-shot INT3 coverage.

    python coverage.py trace --targets _build/replay/functions.tsv [--idle 4]
    python coverage.py hit LIST            # which VAs of LIST ran in any traced session

`trace` puts an INT3 on every target entry. On a function's first hit it logs the VA,
restores the original byte, rewinds EIP and lets it run at full speed, so each function costs
one debug event in the whole session. Play whatever you want covered; the run ends when you
quit the game, or after --idle periods of 8 s without a debug event (a quiet menu counts as
idle: raise --idle for interactive play).

Writes <PR2_BUILD>/replay/coverage/executed.txt (this run, first-hit order) and
coverage.tsv (every target, covered=1 if any traced run ever hit it, this_run).
Use it to decide what to read or rebuild first: code that never runs can wait.
For "which functions does THIS action run", use recorder.py census + record-new instead.
"""
import argparse
import csv
import ctypes as C

import common as CM
import recorder as R

k = R.k


def trace(exe, vas, idle_limit=4, max_events=8_000_000):
    k.DebugSetProcessKillOnExit(True)
    pi = R.launch(exe)
    print("launched pid=%d  instrumenting %d function entries (one-shot)" % (pi.dwProcessId, len(vas)), flush=True)
    hproc, threads, saved = None, {}, {}
    executed, seen = [], set()
    de = R.DEBUG_EVENT()
    timeouts = ev = 0
    while timeouts < idle_limit and ev < max_events:
        if not k.WaitForDebugEvent(C.byref(de), 8000):
            timeouts += 1
            print("  (idle %d/%d, %d fns so far)" % (timeouts, idle_limit, len(executed)), flush=True)
            continue
        timeouts = 0
        ev += 1
        code, tid, status = de.dwDebugEventCode, de.dwThreadId, R.DBG_CONTINUE
        if code == R.CREATE_PROCESS_DEBUG_EVENT:
            cpi = de.u.CreateProcessInfo
            hproc = cpi.hProcess
            threads[tid] = cpi.hThread
            for va in vas:
                R.set_int3(hproc, va, saved)
            print("armed %d/%d entry bps" % (len(saved), len(vas)), flush=True)
        elif code == R.CREATE_THREAD_DEBUG_EVENT:
            threads[tid] = de.u.CreateThread.hThread
        elif code == R.EXIT_THREAD_DEBUG_EVENT:
            threads.pop(tid, None)
        elif code == R.EXIT_PROCESS_DEBUG_EVENT:
            print("process exited code=%d" % de.u.ExitProcess.dwExitCode, flush=True)
            k.ContinueDebugEvent(de.dwProcessId, tid, R.DBG_CONTINUE)
            break
        elif code == R.EXCEPTION_DEBUG_EVENT:
            er = de.u.Exception.ExceptionRecord
            xcode = er.ExceptionCode & 0xFFFFFFFF
            addr = (er.ExceptionAddress or 0) & 0xFFFFFFFF
            hT = threads.get(tid)
            if xcode in R.BP_CODES and addr in saved and hT:
                if addr not in seen:
                    seen.add(addr)
                    executed.append(addr)
                    if len(executed) % 200 == 0:
                        print("  %d fns executed..." % len(executed), flush=True)
                ctx = R.get_ctx(hT)
                if ctx:
                    R.wpm(hproc, addr, bytes([saved.pop(addr)]))
                    ctx.Eip = addr
                    R.set_ctx(hT, ctx)
            elif xcode not in R.BP_CODES:
                status = R.DBG_EXCEPTION_NOT_HANDLED
        k.ContinueDebugEvent(de.dwProcessId, tid, status)
    R._finish(pi)
    print("\ntrace: %d / %d functions executed this run" % (len(executed), len(vas)), flush=True)
    return executed


def write_ledger(vas, executed):
    out = CM.outdir("coverage")
    cov = out / "coverage.tsv"
    prev = set()
    if cov.exists():
        for r in csv.DictReader(open(cov, newline=""), delimiter="\t"):
            if r.get("covered") == "1":
                prev.add(int(r["addr"], 16))
    now = set(executed)
    (out / "executed.txt").write_text("".join("0x%08X\n" % a for a in executed))
    with open(cov, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["addr", "covered", "this_run"])
        for a in sorted(set(vas) | prev):
            w.writerow(["0x%08X" % a, int(a in prev or a in now), int(a in now)])
    print("wrote %s (%d) and %s (%d ever covered)" % (out / "executed.txt", len(now), cov, len(prev | now)))


def hit(listfile):
    cov = CM.OUT / "coverage" / "coverage.tsv"
    if not cov.exists():
        raise SystemExit(f"no {cov}: run `coverage.py trace` first")
    covered = {int(r["addr"], 16) for r in csv.DictReader(open(cov, newline=""), delimiter="\t")
               if r.get("covered") == "1"}
    vas = CM.load_targets(listfile)[0]
    ran = [v for v in vas if v in covered]
    print("%d of %d listed functions ran in a traced session:" % (len(ran), len(vas)))
    for v in ran:
        print("   0x%08X" % v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("trace", help="run the game with every target armed once")
    p.add_argument("--targets", required=True, help="target list (see functions.py)")
    p.add_argument("--exclude", help="VAs to leave out")
    p.add_argument("--idle", type=int, default=4, help="stop after N quiet 8 s periods (default 4)")
    p.add_argument("--exe", help="the game exe inside the test install "
                                 "(default PR2_TEST_EXE or <PR2_TEST_ENV>/PR2_target.exe)")
    p.add_argument("--wait", action="store_true", help="wait for the test-install lock")
    p = sub.add_parser("hit", help="which VAs of a list ran in any traced session")
    p.add_argument("list", help="TSV with an addr column, or one hex VA per line")
    a = ap.parse_args()
    if a.cmd == "hit":
        hit(a.list)
        return
    excl = CM.load_va_set(a.exclude)
    vas = [v for v in CM.load_targets(a.targets)[0] if v not in excl]
    exe = CM.game_exe(a.exe)
    with CM.test_lock("coverage.py trace", wait=a.wait):
        executed = trace(exe, vas, idle_limit=a.idle)
    write_ledger(vas, executed)


if __name__ == "__main__":
    main()
