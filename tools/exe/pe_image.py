"""Shared helpers for the read-only exe tools: load an image, map VAs, decode.

The default image is the DRM-free PR2.exe (setting PR2_BASE_EXE, tools/kit_paths.py). The
Steam PR2.exe cannot be used: SteamStub keeps its code encrypted on disk.
"""
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402


def hexint(s: str) -> int:
    return int(s, 16)


class Image:
    def __init__(self, path=None):
        self.path = Path(path) if path else kit_paths.BASE_EXE
        kit_paths.need(self.path, "exe", "PR2_BASE_EXE")
        self.pe = pefile.PE(str(self.path), fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = []           # (va, bytes, name, executable)
        for s in self.pe.sections:
            name = s.Name.rstrip(b"\0").decode("latin-1", "replace")
            self.sections.append((self.base + s.VirtualAddress, bytes(s.get_data()), name,
                                  bool(s.Characteristics & 0x20000000)))
        text = next((s for s in self.sections if s[2] == ".text"), None) \
            or next(s for s in self.sections if s[3])
        self.text_va, self.text = text[0], text[1]
        self.md = Cs(CS_ARCH_X86, CS_MODE_32)
        self.md.detail = True

    def code_sections(self):
        return [(va, data, name) for va, data, name, x in self.sections if x]

    def text_off(self, va: int) -> int:
        off = va - self.text_va
        if not 0 <= off < len(self.text):
            raise SystemExit(f"{va:#x} is not in .text ({self.text_va:#x}..{self.text_va + len(self.text):#x})")
        return off

    def fn_end(self, va: int, limit: int = 0x4000) -> int:
        """Offset in .text of the first CC CC padding after va (a function's likely end)."""
        off = self.text_off(va)
        end = off
        while end < len(self.text) - 1 and end < off + limit:
            if self.text[end] == 0xCC and self.text[end + 1] == 0xCC and end > off + 4:
                break
            end += 1
        return end

    def disasm(self, va: int, n: int):
        off = self.text_off(va)
        return self.md.disasm(self.text[off:off + n], va)


def add_exe_arg(ap):
    ap.add_argument("--exe", type=Path, default=None,
                    help="the image to read (default: setting PR2_BASE_EXE, the DRM-free PR2.exe)")
