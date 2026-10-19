#!/usr/bin/env python3
"""Snapshot-diff memory scanner for the running game: find the variable behind something you see.

    python memscan.py snap A          # state 1 (e.g. camera scrolled to the far right)
    python memscan.py snap B          # state 2 (scrolled to the far left)
    python memscan.py snap C          # state 1 again
    python memscan.py narrow A B C    # dwords where A == C and A != B: candidates
    python memscan.py changed A B --from 1500 --to 1750   # dwords that went from 1500 to 1750
    python memscan.py find 1750       # search the live process for a value (--float, --snap TAG)
    python memscan.py watch 0x1A2B3C4 0x1A2B3C8           # poll addresses live while you play

Reads memory only (ReadProcessMemory; no debugger, the game keeps running). A snapshot is
every committed writable region: private memory (heap, stacks) plus the writable sections
of loaded modules (globals; leave them out with --private-only). Regions over --max-region
MB (textures, sound) are skipped.

The A/B/A pattern beats a single diff: the game changes thousands of dwords between any two
snapshots (timers, animation, the allocator); a value that returns with the state while
differing in between is rare. Heap objects move between sessions: re-find the owning object
(a vtable, a pointer chain from a global) before relying on an address.

Attaches to the one running PR2_*.exe unless --process/--pid says otherwise; never to the
Steam PR2.exe or anything under "steamapps". Snapshots go to <PR2_BUILD>/live/memscan/
(tens to hundreds of MB each).
"""
import argparse
import pickle
import struct
import time

import proc


def snap_path(tag):
    return proc.outdir("memscan") / f"{tag}.pkl"


def take(a):
    pid, name, _ = proc.pick(a.process, a.pid)
    h = proc.open_read(pid)
    regs = {}
    try:
        for base, size in proc.regions(h, a.private_only, a.max_region * 1024 * 1024):
            b = proc.read(h, base, size)
            if b:
                regs[base] = b
    finally:
        proc.k.CloseHandle(h)
    return pid, name, regs


def load(tag):
    p = snap_path(tag)
    if not p.exists():
        raise SystemExit(f"no snapshot {tag} ({p}); take it with `memscan.py snap {tag}`")
    with open(p, "rb") as f:
        return pickle.load(f)


def cmd_snap(a):
    pid, name, regs = take(a)
    with open(snap_path(a.tag), "wb") as f:
        pickle.dump({"pid": pid, "process": name, "time": time.time(), "regions": regs}, f)
    print(f"snap {a.tag}: {name} pid={pid}, {len(regs)} regions, "
          f"{sum(len(v) for v in regs.values()) // 1024} KB -> {snap_path(a.tag)}")


def _same_process(*snaps):
    if len({s["pid"] for s in snaps}) > 1:
        print("WARNING: the snapshots come from different processes; heap addresses do not match")


def _words(ba):
    n = len(ba) // 4
    return struct.unpack_from("<%dI" % n, ba, 0)


def cmd_narrow(a):
    A, B, Cc = load(a.a), load(a.b), load(a.c)
    _same_process(A, B, Cc)
    hits = []
    for base, ba in A["regions"].items():
        bb, bc = B["regions"].get(base), Cc["regions"].get(base)
        if bb is None or bc is None:
            continue
        n = min(len(ba), len(bb), len(bc)) & ~3
        wa, wb, wc = _words(ba[:n]), _words(bb[:n]), _words(bc[:n])
        for i, (va, vb, vc) in enumerate(zip(wa, wb, wc)):
            if va == vc and va != vb and (not a.max_value or (va <= a.max_value and vb <= a.max_value)):
                hits.append((base + 4 * i, va, vb))
    print(f"{len(hits)} dwords with {a.a} == {a.c} != {a.b}")
    for addr, va, vb in hits[:a.limit]:
        print(f"  0x{addr:08X}: {a.a}={va} (0x{va:X})  {a.b}={vb} (0x{vb:X})  delta={va - vb}")


def cmd_changed(a):
    A, B = load(a.a), load(a.b)
    _same_process(A, B)
    hits = []
    for base, ba in A["regions"].items():
        bb = B["regions"].get(base)
        if bb is None:
            continue
        n = min(len(ba), len(bb)) & ~3
        for i, (va, vb) in enumerate(zip(_words(ba[:n]), _words(bb[:n]))):
            if va != vb and (a.frm is None or va == a.frm) and (a.to is None or vb == a.to):
                hits.append((base + 4 * i, va, vb))
    print(f"{len(hits)} dwords changed {a.a} -> {a.b}")
    for addr, va, vb in hits[:a.limit]:
        print(f"  0x{addr:08X}: {va} (0x{va:X}) -> {vb} (0x{vb:X})")


def cmd_find(a):
    pat = struct.pack("<f", float(a.value)) if a.float else struct.pack("<I", int(a.value, 0) & 0xFFFFFFFF)
    if a.snap:
        regs = load(a.snap)["regions"]
    else:
        regs = take(a)[2]
    hits = []
    for base, b in regs.items():
        i = b.find(pat)
        while i != -1:
            if i % 4 == 0 or a.unaligned:
                hits.append(base + i)
            i = b.find(pat, i + 1)
    print(f"{len(hits)} hits for {a.value}{' (float)' if a.float else ''}")
    for addr in hits[:a.limit]:
        print(f"  0x{addr:08X}")


def cmd_watch(a):
    pid, name, _ = proc.pick(a.process, a.pid)
    h = proc.open_read(pid)
    addrs = [int(x, 16) for x in a.addr]
    print(f"polling {name} pid={pid} every {a.interval}s (Ctrl+C stops)")
    print("  " + "  ".join(f"{x:>12X}" for x in addrs))
    try:
        for _ in range(a.count):
            vals = []
            for x in addrs:
                v = proc.read(h, x, 4)
                vals.append(struct.unpack("<f" if a.float else "<i", v)[0] if len(v) == 4 else None)
            print("  " + "  ".join(f"{'??':>12}" if v is None else
                                   (f"{v:>12.4f}" if a.float else f"{v:>12d}") for v in vals), flush=True)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        proc.k.CloseHandle(h)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target(p):
        p.add_argument("--process", help="process name (default: the one running PR2_*.exe)")
        p.add_argument("--pid", type=int, help="process id")
        p.add_argument("--private-only", action="store_true", help="skip modules' writable sections")
        p.add_argument("--max-region", type=int, default=32, help="skip regions over N MB (default 32)")

    p = sub.add_parser("snap", help="snapshot the live process under a tag")
    p.add_argument("tag")
    target(p)
    p = sub.add_parser("narrow", help="dwords where A == C and A != B")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("c")
    p.add_argument("--max-value", type=lambda s: int(s, 0), default=0,
                   help="only values <= N in A and B (0 = any; e.g. 0x200000 for coordinates)")
    p.add_argument("--limit", type=int, default=80, help="rows to print (default 80)")
    p = sub.add_parser("changed", help="dwords that differ between A and B")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--from", dest="frm", type=lambda s: int(s, 0), help="only dwords that were this in A")
    p.add_argument("--to", type=lambda s: int(s, 0), help="only dwords that are this in B")
    p.add_argument("--limit", type=int, default=80, help="rows to print (default 80)")
    p = sub.add_parser("find", help="search for a 32-bit value (live, or in a snapshot)")
    p.add_argument("value", help="integer (0x.. ok) or, with --float, a float")
    p.add_argument("--float", action="store_true", help="search a 32-bit float")
    p.add_argument("--snap", help="search this snapshot instead of the live process")
    p.add_argument("--unaligned", action="store_true", help="also report hits not on a 4-byte boundary")
    p.add_argument("--limit", type=int, default=80, help="rows to print (default 80)")
    target(p)
    p = sub.add_parser("watch", help="poll dwords live")
    p.add_argument("addr", nargs="+", help="hex addresses")
    p.add_argument("--float", action="store_true", help="show as floats")
    p.add_argument("--interval", type=float, default=0.4, help="seconds between polls (default 0.4)")
    p.add_argument("--count", type=int, default=40, help="polls (default 40)")
    p.add_argument("--process", help="process name (default: the one running PR2_*.exe)")
    p.add_argument("--pid", type=int, help="process id")
    a = ap.parse_args()
    {"snap": cmd_snap, "narrow": cmd_narrow, "changed": cmd_changed,
     "find": cmd_find, "watch": cmd_watch}[a.cmd](a)


if __name__ == "__main__":
    main()
