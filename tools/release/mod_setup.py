"""The setup of a Port Royale 2 mod: installs it into the player's own game (one program for every mod).

    <Mod> Setup.exe [--game DIR]          the setup window (DIR: preselect that folder instead of Steam's)
    <Mod> Setup.exe status    [--game DIR]
    <Mod> Setup.exe install   [--game DIR] [--force] [--dgvoodoo]    (also updates an older version)
    <Mod> Setup.exe repair    [--game DIR]
    <Mod> Setup.exe uninstall [--game DIR]

Everything about the mod comes from the folder "mod" next to this program: its MANIFEST.json
(written by build_release.py from the mod's install.json) names the mod, the window title and
image, the PR2.exe builds the mod is made for and every file with its sha256.

What it does to the game folder:
  * adds the mod's files: the loader (version.dll), the mod's DLL, its patch data <Mod>.patches
    and its loose data files; optionally dgVoodoo2's D3D8 wrapper;
  * never changes PR2.exe or any save; a game file it has to replace is backed up first to
    _<id>_backup\\;
  * records what it did in <id>.install.json, so Uninstall puts the folder back exactly as it was
    and Install over an older version updates it in place.
Several mods share the game folder (techniques/09-releasing-a-mod.md):
  * the loader is one file for every mod: Install keeps the newest loader (each mod's setup
    carries one), Uninstall leaves it while another mod's <id>.install.json still lists it, and
    the last mod's Uninstall removes it (or puts back what was there before the first mod);
  * any other file that another mod installed too: shared if it is the same file (dgVoodoo2),
    refused if it differs (two mods cannot both own include.ini);
  * an older loader that cannot read every mod's patch data (Sovereign Nations' first one, which
    reads only PR2Nation.patches) is replaced by this one, and the other mod's install record is
    updated to the new file.
The game is started as always (from Steam); the loader applies each mod in memory and leaves a
mod out if the game build is not the one it was made for (loader/README.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

LOADER = "version.dll"
LOADER_LOGS = ("PR2Loader.log",)
STEAM_APP = "12470"
DEFAULT_EXES = {
    "5295c06689147f7565f72510aeb686719cf0ae38aa581a6df3e4d61d0cf76264": "Port Royale 2 1.1.2.3 (Steam)",
    "9c9250bc2b7a8068f658c0d9935bf2e7d8f9865bc31b875d9fb3b220d01cf448": "Port Royale 2 1.1.2.3 (DRM-free)",
}


class SetupError(Exception):
    pass


def here() -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent)


def package_dir() -> Path:
    for base in (Path(sys.executable).parent if getattr(sys, "frozen", False) else None, here(), here().parent):
        if base and (base / "mod" / "MANIFEST.json").exists():
            return base / "mod"
    raise SetupError("the folder 'mod' with MANIFEST.json is missing next to the setup program")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def loader_version(p: Path) -> int | None:
    """The version of a PR2 mod loader (version.dll), None if the file is something else."""
    try:
        data = p.read_bytes()
    except OSError:
        return None
    m = re.search(rb"PR2-LOADER (\d+)", data)
    if m:
        return int(m.group(1))
    if b"PR2Nation.patches" in data and b"PR2Loader.log" in data:
        return 1                                   # Sovereign Nations' first loader (PR2Nation.patches only)
    return None


def norm(rel: str) -> str:
    return rel.replace("/", "\\").lower()


# ---- the package --------------------------------------------------------------------------------
class Package:
    """mod/MANIFEST.json: what this setup installs."""

    def __init__(self, pkg: Path | None = None, dgvoodoo: bool = False):
        self.dir = pkg or package_dir()
        try:
            m = json.loads((self.dir / "MANIFEST.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SetupError(f"the package's MANIFEST.json cannot be read ({e}): download it again")
        self.name = m.get("name") or "the mod"
        self.id = m.get("id") or re.sub(r"[^A-Za-z0-9_-]", "", self.name) or "Mod"
        self.version = m["version"]
        self.title = m.get("title") or f"{self.name} setup"
        self.header = m.get("header")
        self.exes = m.get("exes") or DEFAULT_EXES
        self.files = dict(m["files"])
        self.dgv_files = dict(m.get("dgvoodoo") or {})
        self.dgv_option = bool(m.get("dgvoodoo_option", bool(self.dgv_files)))
        if dgvoodoo:
            self.files.update(self.dgv_files)
        self.shared = {norm(r) for r in m.get("shared", [LOADER] if LOADER in self.files else [])}
        self.loader_version = m.get("loader_version") or (loader_version(self.dir / LOADER) if LOADER in self.files else None)
        self.logs = list(m.get("logs") or [])
        self.warn = list(m.get("warn_if_present") or [])
        for rel in self.files:
            if Path(rel).name.lower() == "pr2.exe" or ".." in Path(rel).parts or Path(rel).is_absolute():
                raise SetupError(f"the package lists {rel!r}: refusing")
        self.state = f"{self.id}.install.json"
        self.backup = f"_{self.id}_backup"


# ---- finding the game ------------------------------------------------------------------------
def steam_root() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                p = Path(winreg.QueryValueEx(k, val)[0])
                if p.exists():
                    return p
        except OSError:
            continue
    return None


def steam_libraries(root: Path) -> list[Path]:
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p not in libs:
            libs.append(p)
    return libs


def find_game() -> Path | None:
    """The Steam install of Port Royale 2 (app 12470), read-only."""
    root = steam_root()
    if not root:
        return None
    for lib in steam_libraries(root):
        acf = lib / "steamapps" / f"appmanifest_{STEAM_APP}.acf"
        if acf.exists():
            m = re.search(r'"installdir"\s+"([^"]+)"', acf.read_text(encoding="utf-8", errors="replace"))
            if m:
                for d in (lib / "steamapps" / "common" / m.group(1), lib / "steamapps" / "common" / m.group(1) / "Game"):
                    if (d / "PR2.exe").exists():
                        return d
    return None


def identify(game: Path, pkg: Package) -> str:
    exe = game / "PR2.exe"
    if not exe.exists():
        raise SetupError(f"{game} has no PR2.exe: choose the folder that contains PR2.exe")
    name = pkg.exes.get(sha256(exe))
    if not name:
        raise SetupError(f"This PR2.exe is a game build {pkg.name} was not made for (or it was changed). "
                         f"{pkg.name} needs Port Royale 2 1.1.2.3 as sold on Steam. Nothing was installed.")
    return name


# ---- install state -------------------------------------------------------------------------------
def load_state(game: Path, pkg: Package) -> dict | None:
    p = game / pkg.state
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupError(f"{p} is damaged; it says how to undo the installation, so fix or restore it first")


def save_state(path: Path, st: dict):
    path.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf-8")


class Other:
    """Another mod's install record in the same game folder."""

    def __init__(self, path: Path, st: dict):
        self.path, self.st = path, st
        stem = path.name[:-len(".install.json")]
        self.name = st.get("name") or stem
        self.backup = st.get("backup") or f"_{stem}_backup"
        self.files = {norm(r): r for r in st["files"]}

    def entry(self, rel: str) -> dict | None:
        r = self.files.get(norm(rel))
        return self.st["files"][r] if r is not None else None

    def save(self):
        save_state(self.path, self.st)


def others(game: Path, pkg: Package) -> list[Other]:
    out = []
    for p in sorted(game.glob("*.install.json")):
        if p.name.lower() == pkg.state.lower():
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and isinstance(st.get("files"), dict):
            out.append(Other(p, st))
    return out


def users(oth: list[Other], rel: str) -> list[Other]:
    return [o for o in oth if o.entry(rel) is not None]


def other_mod_files(game: Path, w: dict) -> list[Path]:
    return sorted(p for p in game.glob(w["glob"]) if p.is_file())


def _remove_file(game: Path, pkg: Package, rel: str, entry: dict, oth: list[Other], log):
    """Take one of our files out: leave it to another mod that uses it too (with the original
    we backed up, if any), else restore the original or delete it."""
    dst, bak = game / rel, game / pkg.backup / rel
    still = users(oth, rel)
    if still:
        heir = still[0]
        if entry.get("replaced") and bak.exists():
            to = game / heir.backup / rel
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak, to)
            heir.entry(rel)["replaced"] = True
            heir.save()
        log(f"kept {rel} (also used by {heir.name})")
        return
    if entry.get("replaced"):
        if bak.exists():
            shutil.copy2(bak, dst)
            log(f"restored {rel}")
        else:
            log(f"WARNING: the original of {rel} is missing from the backup; left as it is")
    elif dst.exists():
        dst.unlink()
        log(f"removed {rel}")


def _prune_dirs(game: Path, dirs: list[str], log):
    for rel in sorted(dirs, key=lambda d: -len(Path(d).parts)):
        d = game / rel
        try:
            d.rmdir()                                    # only if empty
            log(f"removed folder {rel}")
        except OSError:
            pass


def _loader_ok(pkg: Package, dst: Path) -> bool:
    """A loader at least as new as ours is in place (another mod may have put a newer one)."""
    v = loader_version(dst) if dst.exists() else None
    return v is not None and pkg.loader_version is not None and v >= pkg.loader_version


def install(game: Path, force=False, dgvoodoo=False, log=print, confirm=None, pkg: Package | None = None) -> str:
    pkg = pkg or Package(dgvoodoo=dgvoodoo)
    name = identify(game, pkg)
    for w in pkg.warn:
        found = other_mod_files(game, w)
        if found and not force:
            msg = w.get("question", "{dir} has {n} files of another mod. Install anyway?").format(
                n=len(found), dir=game / Path(w["glob"]).parent)
            if not (confirm and confirm(msg)):
                raise SetupError("not installed: " + w.get("refusal", "another mod's files are present (use --force)"))
    st = load_state(game, pkg) or {"files": {}, "dirs": []}
    old_files = st["files"]
    oth = others(game, pkg)
    # files another mod installed too: the same file is shared, a different one is a conflict
    clash = []
    for rel, want in sorted(pkg.files.items()):
        if norm(rel) in pkg.shared:
            continue
        for o in users(oth, rel):
            dst = game / rel
            if dst.exists() and sha256(dst) != want:
                clash.append(f"{rel} (installed by {o.name})")
    if clash:
        raise SetupError(f"not installed: {pkg.name} and another mod both install these files, with different "
                         "contents: " + ", ".join(clash) + ". Only one of the two mods can be installed.")
    # update: files of the old version that the new one no longer has
    for rel in [r for r in old_files if r not in pkg.files]:
        _remove_file(game, pkg, rel, old_files[rel], oth, log)
        del old_files[rel]
    for rel, want in sorted(pkg.files.items()):
        src, dst = pkg.dir / rel, game / rel
        if not src.exists() or sha256(src) != want:
            raise SetupError(f"the package is damaged: {rel} is missing or changed (download it again)")
        entry = old_files.get(rel)
        owners = users(oth, rel)
        fresh_backup = False
        if entry is None:
            entry = {"replaced": dst.exists() and not owners}
            if entry["replaced"]:
                bak = game / pkg.backup / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
                fresh_backup = True
                log(f"backed up {rel}")
            parent = Path(rel).parent
            while str(parent) not in (".", ""):
                if not (game / parent).exists() and str(parent) not in st["dirs"]:
                    st["dirs"].append(str(parent))
                parent = parent.parent
        dst.parent.mkdir(parents=True, exist_ok=True)
        if norm(rel) in pkg.shared:
            entry["shared"] = True
            if not fresh_backup and _loader_ok(pkg, dst):
                pass                                     # the same or a newer loader: keep it
            else:
                old = loader_version(dst) if dst.exists() else None
                shutil.copy2(src, dst)
                if old is not None and owners:
                    log(f"replaced the loader {rel} (version {old}) with version {pkg.loader_version}: "
                        f"it serves {', '.join(o.name for o in owners)} too")
                for o in owners:                         # their record follows the new file
                    o.entry(rel)["sha256"] = want
                    o.entry(rel)["shared"] = True
                    o.save()
        elif not dst.exists() or sha256(dst) != want:
            shutil.copy2(src, dst)
        entry["sha256"] = sha256(dst)
        old_files[rel] = entry
    previous = st.get("version")
    st.update(version=pkg.version, game=name, files=old_files, name=pkg.name, backup=pkg.backup, format=2)
    save_state(game / pkg.state, st)
    log(f"{pkg.name} {pkg.version} {'updated from ' + previous if previous and previous != pkg.version else 'installed'} "
        f"for {name}: {len(old_files)} files. Start the game from Steam as always.")
    return pkg.version


def _file_ok(pkg: Package, game: Path, rel: str, entry: dict) -> bool:
    dst = game / rel
    if entry.get("shared") or norm(rel) in pkg.shared:
        return _loader_ok(pkg, dst) or (dst.exists() and sha256(dst) == entry["sha256"])
    return dst.exists() and sha256(dst) == entry["sha256"]


def repair(game: Path, log=print, pkg: Package | None = None) -> int:
    pkg = pkg or Package()
    st = load_state(game, pkg)
    if not st:
        raise SetupError(f"{pkg.name} is not installed in {game}")
    identify(game, pkg)
    n = 0
    for rel, entry in st["files"].items():
        if _file_ok(pkg, game, rel, entry):
            continue
        dst, src = game / rel, pkg.dir / rel
        if not src.exists() or (sha256(src) != entry["sha256"] and not entry.get("shared")):
            raise SetupError(f"{rel} cannot be repaired from this package (version {st['version']} needed)")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        entry["sha256"] = sha256(dst)
        log(f"repaired {rel}")
        n += 1
    if n:
        save_state(game / pkg.state, st)
    log(f"repair: {n} file(s) put back" if n else "repair: every file is in order")
    return n


def uninstall(game: Path, log=print, pkg: Package | None = None):
    pkg = pkg or Package()
    st = load_state(game, pkg)
    if not st:
        raise SetupError(f"{pkg.name} is not installed in {game}")
    oth = others(game, pkg)
    for rel, entry in st["files"].items():
        _remove_file(game, pkg, rel, entry, oth, log)
    shutil.rmtree(game / pkg.backup, ignore_errors=True)
    (game / pkg.state).unlink()
    extra = list(pkg.logs) + ([] if users(oth, LOADER) else list(LOADER_LOGS))   # written while playing
    for name in extra:
        try:
            (game / name).unlink()
        except OSError:
            pass
    _prune_dirs(game, st.get("dirs", []), log)
    log(f"{pkg.name} removed; the game folder is as it was before"
        + (f" ({', '.join(o.name for o in oth)} stays installed)." if oth else "."))


def status(game: Path, pkg: Package | None = None) -> str:
    pkg = pkg or Package()
    st = load_state(game, pkg)
    try:
        name = identify(game, pkg)
    except SetupError as e:
        name = str(e)
    oth = others(game, pkg)
    also = f"\nAlso installed: {', '.join(o.name for o in oth)}." if oth else ""
    if not st:
        return f"{game}\n{name}\n{pkg.name} is not installed.{also}"
    bad = [r for r, e in st["files"].items() if not _file_ok(pkg, game, r, e)]
    return (f"{game}\n{name}\n{pkg.name} {st['version']} installed ({len(st['files'])} files)"
            + (f"; {len(bad)} file(s) changed or missing: use Repair" if bad else ".") + also)


# ---- the window ----------------------------------------------------------------------------------
def gui(game_dir: Path | None = None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    root = tk.Tk()
    try:
        pkg = Package()
    except SetupError as e:
        root.withdraw()
        messagebox.showerror("Setup", str(e))
        return
    app = pkg.name
    root.title(pkg.title)
    game = tk.StringVar(value=str(game_dir or find_game() or ""))
    dgv = tk.BooleanVar(value=False)
    if pkg.header:
        try:                                         # the header is decoration: the setup works without it
            root.header = tk.PhotoImage(file=str(here() / pkg.header))
            tk.Label(root, image=root.header, borderwidth=0).pack(side="top", pady=(10, 0))
        except (tk.TclError, OSError):
            pass
    frm = tk.Frame(root, padx=12, pady=10)
    frm.pack(fill="both", expand=True)
    tk.Label(frm, text="Port Royale 2 folder (the one with PR2.exe):").grid(row=0, column=0, columnspan=3, sticky="w")
    tk.Entry(frm, textvariable=game, width=70).grid(row=1, column=0, columnspan=2, sticky="we")

    def browse():
        d = filedialog.askdirectory(title="The Port Royale 2 folder (with PR2.exe)")
        if d:
            game.set(d)
            show()
    tk.Button(frm, text="Browse...", command=browse).grid(row=1, column=2, padx=6)
    if pkg.dgv_option:
        tk.Checkbutton(frm, text="Also install dgVoodoo2 (only if the game hangs on start with a black screen)",
                       variable=dgv).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
    out = scrolledtext.ScrolledText(frm, width=84, height=14)
    out.grid(row=3, column=0, columnspan=3, pady=6)

    def log(s):
        out.insert("end", s + "\n")
        out.see("end")
        root.update_idletasks()

    def show():
        out.delete("1.0", "end")
        if game.get():
            log(status(Path(game.get()), pkg))
        else:
            log("Steam's Port Royale 2 was not found: choose its folder with Browse.")

    def run(fn):
        try:
            fn()
        except SetupError as e:
            log(str(e))
            messagebox.showerror(app, str(e))
        except OSError as e:
            log(f"error: {e}")
            messagebox.showerror(app, f"{e}\n\nIs the game still running? Close it and try again.")
    btns = tk.Frame(frm)
    btns.grid(row=4, column=0, columnspan=3)
    tk.Button(btns, text="Install / Update", width=16, command=lambda: run(lambda: install(
        Path(game.get()), dgvoodoo=dgv.get(), log=log, confirm=lambda m: messagebox.askyesno(app, m),
        pkg=Package(dgvoodoo=dgv.get())))).pack(side="left", padx=4)
    tk.Button(btns, text="Repair", width=12, command=lambda: run(lambda: repair(Path(game.get()), log=log, pkg=pkg))).pack(side="left", padx=4)
    tk.Button(btns, text="Uninstall", width=12, command=lambda: run(
        lambda: messagebox.askyesno(app, f"Remove {app}? Your saves stay.") and uninstall(Path(game.get()), log=log, pkg=pkg))).pack(side="left", padx=4)
    tk.Button(btns, text="Close", width=10, command=root.destroy).pack(side="left", padx=4)
    show()
    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        gui()
        return 0
    ap = argparse.ArgumentParser(description="the mod's setup")
    ap.add_argument("action", nargs="?", choices=["status", "install", "repair", "uninstall"],
                    help="without an action: the setup window")
    ap.add_argument("--game", type=Path, help="the folder with PR2.exe (default: the Steam install)")
    ap.add_argument("--force", action="store_true", help="install even with another mod's files present")
    ap.add_argument("--dgvoodoo", action="store_true", help="also install dgVoodoo2's D3D8 wrapper")
    a = ap.parse_args(argv)
    if a.action is None:
        gui(a.game)
        return 0
    game = a.game or find_game()
    if not game:
        print("Steam's Port Royale 2 was not found: give its folder with --game")
        return 2
    try:
        if a.action == "status":
            print(status(game))
        elif a.action == "install":
            install(game, force=a.force, dgvoodoo=a.dgvoodoo)
        elif a.action == "repair":
            repair(game)
        else:
            uninstall(game)
    except SetupError as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
