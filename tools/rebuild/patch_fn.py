#!/usr/bin/env python3
"""Drop raw, position-independent machine code (a .bin from extract_fn.py) over a
function in a COPY of the exe, padding the rest of the original body with int3.

    python patch_fn.py 0x0041C6F0 my_func.bin --size 74 --out _build/rebuild/PR2_test.exe

--size is the ORIGINAL function length (from your disassembler), not a scan for int3
padding: functions are often packed back to back with no int3 between them, and a scan
runs into the next function and corrupts it. The code must fit; the input exe is never
written (--out must be a different file). For code with calls or global references use
build.py + relink.py, which fix up the addresses.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_image  # noqa: E402
from pe_image import kit_paths  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("va", help="function VA")
    ap.add_argument("code", help="the .bin with the new machine code")
    ap.add_argument("--size", type=lambda s: int(s, 0), required=True,
                    help="the ORIGINAL function length in bytes")
    ap.add_argument("--out", required=True, help="output exe (never the input)")
    ap.add_argument("--exe", default=kit_paths.BASE_EXE, help="input exe (default: PR2_BASE_EXE)")
    a = ap.parse_args()

    exe = kit_paths.need(a.exe, "input exe", "PR2_BASE_EXE")
    pe_image.refuse_overwrite(exe, a.out)
    img = pe_image.Image(exe)
    va = pe_image.parse_va(a.va)
    if not img.is_code(va):
        raise SystemExit(f"0x{va:08X} is not in an executable section")
    if not Path(a.code).exists():
        raise SystemExit(f"no such file: {a.code}")
    new = Path(a.code).read_bytes()
    off = img.va_to_off(va)
    print(f"0x{va:08X} in {img.section_name(va)}, file offset 0x{off:X}, body {a.size} B, "
          f"new code {len(new)} B")
    if len(new) > a.size:
        raise SystemExit(f"new code {len(new)} B > body {a.size} B: in place only, it must fit")
    data = bytearray(img.data)
    data[off:off + len(new)] = new
    data[off + len(new):off + a.size] = b"\xCC" * (a.size - len(new))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(bytes(data))
    print(f"wrote {a.out} ({len(new)} B + {a.size - len(new)} B int3)")


if __name__ == "__main__":
    main()
