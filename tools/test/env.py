#!/usr/bin/env python3
"""A disposable test install of Port Royale 2, separate from your Steam install.

    python tools/test/env.py setup                 create it from the pristine copy (or verify it)
    python tools/test/env.py status                CLEAN, or what differs from the pristine copy
    python tools/test/env.py deploy FILE... [--to SUBDIR] [--name NAME]   copy your build into it (recorded)
    python tools/test/env.py undeploy              remove what deploy added, restore what it replaced
    python tools/test/env.py backup-saves          snapshot the Save folder and the Run folder
    python tools/test/env.py restore-saves [DIR]   put a snapshot back (default: the newest)
    python tools/test/env.py windowed on|off       FULLSCREEN in settings.cfg (the first change keeps the original)
    python tools/test/env.py fast-boot on|off      park / restore the intro videos
    python tools/test/env.py dpi-aware on|off|list no 2x window scaling on HiDPI screens (test exes only)

Settings (tools/kit_paths.py): PR2_PRISTINE (an unmodified copy of the game folder, made right
after installing from Steam), PR2_BASE_EXE (the DRM-free exe of your own copy; `setup` puts it
in the test install as PR2_target.exe), PR2_TEST_ENV (where the test install lives, default
%USERPROFILE%\\PR2-test-env), PR2_SAVE_DIR, PR2_BACKUP_DIR (default PR2_BUILD\\backup).

The game always writes saves and settings.cfg to your Documents folder, which the test install
SHARES with your Steam install: back them up before every experiment (backup-saves) and put
them back afterwards (restore-saves). `windowed on` keeps the original settings.cfg as
PR2_BACKUP_DIR\\settings.cfg.orig before its first change (restore_settings() puts it back).
Take the test lock (testlock.py) if anything else may use the same test install.

dpi-aware: `on` writes Windows' HIGHDPIAWARE compatibility flag (HKCU\\...\\AppCompatFlags\\Layers,
value "~ HIGHDPIAWARE") for the test exes and records each entry it added in a small state file
(%LOCALAPPDATA%\\pr2-modkit\\dpi_flags.json). `off` removes exactly those entries again (putting back
a value that was there before), and also leftover "~ HIGHDPIAWARE" entries of the tools' own test
exe names (PR2_target.exe and every exe deploy put in) whose file no longer exists, e.g. of a
deleted test install. Entries of other programs are never touched. session.py sets the flag for
the start of the game and removes it once the game runs (the flag is read when a process starts).
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
BACKUPS = kit_paths.BACKUP_DIR
INTRO_VIDEOS = ["Videos/ascaron.wmv", "Videos/nvidia.wmv", "Videos/intro.wmv"]
OWN = {ENV_EXE.name, DEPLOYED.name}       # files of the tools, not drift (a mod may add its log's name)

LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
DPI_FLAG = "~ HIGHDPIAWARE"               # exactly what dpi-aware on writes
DPI_STATE = Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "pr2-modkit" / "dpi_flags.json"
TOOL_EXES = {ENV_EXE.name.lower()}        # test-exe names the tools use (a mod adds its own)


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


def deploy(files, to="", name=None):
    """Copy files into the test install (into SUBDIR `to`; one file may get another `name`) and
    record them. A file of the game that gets replaced is kept and put back by undeploy; a file
    that is not part of the pristine copy (an earlier build) is simply overwritten."""
    files = list(files)
    if name and len(files) != 1:
        raise SystemExit("deploy --name takes exactly one file")
    rec = deployed()
    known = {f["path"] for f in rec["files"]}
    for src in map(Path, files):
        rel = (Path(to) / (name or src.name)).as_posix()
        dst = ENV / rel
        if rel not in known and dst.exists() and (PRISTINE / rel).exists():   # keep the game's file
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
        if dst.suffix.lower() == ".exe":
            dpi_clear(dst)
        if f.get("replaced") and bak.exists():
            shutil.copy2(bak, dst)
            bak.unlink()
            print(f"restored {dst}")
        elif dst.exists():
            dst.unlink()
            print(f"removed  {dst}")
    DEPLOYED.unlink(missing_ok=True)
    if DEPLOY_BACKUP.exists() and not any(DEPLOY_BACKUP.rglob("*.*")):
        shutil.rmtree(DEPLOY_BACKUP)


# --- settings shared with the Steam install -----------------------------------------------
def backup_saves():
    """The Save folder (saves and settings.cfg) and the Run folder (the player's logo files):
    PR2_BACKUP_DIR/saves-<time>/Save and .../Run."""
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


def _layout(backup: Path):
    """(Save copy, Run copy) of a snapshot; also reads the older flat layout (saves + _run/)."""
    if (backup / "Save").is_dir():
        return backup / "Save", backup / "Run"
    return backup, backup / "_run"


def restore_saves(backup=None):
    if backup is None:
        snaps = sorted(BACKUPS.glob("saves-*"))
        if not snaps:
            sys.exit(f"no backups in {BACKUPS}")
        backup = snaps[-1]
    backup = Path(backup)
    save, run = _layout(backup)
    aside = BACKUPS / time.strftime("aside-%Y%m%d-%H%M%S")
    _restore_tree(save, SAVE_DIR, aside / "Save")
    _restore_tree(run, RUN_DIR, aside / "Run")
    print(f"saves restored from {backup}" + (f"; new files kept in {aside}" if aside.exists() else ""))


def restore_run(backup):
    """Only the Run folder (the player's logo files) back from a snapshot; files that appeared
    there since are moved aside."""
    _, run = _layout(Path(backup))
    _restore_tree(run, RUN_DIR, BACKUPS / time.strftime("aside-%Y%m%d-%H%M%S") / "Run")


SETTINGS_ORIG = BACKUPS / "settings.cfg.orig"


def windowed(on):
    """FULLSCREEN in settings.cfg. The first change keeps the original as settings.cfg.orig; an
    orig from an earlier run that did not restore it is kept while the file is still windowed,
    so it is never replaced by the test settings."""
    cfg = SAVE_DIR / "settings.cfg"
    text = cfg.read_text()
    if not SETTINGS_ORIG.exists() or "FULLSCREEN : 0" not in text:
        SETTINGS_ORIG.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg, SETTINGS_ORIG)
    lines = text.splitlines()
    lines = [f"FULLSCREEN : {0 if on else 1}" if l.startswith("FULLSCREEN") else l for l in lines]
    cfg.write_text("\n".join(lines) + "\n")
    print(f"{cfg}: FULLSCREEN = {0 if on else 1}")


def restore_settings():
    """Put back the settings.cfg that windowed() kept."""
    if SETTINGS_ORIG.exists():
        shutil.copy2(SETTINGS_ORIG, SAVE_DIR / "settings.cfg")
        print(f"{SAVE_DIR / 'settings.cfg'} restored")


def fast_boot(on):
    """Skip the start-up videos by parking them (they are pure presentation)."""
    for rel in INTRO_VIDEOS:
        live, parked = ENV / rel, PARKED / rel
        src, dst = (live, parked) if on else (parked, live)
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
    print(f"fast boot {'on' if on else 'off'} (intro videos {'parked' if on else 'restored'})")


# --- the HIGHDPIAWARE flag ------------------------------------------------------------------
def _dpi_state():
    try:
        s = json.loads(DPI_STATE.read_text())
    except (OSError, ValueError):
        s = {}
    return {"added": s.get("added", {}), "names": s.get("names", [])}


def _dpi_save(s):
    DPI_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DPI_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.replace(tmp, DPI_STATE)


def _layers():
    import winreg
    return winreg.CreateKey(winreg.HKEY_CURRENT_USER, LAYERS)


def _get(key, name):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def _key(p) -> str:
    return os.path.normcase(str(p))


def dpi_set(exe: Path):
    """Set the flag for one exe and record it. An entry with exactly our value that is already
    there for an exe of the test install or one of the tools' own test-exe names is taken over
    (earlier tools added it unrecorded);
    any other value is kept in the record and put back by dpi_clear()."""
    import winreg
    s = _dpi_state()
    k, name = _layers(), str(exe)
    cur = _get(k, name)
    if _key(name) not in s["added"]:
        in_env = _key(exe).startswith(_key(ENV) + os.sep)
        if cur == DPI_FLAG and not in_env and exe.name.lower() not in _tool_exes(s):
            return                                       # somebody else's entry with our value
        s["added"][_key(name)] = {"path": name, "previous": None if cur == DPI_FLAG else cur}
    if exe.name.lower() not in s["names"]:
        s["names"].append(exe.name.lower())
    if cur != DPI_FLAG:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, DPI_FLAG)
    _dpi_save(s)


def dpi_clear(exe: Path) -> bool:
    """Remove the entry dpi_set() added for this exe (put back what was there before). Returns
    whether it changed the registry. An entry that is not in the record is left alone."""
    import winreg
    s = _dpi_state()
    rec = s["added"].pop(_key(exe), None)
    if rec is None:
        return False
    k, changed = _layers(), False
    if _get(k, rec["path"]) == DPI_FLAG:                 # still ours (nobody changed it since)
        if rec["previous"] is None:
            winreg.DeleteValue(k, rec["path"])
        else:
            winreg.SetValueEx(k, rec["path"], 0, winreg.REG_SZ, rec["previous"])
        changed = True
    _dpi_save(s)
    return changed


def _tool_exes(s=None):
    s = s or _dpi_state()
    return TOOL_EXES | {f["path"].rsplit("/", 1)[-1].lower() for f in deployed()["files"]
                        if f["path"].lower().endswith(".exe")} | set(s["names"])


def dpi_entries():
    """Every Layers value: (path, data)."""
    import winreg
    k, out, i = _layers(), [], 0
    while True:
        try:
            name, data, _ = winreg.EnumValue(k, i)
        except OSError:
            return out
        out.append((name, data))
        i += 1


def dpi_cleanup(all_installs=False):
    """Remove what the tools added: the recorded entries of this test install's exes (with
    all_installs, of every install), recorded entries whose exe no longer exists, and unrecorded
    "~ HIGHDPIAWARE" leftovers of the tools' own test-exe names whose exe no longer exists."""
    import winreg
    removed = []
    s = _dpi_state()
    env = _key(ENV) + os.sep
    for kk, rec in list(s["added"].items()):
        mine = kk.startswith(env)
        if all_installs or mine or not Path(rec["path"]).exists():
            if dpi_clear(Path(rec["path"])):
                removed.append(rec["path"])
    names = _tool_exes()
    k = _layers()
    for name, data in dpi_entries():
        if data == DPI_FLAG and Path(name).name.lower() in names and not Path(name).exists():
            winreg.DeleteValue(k, name)
            removed.append(name + " (leftover, exe gone)")
    return removed


def dpi_aware(on, also=()):
    """Windows compatibility flag for the test exes only (and `also`, exes of the test install
    about to start): without it, a 200%-scaled display doubles the (DPI-unaware) game window to
    fill the whole screen."""
    exes = test_exes()
    exes += [Path(e) for e in also if Path(e).exists() and Path(e) not in exes]
    if on:
        for exe in exes:
            dpi_set(exe)
        print(f"HIGHDPIAWARE set for {[e.name for e in exes]}")
    else:
        removed = dpi_cleanup()
        print(f"HIGHDPIAWARE removed: {removed or 'nothing the tools added'}")


def dpi_list():
    s = _dpi_state()
    added = {r["path"] for r in s["added"].values()}
    for name, data in dpi_entries():
        if data == DPI_FLAG or name in added:
            print(f"{'added by the tools' if name in added else 'not recorded':<20} {data!r:<18} {name}")


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
    ap.add_argument("--name", help="deploy: the file's name in the test install (one file)")
    ap.add_argument("--all", action="store_true", help="dpi-aware off: the entries added for every test install")
    a = ap.parse_args(argv)
    onoff = a.cmd in ("windowed", "fast-boot", "dpi-aware")
    allowed = ("on", "off", "list") if a.cmd == "dpi-aware" else ("on", "off")
    if onoff and (len(a.args) != 1 or a.args[0] not in allowed):
        ap.error(f"{a.cmd} {'|'.join(allowed)}")
    if a.cmd == "setup":
        return 0 if setup() else 1
    if a.cmd == "status":
        return 0 if status() else 1
    if a.cmd == "deploy":
        if not a.args:
            ap.error("deploy FILE...")
        deploy(a.args, a.to, a.name)
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
    elif a.args[0] == "list":
        dpi_list()
    elif a.args[0] == "on":
        dpi_aware(True)
    else:
        removed = dpi_cleanup(a.all)
        print(f"HIGHDPIAWARE removed: {removed or 'nothing the tools added'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
