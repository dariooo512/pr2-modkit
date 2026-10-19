#!/usr/bin/env python3
"""A disposable test install of Port Royale 2, separate from your Steam install.

    python tools/test/env.py setup                 create it from the pristine copy (or verify it)
    python tools/test/env.py status                CLEAN, or what differs from the pristine copy
    python tools/test/env.py deploy FILE... [--to SUBDIR]   copy your build into it (recorded)
    python tools/test/env.py undeploy              remove what deploy added, restore what it replaced
    python tools/test/env.py backup-saves          snapshot the Save folder and the Run folder
    python tools/test/env.py restore-saves [DIR]   put a snapshot back (default: the newest)
    python tools/test/env.py windowed on|off       FULLSCREEN in settings.cfg
    python tools/test/env.py fast-boot on|off      park / restore the intro videos
    python tools/test/env.py dpi-aware on|off      no 2x window scaling on HiDPI screens (test exes only)

Settings (tools/kit_paths.py): PR2_PRISTINE (an unmodified copy of the game folder, made right
after installing from Steam), PR2_BASE_EXE (the DRM-free exe of your own copy; `setup` puts it
in the test install as PR2_target.exe), PR2_TEST_ENV (where the test install lives, default
%USERPROFILE%\\PR2-test-env), PR2_SAVE_DIR, PR2_BUILD (backups go to PR2_BUILD\\backup).

The game always writes saves and settings.cfg to your Documents folder, which the test install
SHARES with your Steam install: back them up before every experiment (backup-saves) and put
them back afterwards (restore-saves). Take the test lock (testlock.py) if anything else may use
the same test install.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

PRISTINE = kit_paths.PRISTINE
TARGET_EXE = kit_paths.BASE_EXE
TARGET_SHA = kit_paths.BASE_SHA256
RETAIL_SHA = kit_paths.RETAIL_SHA256
ENV = kit_paths.TEST_ENV
LOCK = kit_paths.TEST_LOCK
ENV_EXE = ENV / "PR2_target.exe"          # the unmodified DRM-free exe
DEPLOYED = ENV / "_kit_deployed.json"     # what `deploy` added or replaced
DEPLOY_BACKUP = ENV / "_kit_backup"       # originals that `deploy` replaced
PARKED = ENV / "_parked"                  # files moved aside by fast-boot
SAVE_DIR = kit_paths.SAVE_DIR
RUN_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Ascaron Entertainment" / "Port Royale 2" / "Run"
BUILD = kit_paths.BUILD
BACKUPS = BUILD / "backup"
INTRO_VIDEOS = ["Videos/ascaron.wmv", "Videos/nvidia.wmv", "Videos/intro.wmv"]
OWN = {ENV_EXE.name, DEPLOYED.name}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_files(root):
    return {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()}


def deployed():
    try:
        return json.loads(DEPLOYED.read_text())
    except (OSError, ValueError):
        return {"files": []}


def test_exes():
    """The exes a test may start: PR2_target.exe and every deployed .exe."""
    exes = [ENV_EXE] + [ENV / f["path"] for f in deployed()["files"] if f["path"].lower().endswith(".exe")]
    return [e for e in exes if e.exists()]


# --- the install ------------------------------------------------------------------------
def drift():
    want, have = rel_files(PRISTINE), rel_files(ENV)
    parked = {p.relative_to(PARKED).as_posix() for p in PARKED.rglob("*") if p.is_file()} \
        if PARKED.exists() else set()
    ours = OWN | {f["path"] for f in deployed()["files"]}
    have = {f: s for f, s in have.items()
            if not f.startswith(("_parked/", "_kit_backup/")) and f not in ours}
    extra = sorted(set(have) - set(want))
    missing = sorted(set(want) - set(have) - parked)
    resized = sorted(f for f in set(want) & set(have) if want[f] != have[f])
    return extra, missing, resized, sorted(parked)


def setup():
    kit_paths.need(PRISTINE / "PR2.exe", "the pristine game copy", "PR2_PRISTINE")
    kit_paths.need(TARGET_EXE, "the DRM-free exe", "PR2_BASE_EXE")
    if sha256(PRISTINE / "PR2.exe") != RETAIL_SHA:
        sys.exit(f"the pristine copy is not the Steam 1.1.2.3 build: {PRISTINE / 'PR2.exe'}")
    if sha256(TARGET_EXE) != TARGET_SHA:
        sys.exit(f"the DRM-free exe has the wrong sha256: {TARGET_EXE}")
    if any(p.lower() == "steamapps" for p in ENV.resolve().parts):
        sys.exit(f"refusing to use {ENV} as a test install: it is inside a Steam library")
    if not ENV.exists():
        print(f"copying {PRISTINE} -> {ENV} ...")
        shutil.copytree(PRISTINE, ENV)
    shutil.copy2(TARGET_EXE, ENV_EXE)
    return status()


def status():
    if not ENV.exists():
        sys.exit(f"no test install at {ENV}; run setup")
    extra, missing, resized, parked = drift()
    exe_ok = ENV_EXE.exists() and sha256(ENV_EXE) == TARGET_SHA
    print(f"test install: {ENV}")
    print(f"target:    {ENV_EXE.name} {'OK (unmodified DRM-free 1.1.2.3)' if exe_ok else 'MISSING/MODIFIED'}")
    print(f"deployed:  {[f['path'] for f in deployed()['files']] or 'none'}")
    if parked:
        print(f"parked:    {parked}")
    print(f"extra:     {extra or 'none'}")
    print(f"missing:   {missing or 'none'}")
    print(f"resized:   {resized or 'none'}")
    clean = exe_ok and not (extra or missing or resized)
    print("VERDICT:  ", "CLEAN" if clean else "DIRTY")
    return clean


def deploy(files, to=""):
    rec = deployed()
    known = {f["path"] for f in rec["files"]}
    for src in map(Path, files):
        rel = (Path(to) / src.name).as_posix()
        dst = ENV / rel
        if rel not in known and dst.exists():          # keep the file we replace
            bak = DEPLOY_BACKUP / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if rel not in known:
            rec["files"].append({"path": rel, "replaced": (DEPLOY_BACKUP / rel).exists()})
            known.add(rel)
        print(f"deployed {src} -> {dst}  sha256 {sha256(dst)[:16]}")
    DEPLOYED.write_text(json.dumps(rec, indent=1))


def undeploy():
    rec = deployed()
    for f in reversed(rec["files"]):
        dst, bak = ENV / f["path"], DEPLOY_BACKUP / f["path"]
        if f.get("replaced") and bak.exists():
            shutil.copy2(bak, dst)
            bak.unlink()
            print(f"restored {dst}")
        elif dst.exists():
            dst.unlink()
            print(f"removed  {dst}")
        dpi_aware_one(dst, False)
    DEPLOYED.unlink(missing_ok=True)
    if DEPLOY_BACKUP.exists() and not any(DEPLOY_BACKUP.rglob("*.*")):
        shutil.rmtree(DEPLOY_BACKUP)


# --- settings shared with the Steam install -----------------------------------------------
def backup_saves():
    """The Save folder (saves and settings.cfg) and the Run folder (the player's logo files)."""
    dest = BACKUPS / time.strftime("saves-%Y%m%d-%H%M%S")
    shutil.copytree(SAVE_DIR, dest / "Save")
    if RUN_DIR.exists():
        shutil.copytree(RUN_DIR, dest / "Run")
    print(f"saves backed up -> {dest}")
    return dest


def _restore_tree(src: Path, live: Path, aside: Path):
    """Make `live` equal `src`; files that are only in `live` are moved to `aside`, not deleted."""
    if not src.exists():
        return
    live.mkdir(parents=True, exist_ok=True)
    want = rel_files(src)
    for rel in rel_files(live):
        if rel not in want:
            (aside / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(live / rel), str(aside / rel))
            print(f"moved aside (new since the backup): {rel}")
    for rel in want:
        a, b = src / rel, live / rel
        if not b.exists() or sha256(a) != sha256(b):
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a, b)


def restore_saves(backup=None):
    if backup is None:
        snaps = sorted(BACKUPS.glob("saves-*"))
        if not snaps:
            sys.exit(f"no backups in {BACKUPS}")
        backup = snaps[-1]
    backup = Path(backup)
    aside = BACKUPS / time.strftime("aside-%Y%m%d-%H%M%S")
    _restore_tree(backup / "Save", SAVE_DIR, aside / "Save")
    _restore_tree(backup / "Run", RUN_DIR, aside / "Run")
    print(f"saves restored from {backup}" + (f"; new files kept in {aside}" if aside.exists() else ""))


def windowed(on):
    cfg = SAVE_DIR / "settings.cfg"
    lines = cfg.read_text().splitlines()
    lines = [f"FULLSCREEN : {0 if on else 1}" if l.startswith("FULLSCREEN") else l for l in lines]
    cfg.write_text("\n".join(lines) + "\n")
    print(f"{cfg}: FULLSCREEN = {0 if on else 1}")


def fast_boot(on):
    """Skip the start-up videos by parking them (they are pure presentation)."""
    for rel in INTRO_VIDEOS:
        live, parked = ENV / rel, PARKED / rel
        src, dst = (live, parked) if on else (parked, live)
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
    print(f"fast boot {'on' if on else 'off'} (intro videos {'parked' if on else 'restored'})")


def dpi_aware_one(exe: Path, on: bool):
    import winreg
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers")
    if on:
        winreg.SetValueEx(key, str(exe), 0, winreg.REG_SZ, "~ HIGHDPIAWARE")
    else:
        try:
            winreg.DeleteValue(key, str(exe))
        except FileNotFoundError:
            pass


def dpi_aware(on):
    """Windows compatibility flag for the test exes only: without it, a 200%-scaled display
    doubles the (DPI-unaware) game window to fill the whole screen."""
    exes = test_exes()
    for exe in exes:
        dpi_aware_one(exe, on)
    print(f"HIGHDPIAWARE {'set' if on else 'cleared'} for {[e.name for e in exes]}")


def mute_sound():
    """Keep test games silent: start tools/test/mute_game.ps1 -Watch (the volume mixer's mute for
    the test exes, never the Steam PR2.exe) unless one already runs; it stops by itself after
    10 minutes without a test game."""
    pid_file = BUILD / "mute_watch.pid"
    try:
        pid = int(pid_file.read_text())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        if str(pid) in out:
            return
    except (OSError, ValueError):
        pass
    p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(Path(__file__).resolve().parent / "mute_game.ps1"), "-Watch", "-IdleMinutes", "10"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000)              # CREATE_NO_WINDOW
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(p.pid))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("cmd", choices=["setup", "status", "deploy", "undeploy", "backup-saves", "restore-saves",
                                    "windowed", "fast-boot", "dpi-aware"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--to", default="", help="deploy: subfolder of the test install")
    a = ap.parse_args(argv)
    onoff = a.cmd in ("windowed", "fast-boot", "dpi-aware")
    if onoff and (len(a.args) != 1 or a.args[0] not in ("on", "off")):
        ap.error(f"{a.cmd} on|off")
    if a.cmd == "setup":
        return 0 if setup() else 1
    if a.cmd == "status":
        return 0 if status() else 1
    if a.cmd == "deploy":
        if not a.args:
            ap.error("deploy FILE...")
        deploy(a.args, a.to)
    elif a.cmd == "undeploy":
        undeploy()
    elif a.cmd == "backup-saves":
        backup_saves()
    elif a.cmd == "restore-saves":
        restore_saves(a.args[0] if a.args else None)
    elif a.cmd == "windowed":
        windowed(a.args[0] == "on")
    elif a.cmd == "fast-boot":
        fast_boot(a.args[0] == "on")
    elif a.cmd == "dpi-aware":
        dpi_aware(a.args[0] == "on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
