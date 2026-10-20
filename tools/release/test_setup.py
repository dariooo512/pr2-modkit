#!/usr/bin/env python3
"""Tests of the setup (mod_setup.py) and the loader's rules for several mods, on throw-away folders.

    python tools/release/test_setup.py [--release DIR]

DIR is a release made by build_release.py (default: the template's newest, template/_build/release).
Each test builds a small fake game folder in _build/setup_test with a copy of PR2.exe (the Steam
build, from the settings PR2_PRISTINE or PR2_TEST_ENV) and two vanilla files, installs, updates and
uninstalls one or more mods with the setup and compares the folder byte for byte. The other mods are
made from the release's package (renamed copies), so only the kit and one release are needed.
Never touches a real install and never starts the game.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import kit_paths  # noqa: E402
import mod_setup as S  # noqa: E402
import export_patches as X  # noqa: E402
from install_json import Config  # noqa: E402

ROOT = kit_paths.BUILD / "setup_test"
results = []


def record(test, ok, detail):
    results.append((test, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {test:<7} {detail}", flush=True)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tree(d: Path) -> dict:
    return {str(f.relative_to(d)): sha(f.read_bytes()) for f in sorted(d.rglob("*")) if f.is_file()} | \
           {str(f.relative_to(d)) + "/": "dir" for f in sorted(d.rglob("*")) if f.is_dir()}


def steam_exe() -> Path:
    for d in (kit_paths.PRISTINE, kit_paths.TEST_ENV):
        if d and (d / "PR2.exe").exists() and S.sha256(d / "PR2.exe") == kit_paths.RETAIL_SHA256:
            return d
    raise SystemExit("no Steam PR2.exe 1.1.2.3 found: set PR2_PRISTINE or PR2_TEST_ENV (tools/kit_paths.py)")


def fake_game(name: str, src: Path) -> Path:
    g = ROOT / name / "Game"
    shutil.rmtree(g.parent, ignore_errors=True)
    (g / "scripts").mkdir(parents=True)
    for n in ("PR2.exe", "A2dLib-2.20A.dll", "lua.dll"):
        shutil.copy2(src / n, g / n)
    (g / "scripts" / "SOMETHING_800x600.screen").write_bytes(b"vanilla screen")
    return g


def variant(base: Path, mod_id: str, extra: dict[str, bytes] | None = None, loader: bytes | None = None) -> Path:
    """A copy of the release package `base` as another mod: its DLL, patch data and records renamed,
    `extra` data files added, optionally another loader."""
    m = json.loads((base / "MANIFEST.json").read_text(encoding="utf-8"))
    out = ROOT / "pkg" / mod_id / "mod"
    shutil.rmtree(out.parent, ignore_errors=True)
    out.mkdir(parents=True)
    old = m["id"]
    files = {}
    for rel in m["files"]:
        data = (base / rel).read_bytes()
        new_rel = rel.replace(old, mod_id)
        if rel.endswith(".patches"):
            lines = data.decode("ascii").splitlines()
            lines = [l.replace(old, mod_id) if l.startswith(("name ", "dll ")) else l for l in lines[:-1]]
            body = "\n".join(lines) + "\n"
            data = (body + "end " + sha(body.encode("ascii")) + "\n").encode("ascii")
        if rel == S.LOADER and loader is not None:
            data = loader
        files[new_rel] = data
    files.update(extra or {})
    for rel, data in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(data)
    v = S.loader_version(out / S.LOADER)
    m.update(name=mod_id, id=mod_id, title=f"{mod_id} setup", files={r: sha(d) for r, d in files.items()},
             loader_version=v, logs=[f"{mod_id}.log"])
    (out / "MANIFEST.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return out


def latest_release() -> Path:
    rels = sorted((KIT / "template" / "_build" / "release").glob("*/mod/MANIFEST.json"), key=lambda p: p.stat().st_mtime)
    if not rels:
        raise SystemExit("no template release: python tools/release/build_release.py template/install.json")
    return rels[-1].parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", type=Path)
    a = ap.parse_args()
    rel = a.release or latest_release()
    base = rel / "mod"
    src = steam_exe()
    log = []
    hello = S.Package(base)
    loader2 = (base / S.LOADER).read_bytes()
    v2 = S.loader_version(base / S.LOADER)
    print(f"release {rel.name} ({hello.name}), loader version {v2}")

    # SET-01: one mod, install / status / uninstall
    g = fake_game("one", src)
    before = tree(g)
    S.install(g, log=log.append, pkg=hello)
    ok = all((g / r).exists() for r in hello.files) and "installed" in S.status(g, hello)
    (g / "PR2Loader.log").write_text("written while playing")
    (g / f"{hello.id}.log").write_text("written while playing")
    S.uninstall(g, log=log.append, pkg=hello)
    record("SET-01", ok and tree(g) == before, f"{hello.name}: install, status, uninstall byte-identical")

    # SET-02/03: two mods share the loader; uninstall in either order
    other = S.Package(variant(base, "OtherMod", {"lua\\other.ini": b"-- OtherMod\n"}))
    for test, order in (("SET-02", (hello, other)), ("SET-03", (other, hello))):
        g = fake_game(test, src)
        before = tree(g)
        S.install(g, log=log.append, pkg=hello)
        S.install(g, log=log.append, pkg=other)
        st_o = json.loads((g / other.state).read_text())
        shared = st_o["files"][S.LOADER].get("shared") and not st_o["files"][S.LOADER].get("replaced")
        first, second = order
        (g / "PR2Loader.log").write_text("log")
        S.uninstall(g, log=log.append, pkg=first)
        kept = (g / S.LOADER).exists() and (g / "PR2Loader.log").exists() and \
            "changed or missing" not in S.status(g, second) and all((g / r).exists() for r in second.files)
        S.uninstall(g, log=log.append, pkg=second)
        record(test, shared and kept and tree(g) == before,
               f"{hello.name} + {other.name}: one loader; uninstall {first.name} keeps it for {second.name}, "
               f"then {second.name}: byte-identical")

    # SET-04: a version.dll that was there before any mod: backed up by the first, handed over
    g = fake_game("foreign", src)
    (g / S.LOADER).write_bytes(b"someone else's version.dll")
    before = tree(g)
    S.install(g, log=log.append, pkg=hello)
    S.install(g, log=log.append, pkg=other)
    S.uninstall(g, log=log.append, pkg=hello)
    handed = json.loads((g / other.state).read_text())["files"][S.LOADER].get("replaced") and \
        (g / other.backup / S.LOADER).exists() and S.loader_version(g / S.LOADER) == v2
    S.uninstall(g, log=log.append, pkg=other)
    record("SET-04", handed and tree(g) == before,
           "a foreign version.dll: backed up, the backup handed to the other mod, restored by the last uninstall")

    # SET-05: the newest loader wins, whatever the order
    newer = S.Package(variant(base, "NewerMod", loader=loader2.replace(b"PR2-LOADER %d" % v2, b"PR2-LOADER %d" % (v2 + 1))))
    g = fake_game("newer", src)
    before = tree(g)
    S.install(g, log=log.append, pkg=hello)
    S.install(g, log=log.append, pkg=newer)
    up = S.loader_version(g / S.LOADER) == v2 + 1 and "changed or missing" not in S.status(g, hello)
    S.install(g, log=log.append, pkg=hello)                 # an update of the older mod keeps the newer loader
    kept = S.loader_version(g / S.LOADER) == v2 + 1 and S.repair(g, log=log.append, pkg=hello) == 0
    S.uninstall(g, log=log.append, pkg=newer)
    S.uninstall(g, log=log.append, pkg=hello)
    record("SET-05", up and kept and tree(g) == before,
           f"loader {v2} then {v2 + 1}: the newer stays (the older mod's record follows it, its update and repair keep it)")

    # SET-06: two mods that install the same data file with different contents: refused
    clash = S.Package(variant(base, "ClashMod", {"lua\\other.ini": b"-- ClashMod's own\n"}))
    g = fake_game("clash", src)
    S.install(g, log=log.append, pkg=other)
    before = tree(g)
    try:
        S.install(g, log=log.append, pkg=clash)
        refused = False
    except S.SetupError as e:
        refused = "lua\\other.ini" in str(e) and "OtherMod" in str(e)
    record("SET-06", refused and tree(g) == before, "the same data file with other contents: refused, nothing changed")
    S.uninstall(g, log=log.append, pkg=other)

    # SET-07: the same file with the same contents (dgVoodoo2's D3D8.dll): shared
    dgv = {"D3D8.dll": b"the same wrapper"}
    a1, a2 = S.Package(variant(base, "ModA", dgv)), S.Package(variant(base, "ModB", dgv))
    g = fake_game("same", src)
    before = tree(g)
    S.install(g, log=log.append, pkg=a1)
    S.install(g, log=log.append, pkg=a2)
    S.uninstall(g, log=log.append, pkg=a1)
    kept = (g / "D3D8.dll").exists()
    S.uninstall(g, log=log.append, pkg=a2)
    record("SET-07", kept and tree(g) == before, "the same file in two mods: kept until the last uninstall")

    # SET-08: migration: a game folder with Sovereign Nations' first loader and its install record
    g = fake_game("migrate", src)
    v1 = b"MZ fake first loader: opens PR2Nation.patches, logs to PR2Loader.log"
    (g / S.LOADER).write_bytes(v1)
    (g / "PR2Nation.patches").write_text("PR2NATION-PATCHDATA 1\n")
    sn = {"files": {S.LOADER: {"replaced": False, "sha256": sha(v1)},
                    "PR2Nation.patches": {"replaced": False, "sha256": S.sha256(g / "PR2Nation.patches")}},
          "dirs": [], "version": "v2026.10.20-old", "game": "Port Royale 2 1.1.2.3 (Steam)"}
    (g / "SovereignNations.install.json").write_text(json.dumps(sn))
    S.install(g, log=log.append, pkg=hello)
    sn2 = json.loads((g / "SovereignNations.install.json").read_text())
    migrated = S.loader_version(g / S.LOADER) == v2 and sn2["files"][S.LOADER]["sha256"] == S.sha256(g / S.LOADER) \
        and (g / "PR2Nation.patches").read_text() == "PR2NATION-PATCHDATA 1\n"
    S.uninstall(g, log=log.append, pkg=hello)
    kept = S.loader_version(g / S.LOADER) == v2 and not (g / hello.state).exists()
    record("SET-08", migrated and kept,
           "Sovereign Nations' first loader (PR2Nation.patches only) replaced by the generic one, its record "
           "updated, PR2Nation.patches untouched; HelloMod's uninstall leaves the loader to it")

    # SET-09: the loader's rule for overlapping patch data (its Python model, export_patches.conflict)
    tpl = Config(KIT / "template" / "install.json")
    tpl.patch_globs = ["patches/load_mod_dll.py"]           # the entry-point detour of the file route
    base_exe = kit_paths.BASE_EXE
    if base_exe and base_exe.exists():
        d_none = X.parse((base / f"{hello.id}.patches").read_text())
        d_entry = X.parse(X.export(tpl, "test", base_exe)[0])
        c1, c2 = X.conflict(d_none, d_entry), X.conflict(d_entry, d_entry)
        record("SET-09", c1 is None and c2 == 0x64E03F,
               f"patch data without patches overlaps nothing; two entry detours overlap at {c2:#x}: the later is refused")
    else:
        record("SET-09", True, "the overlap model: skipped (no DRM-free base exe, setting PR2_BASE_EXE)")
    shutil.rmtree(ROOT, ignore_errors=True)
    n = sum(ok for _, ok, _ in results)
    print(f"setup tests: {n} pass, {len(results) - n} fail")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
