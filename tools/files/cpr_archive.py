"""Read and write Ascaron `.cpr` archives (ASCARON_ARCHIVE V0.9), single and multi-segment.

Layout (little-endian), checked on all four archives of Port Royale 2 1.1.2.3:

    [0 .. 32)   magic "ASCARON_ARCHIVE V0.9", NUL padded
    segment header at H (H = 32 for the first segment):
      u32 data_start_rel = 0x1000      (data starts at H + 0x1000)
      u32 dir_len        = directory bytes + 16
      u32 count
      u32 data_size      (padded to 32)
    directory (H + 16), `count` entries:
      u32 offset (absolute in the file), u32 size, u32 flag (= 1), name + NUL (backslash path)
    zero bytes up to H + 0x1000
    data: each file at a 32-byte aligned offset, zero padded, the segment's end padded to 32
    the next segment's header follows at H + 0x1000 + data_size (no magic)

A segment's directory must fit in 0x1000 - 16 bytes, which is why the big archives are split
into segments. Names may repeat (pr2_loca.cpr lists one screen twice).

Used by cpr_extract.py and cpr_pack.py.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"ASCARON_ARCHIVE V0.9"
HEADER = 32
DATA_REL = 0x1000
DIR_MAX = DATA_REL - 16
ALIGN = 32


@dataclass
class Entry:
    name: str           # backslash path as stored
    offset: int         # absolute file offset
    size: int
    flag: int
    segment: int


def _align(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) // a * a


def _parse_segment(data: bytes, h: int, seg: int):
    if h + 16 > len(data):
        return None
    rel, dir_len, count, data_size = struct.unpack_from("<IIII", data, h)
    if rel != DATA_REL or count == 0 or count > 100_000 or dir_len > DATA_REL:
        return None
    pos, end, entries = h + 16, h + dir_len, []
    try:
        for _ in range(count):
            off, size, flag = struct.unpack_from("<III", data, pos)
            pos += 12
            nul = data.index(b"\0", pos, end)
            name = data[pos:nul].decode("latin-1")
            pos = nul + 1
            if off + size > len(data):
                return None
            entries.append(Entry(name, off, size, flag, seg))
    except (struct.error, ValueError):
        return None
    return entries, h + rel + data_size, data_size


def read_index(data: bytes, log=None):
    """All entries of an archive, in directory order. log(msg) gets one line per segment."""
    if not data.startswith(MAGIC):
        raise ValueError("not an ASCARON_ARCHIVE (bad magic)")
    entries, h, seg = [], HEADER, 0
    while h < len(data):
        res = _parse_segment(data, h, seg)
        if res is None:
            # Not where the rules say: scan forward in 16-byte steps (never needed for the
            # 1.1.2.3 archives; kept so a damaged or unusual archive still lists).
            probe, found = _align(h, 16), None
            while probe < min(len(data), h + (64 << 20)):
                res = _parse_segment(data, probe, seg)
                if res is not None:
                    found = probe
                    break
                probe += 16
            if found is None:
                if log and len(data) - h > 0:
                    log(f"warning: {len(data) - h} trailing bytes at {h:#x} are not a segment")
                break
            if log:
                log(f"warning: segment {seg} found at {found:#x}, expected {h:#x}")
            h = found
        ents, nxt, size = res
        if log:
            log(f"seg {seg:>3}: header=0x{h:08X} files={len(ents):>5} data_size={size}")
        entries.extend(ents)
        seg += 1
        if nxt <= h:
            break
        h = nxt
    return entries


def _dir_bytes(name: str) -> int:
    return 12 + len(name.encode("latin-1")) + 1


def build(files, segments=None) -> bytes:
    """files: list of (name, bytes) in directory order. segments: optional list giving each
    file's segment number (non-decreasing); without it, files fill segments greedily."""
    if not files:
        raise ValueError("no files")
    if segments is None:
        segments, seg, used = [], 0, 0
        for name, _ in files:
            n = _dir_bytes(name)
            if used and used + n > DIR_MAX:
                seg, used = seg + 1, 0
            segments.append(seg)
            used += n
    if len(segments) != len(files) or any(b < a for a, b in zip(segments, segments[1:])):
        raise ValueError("segments must be one non-decreasing number per file")
    groups = {}
    for (name, blob), s in zip(files, segments):
        groups.setdefault(s, []).append((name, blob))
    out = bytearray(MAGIC.ljust(HEADER, b"\0"))
    h = HEADER
    for s in sorted(groups):
        group = groups[s]
        dir_bytes = sum(_dir_bytes(n) for n, _ in group)
        if dir_bytes > DIR_MAX:
            raise ValueError(f"segment {s}: directory {dir_bytes} bytes > {DIR_MAX}")
        cursor, layout = h + DATA_REL, []
        for name, blob in group:
            layout.append((name, cursor, blob))
            cursor = _align(cursor + len(blob))
        data_size = cursor - (h + DATA_REL)
        seg = bytearray(DATA_REL + data_size)
        struct.pack_into("<IIII", seg, 0, DATA_REL, dir_bytes + 16, len(group), data_size)
        pos = 16
        for name, off, blob in layout:
            struct.pack_into("<III", seg, pos, off, len(blob), 1)
            pos += 12
            nb = name.encode("latin-1")
            seg[pos:pos + len(nb)] = nb
            pos += len(nb) + 1
        for name, off, blob in layout:
            seg[off - h:off - h + len(blob)] = blob
        out += seg
        h += len(seg)
    return bytes(out)
