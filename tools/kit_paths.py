"""Where the kit's tools find things that are not part of the kit: settings with defaults.

Every setting is an environment variable. Unset, it takes the default below. The defaults put
your own game files in `_game/` inside the kit (git-ignored), and everything the tools write in
`_build/` (git-ignored):

  PR2_BASE_EXE   the DRM-free PR2.exe 1.1.2.3 made from your own copy (sha256 9c9250bc...);
                 read by the disassembly tools and by exe/pe_patch.py
                 default: _game/PR2.exe.unpacked.exe
  PR2_PRISTINE   an unmodified copy of the game folder (the test install is copied from it)
                 default: _game/original
  PR2_WORKDIR    the game's archives, extracted (arcs/, loca/, arct/, arcd/, one per archive)
                 default: _game/extracted
  PR2_TEST_ENV   the disposable test install (test/env.py)
                 default: %USERPROFILE%\\PR2-test-env
  PR2_TEST_EXE   the test exe the live/replay tools start or attach to
                 default: PR2_target.exe in the test install
  PR2_TEST_LOCK  the lock folder test runs share, so only one runs the test install at a time
                 default: the test install's path + ".lock"
  PR2_SAVE_DIR   the game's Save folder (default: Documents\\Ascaron Entertainment\\Port Royale 2\\Save,
                 with Documents where Windows says it is, also when it is redirected)
  PR2_BUILD      where the tools write logs, screenshots, backups and builds
                 default: _build
  MINGW32_BIN    the bin folder of a 32-bit MinGW-w64 GCC
                 default: MSYS2's default C:\\msys64\\mingw32\\bin, else gcc on PATH
  GHIDRA_HOME    a Ghidra install (the folder with support\\analyzeHeadless.bat); no default
  REKO_HOME      a Reko build (the folder with decompile.exe); no default
  VC71_BIN       the bin folder of the Visual C++ Toolkit 2003 (cl 13.10); no default

    python tools/kit_paths.py        prints every setting, where it comes from and whether it exists
"""
import os
import shutil
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]           # the kit's root folder
TOOLS = KIT / "tools"

# The two builds of PR2.exe 1.1.2.3 every address in the docs refers to.
RETAIL_SHA256 = "5295c06689147f7565f72510aeb686719cf0ae38aa581a6df3e4d61d0cf76264"   # Steam, SteamStub
BASE_SHA256 = "9c9250bc2b7a8068f658c0d9935bf2e7d8f9865bc31b875d9fb3b220d01cf448"     # DRM-free


def setting(var: str, default) -> Path:
    v = os.environ.get(var)
    return Path(v).expanduser() if v else (Path(default) if default is not None else None)


def documents() -> Path:
    """The user's Documents folder as Windows knows it (it may be redirected to another folder)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                        ("d", ctypes.c_ubyte * 8)]
        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                   (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


BASE_EXE = setting("PR2_BASE_EXE", KIT / "_game" / "PR2.exe.unpacked.exe")
PRISTINE = setting("PR2_PRISTINE", KIT / "_game" / "original")
WORKDIR = setting("PR2_WORKDIR", KIT / "_game" / "extracted")
TEST_ENV = setting("PR2_TEST_ENV", Path.home() / "PR2-test-env")
TEST_EXE = setting("PR2_TEST_EXE", TEST_ENV / "PR2_target.exe")
TEST_LOCK = setting("PR2_TEST_LOCK", TEST_ENV.with_name(TEST_ENV.name + ".lock"))
SAVE_DIR = setting("PR2_SAVE_DIR", documents() / "Ascaron Entertainment" / "Port Royale 2" / "Save")
BUILD = setting("PR2_BUILD", KIT / "_build")
MINGW32_BIN = setting("MINGW32_BIN", Path(r"C:\msys64\mingw32\bin"))
GHIDRA_HOME = setting("GHIDRA_HOME", None)
REKO_HOME = setting("REKO_HOME", None)
VC71_BIN = setting("VC71_BIN", None)


def gcc() -> Path:
    """A 32-bit MinGW gcc.exe: MINGW32_BIN, else gcc on PATH (which must be an i686 compiler)."""
    if MINGW32_BIN and (MINGW32_BIN / "gcc.exe").exists():
        return MINGW32_BIN / "gcc.exe"
    g = shutil.which("gcc")
    if g:
        return Path(g)
    raise SystemExit("no gcc found: install MSYS2 and run `pacman -S mingw-w64-i686-gcc`, "
                     "or set MINGW32_BIN to the folder that holds a 32-bit gcc.exe")


def need(path: Path, what: str, var: str) -> Path:
    """Exit with a clear message when a setting points nowhere."""
    if path is None or not Path(path).exists():
        raise SystemExit(f"{what} not found at {path}; set {var} (see tools/kit_paths.py)")
    return Path(path)


def main():
    rows = [("PR2_BASE_EXE", BASE_EXE), ("PR2_PRISTINE", PRISTINE), ("PR2_WORKDIR", WORKDIR),
            ("PR2_TEST_ENV", TEST_ENV), ("PR2_TEST_EXE", TEST_EXE),
            ("PR2_TEST_LOCK", TEST_LOCK), ("PR2_SAVE_DIR", SAVE_DIR),
            ("PR2_BUILD", BUILD), ("MINGW32_BIN", MINGW32_BIN), ("GHIDRA_HOME", GHIDRA_HOME),
            ("REKO_HOME", REKO_HOME), ("VC71_BIN", VC71_BIN)]
    for var, p in rows:
        src = "env" if os.environ.get(var) else "default"
        state = "-" if p is None else ("exists" if Path(p).exists() else "missing")
        print(f"{var:<14} {src:<8} {state:<8} {p if p is not None else '(not set)'}")


if __name__ == "__main__":
    main()
