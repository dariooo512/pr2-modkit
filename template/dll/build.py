"""Build the template mod DLL with 32-bit MinGW GCC (MSYS2 mingw32).

    python template/dll/build.py            ->  template/_build/<MOD_NAME>.dll

MOD_NAME is read from dll/mod.h. The compiler is looked up in this order:
  1. the folder in the environment variable MINGW32_BIN,
  2. C:\\msys64\\mingw32\\bin (the MSYS2 default),
  3. gcc on PATH (it must be a 32-bit, i686 compiler).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE.parent / "_build"


def mod_name() -> str:
    m = re.search(r'#define\s+MOD_NAME\s+"([^"]+)"', (HERE / "mod.h").read_text(encoding="utf-8"))
    if not m:
        sys.exit("dll/mod.h: no #define MOD_NAME \"...\"")
    return m.group(1)


def find_gcc() -> Path:
    for d in (os.environ.get("MINGW32_BIN"), r"C:\msys64\mingw32\bin"):
        if d and (Path(d) / "gcc.exe").exists():
            return Path(d) / "gcc.exe"
    g = shutil.which("gcc")
    if g:
        return Path(g)
    sys.exit("no gcc found: install MSYS2 and run `pacman -S mingw-w64-i686-gcc`, "
             "or set MINGW32_BIN to the folder that holds a 32-bit gcc.exe")


def main() -> int:
    name = mod_name()
    out = BUILD / f"{name}.dll"
    BUILD.mkdir(parents=True, exist_ok=True)
    gcc = find_gcc()
    srcs = sorted(str(p) for p in HERE.glob("*.c"))
    cmd = [str(gcc), "-m32", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
           "-shared", "-static", "-static-libgcc", "-o", str(out), *srcs,
           "-Wl,--enable-stdcall-fixup", "-luser32"]
    env = dict(os.environ, PATH=str(gcc.parent) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sys.stdout.write(r.stdout + r.stderr)
    if r.returncode == 0:
        print(f"built {out} ({out.stat().st_size} bytes)")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
