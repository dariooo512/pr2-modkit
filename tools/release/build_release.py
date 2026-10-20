#!/usr/bin/env python3
"""Build a mod's public release from its install.json: the setup program and the mod's files, no game file.

    python tools/release/build_release.py <mod>/install.json [--dgvoodoo DIR] [--no-check]
           [--game-dir DIR ...] [--workdir DIR] [--base EXE] [--no-build] [--version V]

Steps (techniques/09-releasing-a-mod.md):
  1. runs the mod's build scripts (install.json `build`), builds the loader (loader/build.py)
     and exports the patch data with --check (export_patches.py);
  2. writes <release_dir>/<id>_<version>/ and a zip of it:
       <name> Setup.exe   the setup (mod_setup.py frozen with PyInstaller when it is installed;
                          otherwise mod_setup.py + "<name> Setup.bat", which need Python 3), with
                          the window image install.json `setup.image` bundled in
       mod/               version.dll (the loader), the mod's DLL, its .patches and the loose data
                          files, and MANIFEST.json: the mod's name, title, supported PR2.exe builds
                          and the sha256 of each file (what the setup reads)
       HOW TO INSTALL.txt / .html, VERSION.txt, and the files install.json `docs` lists
  3. the game-file check (default on): hashes every file of the game folders (--game-dir, default
     the settings PR2_PRISTINE and PR2_TEST_ENV that exist) and of the extracted archives
     (--workdir, default PR2_WORKDIR) and refuses to build if any release file equals one of them,
     or if the release contains a game file type (.cpr, .pr2, PR2.exe, any .exe but the setup).
--dgvoodoo DIR adds dgVoodoo2's unmodified D3D8.dll / D3DImm.dll / DDraw.dll and dgVoodoo.conf from
an official dgVoodoo2 release folder (its licence lets a game mod ship them unmodified); the setup
installs them only when the player ticks the box (install.json setup.dgvoodoo).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import kit_paths  # noqa: E402
from install_json import Config, ConfigError  # noqa: E402
import mod_setup  # noqa: E402  (loader_version: the same test the setup makes)

DGV_FILES = ("D3D8.dll", "D3DImm.dll", "DDraw.dll", "dgVoodoo.conf")
SETUP = HERE / "mod_setup.py"

HOW_TO = r"""{name_upper} {version}
HOW TO INSTALL
==============================================

WHAT YOU NEED
- Port Royale 2 from Steam, installed (version 1.1.2.3, the one Steam sells).
- Windows 10 or 11.

INSTALL
1. Extract this zip to any folder (right-click -> Extract All...).
2. Double-click "{name} Setup".
3. The setup finds your Steam copy of Port Royale 2 by itself (or click Browse and pick the
   folder that contains PR2.exe). Click "Install / Update".
4. Start Port Royale 2 from Steam as always.

The mod does not change PR2.exe or your saves. If Steam updates the game to a build the mod
does not know, the game simply starts without the mod.

OTHER MODS
Mods made with the Port Royale 2 modding kit share one loader (version.dll) and can be
installed side by side, unless two of them change the same part of the game: then the loader
starts the game with the first one only and says why in PR2Loader.log.

UPDATE
Run the setup of the new version and click "Install / Update". No need to uninstall first.

UNINSTALL
Run the setup and click "Uninstall". The game folder is put back exactly as it was; saves stay.

REPAIR
If a mod file was deleted or changed, run the setup and click "Repair".

Logs, if something goes wrong: PR2Loader.log{logs} in the game folder.
"""

HOW_TO_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}: how to install</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:1200px;margin:0 auto;padding:16px;background:#fff;color:#111}}
img{{width:100%;height:auto}} pre{{white-space:pre-wrap;font:15px/1.5 Segoe UI,Arial,sans-serif}}</style></head>
<body>{banner}
<pre>{text}</pre></body></html>
"""

SETUP_BAT = r"""@echo off
rem {name} setup (needs Python 3 from python.org when there is no Setup.exe)
python "%~dp0mod_setup.py" %*
if errorlevel 9009 echo Python 3 is needed: https://www.python.org/downloads/ & pause
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(root: Path) -> str:
    r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h", "--abbrev=8"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "nogit"


def loader_path(cfg: Config) -> Path:
    return cfg.patch_data.parent / "version.dll"


def build_parts(cfg: Config, base: Path | None, version: str):
    for step in cfg.build:
        script, *args = step
        subprocess.run([sys.executable, str(cfg.root / script), *args], check=True, cwd=cfg.root)
    subprocess.run([sys.executable, str(KIT / "loader" / "build.py"), "--out", str(loader_path(cfg))], check=True)
    cmd = [sys.executable, str(HERE / "export_patches.py"), str(cfg.path), "--check", "--version", version]
    if base:
        cmd += ["--base", str(base)]
    subprocess.run(cmd, check=True)


def mod_files(cfg: Config) -> dict[str, Path]:
    files = {"version.dll": loader_path(cfg), cfg.dll_name: cfg.dll, cfg.patch_data.name: cfg.patch_data}
    for rel, src in cfg.data_files().items():
        if rel.lower() in {k.lower() for k in files}:
            raise ConfigError(f"data file {rel} has the name of one of the mod's own files")
        files[rel] = src
    for rel, src in files.items():
        if not src.exists():
            raise ConfigError(f"{src} is missing (the build did not make it?)")
    return files


def game_hashes(dirs, workdir: Path | None, ours=(), ignore=()) -> dict:
    """sha256 -> name of every file of the game: the given game folders without what mods put
    there (the release's own files, any loader, patch data, install records and backups, and the
    install.json check_ignore patterns), and the contents of the game's extracted archives."""
    ours = {r.replace("/", "\\").lower() for r in ours}
    skip = ["version.dll", "*.patches", "*.install.json", "pr2loader.log", "_parked\\*", "_*_backup\\*",
            *[p.replace("/", "\\").lower() for p in ignore]]
    out = {}
    for d in dirs:
        d = Path(d)
        for f in d.rglob("*") if d.exists() else []:
            if not f.is_file() or f.stat().st_size == 0:
                continue
            rel = str(f.relative_to(d)).lower()
            if rel in ours or any(fnmatch.fnmatch(rel, p) for p in skip):
                continue
            out.setdefault(sha256(f), str(f.relative_to(d)))
    for f in workdir.rglob("*") if workdir and workdir.exists() else []:
        if f.is_file() and f.stat().st_size > 0:
            out.setdefault(sha256(f), "archive: " + str(f.relative_to(workdir)))
    return out


def freeze(cfg: Config, out: Path) -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        shutil.copy2(SETUP, out / "mod_setup.py")
        if cfg.image:
            shutil.copy2(cfg.image, out / "header.png")
        (out / f"{cfg.name} Setup.bat").write_text(SETUP_BAT.format(name=cfg.name), encoding="ascii")
        return False
    work = cfg.root / "_build" / "pyinstaller"
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", "--noconfirm", "--name", f"{cfg.name} Setup",
           "--distpath", str(out), "--workpath", str(work), "--specpath", str(work)]
    if cfg.image:
        stage = work / "image"
        stage.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg.image, stage / "header.png")
        cmd += ["--add-data", f"{stage / 'header.png'}{os.pathsep}."]
    subprocess.run(cmd + [str(SETUP)], check=True)
    return True


def build(cfg: Config, dgvoodoo: Path | None = None, check=True, game_dirs=None, workdir=None,
          base: Path | None = None, run_build=True, version: str | None = None) -> Path:
    version = version or cfg.version or f"v{time.strftime('%Y.%m.%d')}-{git_commit(cfg.root)}"
    if run_build:
        build_parts(cfg, base, version)
    out = cfg.release_dir / f"{cfg.id}_{version}"
    shutil.rmtree(out, ignore_errors=True)
    mod = out / "mod"
    files = mod_files(cfg)
    loader_v = mod_setup.loader_version(files["version.dll"])
    if not loader_v:
        raise ConfigError(f"{files['version.dll']} is not a PR2 loader")
    man = {"format": 2, "name": cfg.name, "id": cfg.id, "version": version, "title": cfg.title,
           "header": "header.png" if cfg.image else None, "exes": cfg.exes, "files": {}, "dgvoodoo": {},
           "dgvoodoo_option": cfg.dgvoodoo_option, "shared": ["version.dll"], "loader_version": loader_v,
           "logs": cfg.logs, "warn_if_present": cfg.warn}
    for rel, src in files.items():
        dst = mod / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        man["files"][rel] = sha256(dst)
    if dgvoodoo:
        for n in DGV_FILES:
            src = next(iter(Path(dgvoodoo).rglob(n)), None)
            if src:
                shutil.copy2(src, mod / n)
                man["dgvoodoo"][n] = sha256(mod / n)
    (mod / "MANIFEST.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    frozen = freeze(cfg, out)
    logs = "".join(f" and {n}" for n in cfg.logs)
    if cfg.how_to:
        how_to = cfg.how_to.read_text(encoding="utf-8").format(name=cfg.name, version=version)
    else:
        how_to = HOW_TO.format(name=cfg.name, name_upper=cfg.name.upper(), version=version, logs=logs)
    (out / "HOW TO INSTALL.txt").write_text(how_to.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")
    banner = (f'<img src="{html.escape(cfg.how_to_banner)}" alt="{html.escape(cfg.name)}, a Port Royale 2 mod">'
              if cfg.how_to_banner else "")
    (out / "HOW TO INSTALL.html").write_text(HOW_TO_HTML.format(title=html.escape(cfg.name), banner=banner,
                                                                text=html.escape(how_to)), encoding="utf-8")
    for doc in cfg.docs:
        (out / doc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg.root / doc, out / doc)
    (out / "VERSION.txt").write_text(f"{cfg.name} {version}\r\n", encoding="utf-8")

    if check:
        if game_dirs is None:
            game_dirs = [d for d in (kit_paths.PRISTINE, kit_paths.TEST_ENV) if d and d.exists()]
        if workdir is None:
            workdir = kit_paths.WORKDIR
        if not game_dirs and not (workdir and workdir.exists()):
            raise SystemExit("the game-file check has no game folder to compare with: set PR2_PRISTINE or "
                             "PR2_TEST_ENV and PR2_WORKDIR (tools/kit_paths.py), or pass --no-check")
        game = game_hashes(game_dirs, workdir, files, cfg.check_ignore)
        bad = []
        for f in out.rglob("*"):
            if not f.is_file():
                continue
            low = f.suffix.lower()
            if low in (".cpr", ".pr2") or f.name.lower() == "pr2.exe" or (low == ".exe" and not
                                                                            (frozen and f.parent == out and f.name == f"{cfg.name} Setup.exe")):
                bad.append(f"{f.relative_to(out)}: a game file type")
            h = sha256(f)
            if h in game:
                bad.append(f"{f.relative_to(out)} == game file {game[h]}")
        if bad:
            raise SystemExit("release refused, it contains game files:\n  " + "\n  ".join(bad))
        print(f"check: none of {sum(1 for f in out.rglob('*') if f.is_file())} release files is one of the "
              f"game's {len(game)} files")
    z = out.parent / (out.name + ".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out.parent))
    print(f"{z} ({z.stat().st_size // 1024} KB), setup {'exe' if frozen else 'script (no PyInstaller)'}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("install_json", type=Path, help="the mod's install.json (or its folder)")
    ap.add_argument("--dgvoodoo", type=Path, help="folder of an official dgVoodoo2 release (MS\\x86 files)")
    ap.add_argument("--no-check", action="store_true", help="skip the game-file check")
    ap.add_argument("--game-dir", type=Path, action="append",
                    help="a game folder to compare with (repeatable; default PR2_PRISTINE and PR2_TEST_ENV)")
    ap.add_argument("--workdir", type=Path, help="the extracted archives (default PR2_WORKDIR)")
    ap.add_argument("--base", type=Path, help="the DRM-free PR2.exe for the patch export (default PR2_BASE_EXE)")
    ap.add_argument("--no-build", action="store_true", help="use the built files as they are")
    ap.add_argument("--version", help="default: install.json version, else v<date>-<commit>")
    a = ap.parse_args(argv)
    try:
        cfg = Config(a.install_json)
        build(cfg, a.dgvoodoo, not a.no_check, a.game_dir, a.workdir, a.base, not a.no_build, a.version)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: {' '.join(map(str, e.cmd))} failed ({e.returncode})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
