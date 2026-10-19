"""Shared helpers for the record/replay tools: settings, the safe game launch, target lists.

Nothing here runs on import except reading kit_paths. The tools that start the game all go
through `game_exe()` (which refuses anything outside the test install, any path containing
"steamapps", and the SteamStub retail exe) and `test_lock()` (the lock folder every test run
of the test install shares).
"""
import hashlib
import os
import re
import shutil
import struct
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = kit_paths.BUILD / "replay"            # everything these tools write lives below this
CORPUS = OUT / "corpus"                     # recorded sessions (session_<pid>/case_*.pkl)
# The exe the tools start by default: the DRM-free copy the test install runs.
# (Override per run with --exe, or for every run with PR2_TEST_EXE.)
DEFAULT_EXE = kit_paths.TEST_EXE                 # setting PR2_TEST_EXE


def outdir(*parts) -> Path:
    p = OUT.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- the game exe --------------------------------------------------------------------------
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def game_exe(arg=None) -> Path:
    """The exe to start, checked: it must exist, live inside the test install (PR2_TEST_ENV),
    not be under a Steam library ("steamapps"), and not be the SteamStub retail exe."""
    exe = Path(arg) if arg else DEFAULT_EXE
    if not exe.is_absolute():
        exe = kit_paths.TEST_ENV / exe
    exe = exe.resolve()
    if "steamapps" in str(exe).lower():
        raise SystemExit(f"refusing to start {exe}: it is in a Steam library. These tools only "
                         "run the game from the disposable test install (PR2_TEST_ENV).")
    env = kit_paths.TEST_ENV.resolve()
    if env not in exe.parents:
        raise SystemExit(f"refusing to start {exe}: it is not inside the test install {env} "
                         "(set PR2_TEST_ENV, or pass --exe with a path inside it)")
    kit_paths.need(exe, "game exe", "PR2_TEST_EXE or --exe")
    if _sha256(exe) == kit_paths.RETAIL_SHA256:
        raise SystemExit(f"refusing to start {exe}: it is the SteamStub retail exe; use the "
                         "DRM-free copy (see docs/getting-started.md)")
    return exe


@contextmanager
def test_lock(what: str, wait: bool = False):
    """Hold the shared test-install lock (a folder, PR2_TEST_LOCK) for the duration."""
    lock = kit_paths.TEST_LOCK
    while True:
        try:
            lock.mkdir(parents=False)
            break
        except FileExistsError:
            try:
                owner = (lock / "owner.txt").read_text().strip()
            except OSError:
                owner = "?"
            if not wait:
                raise SystemExit(f"the test install is in use (lock {lock} held by: {owner}); "
                                 "wait for that run to end, or pass --wait")
            print(f"waiting for the test-install lock ({owner}) ...", flush=True)
            time.sleep(30)
        except FileNotFoundError:
            raise SystemExit(f"cannot create the lock {lock}: its parent folder does not exist "
                             "(set PR2_TEST_LOCK)")
    try:
        (lock / "owner.txt").write_text(f"{what} (pid {os.getpid()}), {time.ctime()}\n")
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


# --- the image ---------------------------------------------------------------------------
def image_layout(exe: Path):
    """(image_base, size_of_image, entry_va, [(va, vsize, characteristics, name)]) from the PE header."""
    d = open(exe, "rb").read(0x1000)
    e = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, e + 6)[0]
    optsz = struct.unpack_from("<H", d, e + 20)[0]
    opt = e + 24
    entry = struct.unpack_from("<I", d, opt + 16)[0]
    base = struct.unpack_from("<I", d, opt + 28)[0]
    size = struct.unpack_from("<I", d, opt + 56)[0]
    secs = []
    for i in range(nsec):
        s = opt + optsz + 40 * i
        name = d[s:s + 8].rstrip(b"\0").decode("latin1")
        vsz, va = struct.unpack_from("<II", d, s + 8)
        ch = struct.unpack_from("<I", d, s + 36)[0]
        secs.append((base + va, vsz, ch, name))
    return base, size, base + entry, secs


# --- addresses and target lists ---------------------------------------------------------------
def norm_va(va) -> str:
    """0x004.., 004.., an int -> canonical 8-upper-hex string (no 0x)."""
    if isinstance(va, int):
        return "%08X" % va
    s = va.strip()
    s = s[2:] if s[:2].lower() == "0x" else s
    return "%08X" % int(s, 16)


_HEX = re.compile(r"^(?:0x)?([0-9A-Fa-f]{6,8})$")


def load_targets(path):
    """Read function entry VAs from a file: a TSV with an `addr` (or `va`) column, or plain
    text with one hex VA first on each line (# comments allowed).
    -> (list of ints in file order, {va: row dict})."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"target list {p} not found (make one with functions.py)")
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    rows, order = {}, []
    head = lines[0].split("\t") if lines else []
    col = next((c for c in ("addr", "va") if c in head), None)
    if col:
        for ln in lines[1:]:
            if not ln.strip():
                continue
            r = dict(zip(head, ln.split("\t")))
            m = _HEX.match(r.get(col, "").strip())
            if m:
                va = int(m.group(1), 16)
                if va not in rows:
                    order.append(va)
                rows[va] = r
    else:
        for ln in lines:
            ln = ln.split("#", 1)[0].strip()
            if not ln:
                continue
            m = _HEX.match(ln.split()[0])
            if m:
                va = int(m.group(1), 16)
                if va not in rows:
                    order.append(va)
                rows[va] = {"addr": "0x%08X" % va}
    return order, rows


def load_va_set(path) -> set:
    return set(load_targets(path)[0]) if path else set()


# --- C source scanning (used by slice_src.py and fuzz.py) -------------------------------------
def match_paren(text, open_paren):
    """Index just past the `)` matching the `(` at `open_paren`, or -1."""
    depth = 0
    for i in range(open_paren, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def match_brace(text, open_brace):
    """Index just past the `}` matching the `{` at `open_brace`, or -1."""
    depth = 0
    for i in range(open_brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1
