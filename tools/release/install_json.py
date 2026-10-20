"""A mod's install.json: what its release needs (read by export_patches.py and build_release.py).

Every path is relative to the folder of install.json (the mod's folder). Keys:

  name                 the mod's name for players: setup window, HOW TO INSTALL, logs   (required)
  id                   the name used in file names: <id>.install.json and _<id>_backup in the game
                       folder, <id>_<version> for the release folder (default: name without spaces
                       and punctuation)
  version              the release's version (default: v<yyyy.mm.dd>-<git commit>)
  dll                  the mod's DLL as built, e.g. "_build/HelloMod.dll"; installed under its own
                       file name next to PR2.exe, and named in the patch data's `dll` record (required)
  data_dir             loose files installed at the same relative path in the game folder
                       (README.md files are left out); optional
  patch_data           where export_patches.py writes the patch data, e.g.
                       "_build/loader/HelloMod.patches"; the loader reads every *.patches next to
                       the exe, so the file name must be the mod's own (required)
  patches              the exe patch modules (pe_patch.py modules, globs allowed) the loader applies
                       in memory, in order; [] when the mod needs no exe patch (the loader loads
                       the DLL either way). Default []
  section_name         the new section's name in the file build (default ".mod")
  section_size         its size (default 0x1000)
  section_va           where the loader puts it (default: right after the exe's last section,
                       0x9F7000, which every mod with a section shares: two mods whose sections
                       overlap cannot run together, so a second mod picks another address)
  supported_exe_sha256 the PR2.exe builds the setup accepts: a list of sha256, or {sha256: label}
                       (default: the Steam 1.1.2.3 exe and its DRM-free image)
  build                python scripts (relative paths, optionally with arguments) run before the
                       release is put together, e.g. ["dll/build.py"]
  docs                 files copied into the release at the same relative path (README, changelog)
  logs                 files the mod writes into the game folder while playing; Uninstall removes
                       them (default: "<dll name>.log")
  warn_if_present      [{"glob": "towns/*.tow", "question": "... {n} ... Install anyway?",
                         "refusal": "..."}]: files of another mod the setup asks about
  setup                {"title": window title, "image": a PNG shown at the top of the window
                        (at most about 600x300), "dgvoodoo": true to offer dgVoodoo2's D3D8 files}
  how_to               a text file with the player's HOW TO INSTALL ({name}, {version} are filled
                       in); default: the kit's generic text
  how_to_banner        an image shown above HOW TO INSTALL.html (it must be one of the docs)
  check_ignore         glob patterns of files in the test install that are the mod's own (the
                       release check skips them when it hashes the game's files)
  release_dir          where releases go (default "_build/release")
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

RETAIL_SHA256 = "5295c06689147f7565f72510aeb686719cf0ae38aa581a6df3e4d61d0cf76264"
BASE_SHA256 = "9c9250bc2b7a8068f658c0d9935bf2e7d8f9865bc31b875d9fb3b220d01cf448"
DEFAULT_EXES = {RETAIL_SHA256: "Port Royale 2 1.1.2.3 (Steam)", BASE_SHA256: "Port Royale 2 1.1.2.3 (DRM-free)"}


class ConfigError(Exception):
    pass


def _int(v, default):
    if v is None:
        return default
    return int(v, 0) if isinstance(v, str) else int(v)


class Config:
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        if self.path.is_dir():
            self.path = self.path / "install.json"
        try:
            j = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"{self.path}: {e}")
        self.raw = j
        self.root = self.path.parent
        for key in ("name", "dll", "patch_data"):
            if not j.get(key):
                raise ConfigError(f"{self.path}: '{key}' is missing")
        self.name = j["name"]
        self.id = j.get("id") or re.sub(r"[^A-Za-z0-9_-]", "", self.name)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ConfigError(f"{self.path}: id {self.id!r} must be letters, digits, '_' or '-'")
        self.version = j.get("version")
        self.dll = self.root / j["dll"]
        self.dll_name = self.dll.name
        self.data_dir = self.root / j["data_dir"] if j.get("data_dir") else None
        self.patch_data = self.root / j["patch_data"]
        if self.patch_data.suffix.lower() != ".patches":
            raise ConfigError(f"{self.path}: patch_data must end in .patches")
        self.patch_globs = list(j.get("patches") or [])
        self.section_name = j.get("section_name") or ".mod"
        self.section_size = _int(j.get("section_size"), 0x1000)
        self.section_va = _int(j.get("section_va"), None)
        exes = j.get("supported_exe_sha256") or DEFAULT_EXES
        if isinstance(exes, list):
            exes = {h: DEFAULT_EXES.get(h, "a supported PR2.exe") for h in exes}
        self.exes = {h.lower(): label for h, label in exes.items()}
        self.build = [b.split() if isinstance(b, str) else list(b) for b in j.get("build") or []]
        self.docs = list(j.get("docs") or [])
        self.logs = list(j.get("logs") or [Path(self.dll_name).stem + ".log"])
        self.warn = list(j.get("warn_if_present") or [])
        s = j.get("setup") or {}
        self.title = s.get("title") or f"{self.name} setup"
        self.image = self.root / s["image"] if s.get("image") else None
        self.dgvoodoo_option = bool(s.get("dgvoodoo"))
        self.how_to = self.root / j["how_to"] if j.get("how_to") else None
        self.how_to_banner = j.get("how_to_banner")
        self.check_ignore = list(j.get("check_ignore") or [])
        self.release_dir = self.root / (j.get("release_dir") or "_build/release")

    def patch_modules(self) -> list[Path]:
        out = []
        for g in self.patch_globs:
            hits = sorted(self.root.glob(g))
            if not hits:
                raise ConfigError(f"{self.path}: patches entry {g!r} matches no file")
            out += [h for h in hits if h not in out]
        return out

    def data_files(self) -> dict[str, Path]:
        """{relative game path with backslashes: source} of the loose data files."""
        out = {}
        if self.data_dir and self.data_dir.exists():
            for f in sorted(self.data_dir.rglob("*")):
                if f.is_file() and f.name.lower() != "readme.md":
                    out[str(f.relative_to(self.data_dir)).replace("/", "\\")] = f
        return out

    def ignored(self, rel: str) -> bool:
        r = rel.replace("\\", "/")
        return any(fnmatch.fnmatch(r.lower(), p.lower()) for p in self.check_ignore)
