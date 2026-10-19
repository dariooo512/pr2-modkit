"""Shared helpers for the rebuild tools: read a PE image by virtual address, find the
compiler, parse the `VA:OBJ:LEN` target syntax, and refuse to overwrite an input exe.

Not a command; the other tools in this folder import it.
"""
import hashlib
import os
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

# The 64-bit integer helpers the compiler calls by name (`__allmul` and friends) live at
# fixed addresses inside PR2.exe 1.1.2.3 (sha256 kit_paths.BASE_SHA256). A rebuilt
# function that does 64-bit arithmetic references them by name; these are their VAs in
# that one build. For any other binary pass --symbol NAME=VA instead.
PR2_RUNTIME_HELPERS = {
    "_allshr": 0x0064DF40,
    "_alldiv": 0x0064DE90,
    "_allmul": 0x0064DF70,
}


class Image:
    """A PE file loaded once; addresses are virtual addresses (ImageBase included)."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise SystemExit(f"no such file: {self.path}")
        self.data = self.path.read_bytes()
        self.pe = pefile.PE(data=self.data, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.lo = self.base
        self.hi = self.base + self.pe.OPTIONAL_HEADER.SizeOfImage
        self._sha = None

    def sha256(self):
        if self._sha is None:
            self._sha = hashlib.sha256(self.data).hexdigest()
        return self._sha

    def section(self, va):
        for s in self.pe.sections:
            sva = self.base + s.VirtualAddress
            if sva <= va < sva + max(s.Misc_VirtualSize, s.SizeOfRawData):
                return s
        return None

    def section_name(self, va):
        s = self.section(va)
        return s.Name.rstrip(b"\x00").decode("latin1") if s else "?"

    def is_code(self, va):
        s = self.section(va)
        return bool(s and s.Characteristics & 0x20000000)      # IMAGE_SCN_MEM_EXECUTE

    def va_to_off(self, va):
        s = self.section(va)
        if s is None:
            raise SystemExit(f"VA 0x{va:08X} is not inside any section of {self.path.name}")
        rel = va - (self.base + s.VirtualAddress)
        if rel >= s.SizeOfRawData:
            raise SystemExit(f"VA 0x{va:08X} lies in the uninitialised tail of its section")
        return s.PointerToRawData + rel

    def read(self, va, n):
        """n bytes at va, cut at the end of the section's raw data."""
        s = self.section(va)
        if s is None:
            raise SystemExit(f"VA 0x{va:08X} is not inside any section of {self.path.name}")
        off = self.va_to_off(va)
        end = min(off + n, s.PointerToRawData + s.SizeOfRawData)
        return self.data[off:end]

    def imports(self):
        """{IAT slot VA: 'DLL!name'} (parses the import directory on first use)."""
        if not hasattr(self, "_imports"):
            self._imports = {}
            self.pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            for d in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
                dll = d.dll.decode(errors="replace")
                for imp in d.imports:
                    name = imp.name.decode(errors="replace") if imp.name else f"#{imp.ordinal}"
                    self._imports[imp.address] = f"{dll}!{name}"
        return self._imports

    def runtime_helpers(self):
        """The built-in name->VA table for this image (only for PR2.exe 1.1.2.3)."""
        return dict(PR2_RUNTIME_HELPERS) if self.sha256() == kit_paths.BASE_SHA256 else {}


def guess_size(img, va, limit=0x4000):
    """Guess a function's length by decoding forward from va until a ret / jmp / int3 that
    no earlier branch jumps past. A heuristic for when you have no size from a
    disassembler: check it against the listing. Returns 0 when nothing decodes."""
    code = img.read(va, limit)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    furthest = va
    end = va
    for ins in md.disasm(code, va):
        end = ins.address + ins.size
        m = ins.mnemonic
        if m.startswith("j") or m.startswith("loop"):
            try:
                t = int(ins.op_str, 16)
                if va < t < va + limit:
                    furthest = max(furthest, t)
            except ValueError:
                pass
        if m in ("ret", "retf", "int3") or m == "jmp":
            if end > furthest:
                return (end - va) if m != "int3" else (ins.address - va)
    return end - va


def parse_va(s):
    s = s.strip()
    return int(s, 16)


def parse_target(t, with_obj=True):
    """'VA:OBJ:LEN' (or 'VA:LEN' when with_obj is False). OBJ may be a Windows path with a
    drive letter, so the VA is taken up to the first ':' and the length after the last."""
    first, last = t.find(":"), t.rfind(":")
    if first < 0:
        raise SystemExit(f"bad target {t!r}: expected VA:{'OBJ:' if with_obj else ''}LEN")
    va = parse_va(t[:first])
    ln = int(t[last + 1:], 0)
    if with_obj:
        if first == last:
            raise SystemExit(f"bad target {t!r}: expected VA:OBJ:LEN")
        return va, t[first + 1:last], ln
    return va, ln


def refuse_overwrite(src, dst):
    """Stop when dst is the input exe (the tools never write an exe in place)."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if src == dst or (dst.exists() and os.path.samefile(src, dst)):
        raise SystemExit(f"refusing to overwrite the input {src}; give a different output path")


def vc71():
    """(cl.exe path, environment) for the Visual C++ Toolkit 2003."""
    b = kit_paths.need(kit_paths.VC71_BIN, "the Visual C++ Toolkit 2003 bin folder (cl 13.10)",
                       "VC71_BIN")
    cl = kit_paths.need(Path(b) / "cl.exe", "cl.exe", "VC71_BIN")
    root = Path(b).parent
    env = dict(os.environ)
    env["PATH"] = str(b) + os.pathsep + env.get("PATH", "")
    env["INCLUDE"] = str(root / "include")
    env["LIB"] = str(root / "lib")
    return cl, env


def work_dir(arg=None):
    d = Path(arg) if arg else kit_paths.BUILD / "rebuild"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_symbols(items):
    """['NAME=VA', ...] -> {NAME: VA}"""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"bad --symbol {it!r}: expected NAME=VA")
        k, v = it.split("=", 1)
        out[k.strip()] = parse_va(v)
    return out
