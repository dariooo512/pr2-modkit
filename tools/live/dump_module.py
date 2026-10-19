#!/usr/bin/env python3
r"""Dump a module from the running game as a static PE: unpacks UPX-packed engine DLLs.

    python dump_module.py --list                       # modules of the running test game
    python dump_module.py Walhalla.dll                 # -> _build/live/Walhalla.unpacked.dll
    python dump_module.py A2dLib-2.20a.dll --out a2d.dll --pid 1234

Several of the game's DLLs are UPX-packed: the file on disk is a decompression stub. Once
the Windows loader has run that stub, the module's memory IS the unpacked code. This reads
the whole image from the running process (read-only; no debugger) and rewrites the section
table so each section's file offset equals its virtual address and its file size its virtual
size, and ImageBase is the address it is loaded at. Disassemblers then read the code
straight from the file, at the same addresses you see in the running game.

Attaches to the one running PR2_*.exe (the test install's exes) unless --process/--pid says
otherwise; never to the Steam PR2.exe or a process under "steamapps".

Limits: the dump is the module's state at that moment: imports are already resolved to
absolute addresses (the import table may not parse; read the IAT as data), relocations are
applied for the load address, and globals hold runtime values. It is for reading, not for
loading.
"""
import argparse
import struct
from pathlib import Path

import proc


def rebuild_pe(img, base):
    """Section raw offsets/sizes := virtual ones; ImageBase := the load address."""
    b = bytearray(img)
    e_lfanew = struct.unpack_from("<I", b, 0x3C)[0]
    if b[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise SystemExit("bad PE header in the dump")
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", b, coff + 2)[0]
    sizeof_opt = struct.unpack_from("<H", b, coff + 16)[0]
    opt = coff + 20
    struct.pack_into("<I", b, opt + 28, base & 0xFFFFFFFF)
    sechdr = opt + sizeof_opt
    for i in range(nsec):
        sh = sechdr + i * 40
        vsz, vad = struct.unpack_from("<II", b, sh + 8)
        struct.pack_into("<I", b, sh + 16, vsz)   # SizeOfRawData
        struct.pack_into("<I", b, sh + 20, vad)   # PointerToRawData
    return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", nargs="?", help="module name, e.g. Walhalla.dll")
    ap.add_argument("--out", help="output file (default _build/live/<module>.unpacked.dll)")
    ap.add_argument("--process", help="process name (default: the one running PR2_*.exe)")
    ap.add_argument("--pid", type=int, help="process id")
    ap.add_argument("--list", action="store_true", help="list the process's modules and exit")
    a = ap.parse_args()
    pid, name, _ = proc.pick(a.process, a.pid)
    mods = proc.modules(pid)
    if a.list or not a.module:
        print(f"{name} pid={pid}: {len(mods)} modules")
        for base, size, mname, _p in sorted(mods):
            print(f"  0x{base:08X} 0x{size:08X} {mname}")
        return
    hit = [m for m in mods if m[2].lower() == a.module.lower()]
    if not hit:
        raise SystemExit(f"{a.module} is not loaded in {name} (pid {pid}); see --list")
    base, size, mname, _p = hit[0]
    h = proc.open_read(pid)
    img, bad = bytearray(), 0
    try:
        for off in range(0, size, 0x1000):        # page by page: an unreadable page is zeroed
            n = min(0x1000, size - off)
            page = proc.read(h, base + off, n)
            bad += len(page) < n
            img += page.ljust(n, b"\0")
    finally:
        proc.k.CloseHandle(h)
    print(f"{name} pid={pid}  {mname} base=0x{base:X} size=0x{size:X}; "
          f"{bad} unreadable page(s) zero-filled")
    if bad * 0x1000 >= size or img[:2] != b"MZ":
        raise SystemExit("could not read the module image")
    out = Path(a.out) if a.out else proc.outdir() / (Path(mname).stem + ".unpacked" + Path(mname).suffix)
    out.write_bytes(rebuild_pe(img, base))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
