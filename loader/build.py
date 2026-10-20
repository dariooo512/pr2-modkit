"""Build the loader, version.dll (loader.c), with the 32-bit MinGW GCC (setting MINGW32_BIN).

    python loader/build.py [--out FILE]      default: _build/loader/version.dll in the kit

One loader serves every mod: it reads each *.patches file next to the game exe (README.md).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import kit_paths  # noqa: E402

OUT = kit_paths.BUILD / "loader" / "version.dll"


def build(out: Path = OUT) -> int:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    gcc_exe = kit_paths.gcc()
    cmd = [str(gcc_exe), "-m32", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-shared", "-static",
           "-static-libgcc", "-o", str(out), str(HERE / "loader.c"), str(HERE / "sha256.c"), str(HERE / "version.def"),
           "-Wl,--enable-stdcall-fixup"]
    env = dict(os.environ, PATH=str(gcc_exe.parent) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sys.stdout.write(r.stdout + r.stderr)
    if r.returncode == 0:
        if b"PR2-LOADER " not in out.read_bytes():
            print("error: the built loader lacks its PR2-LOADER identification string", file=sys.stderr)
            return 1
        print(f"built {out} ({out.stat().st_size} bytes)")
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    return build(ap.parse_args(argv).out)


if __name__ == "__main__":
    sys.exit(main())
