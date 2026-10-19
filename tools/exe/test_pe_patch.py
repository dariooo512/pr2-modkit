#!/usr/bin/env python3
"""Tests for pe_patch.py. Run: python tools/exe/test_pe_patch.py

Needs the DRM-free exe (setting PR2_BASE_EXE). Builds into PR2_BUILD/pe_patch_test/
(git-ignored). Never runs the game.
"""
import json
import struct
import sys
import unittest
from pathlib import Path

import capstone
import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_patch  # noqa: E402
from pe_patch import PatchBuilder, PatchError, build, manifest_path  # noqa: E402

OUT_DIR = pe_patch.BUILD_DIR / "pe_patch_test"
IS_AT_WAR = 0x5BA0A0          # CNation::IsAtWar
IS_AT_WAR_INSNS = ["mov al, byte ptr [esp + 4]", "cmp al, 4"]
CALL_SITE = 0x409B80          # function starting with `call 0x405140` (rel32 relocation)
MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def is_at_war_patch(b):
    d = b.detour(IS_AT_WAR, IS_AT_WAR_INSNS, target="tramp", desc="test detour CNation::IsAtWar")
    b.place_trampoline("tramp", d)
    b.reserve("counter", 4, desc="test data")


def text(i):
    return f"{i.mnemonic} {i.op_str}"


class PePatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = OUT_DIR / "PR2_test.exe"
        cls.manifest = build(cls.out, [is_at_war_patch], section_size=0x2000)
        cls.base = pe_patch.BASE_EXE.read_bytes()
        cls.data = cls.out.read_bytes()
        cls.pe = pefile.PE(data=cls.data)
        cls.bpe = pefile.PE(data=cls.base, fast_load=True)
        cls.sec = cls.pe.sections[-1]
        cls.sec_va = cls.pe.OPTIONAL_HEADER.ImageBase + cls.sec.VirtualAddress

    def at(self, va, n):
        return self.pe.get_data(va - self.pe.OPTIONAL_HEADER.ImageBase, n)

    # -- section table ----------------------------------------------------
    def test_section_table(self):
        pe, sec = self.pe, self.sec
        oh = pe.OPTIONAL_HEADER
        self.assertEqual(pe.FILE_HEADER.NumberOfSections, 5)
        self.assertEqual([s.Name.rstrip(b"\0") for s in pe.sections],
                         [b".text", b".rdata", b".data", b".rsrc", b".mod"])
        self.assertEqual(sec.VirtualAddress, 0x5F7000)
        self.assertEqual(sec.PointerToRawData, len(self.base))
        self.assertEqual(sec.Misc_VirtualSize, 0x2000)
        self.assertEqual(sec.SizeOfRawData % oh.FileAlignment, 0)
        self.assertEqual(sec.VirtualAddress % oh.SectionAlignment, 0)
        self.assertEqual(sec.PointerToRawData % oh.FileAlignment, 0)
        self.assertEqual(sec.Characteristics, pe_patch.SECTION_CHARS)
        self.assertEqual(oh.SizeOfImage, sec.VirtualAddress + 0x2000)
        self.assertEqual(len(self.data), sec.PointerToRawData + sec.SizeOfRawData)
        self.assertLessEqual(pe.sections[-1].get_file_offset() + 40, oh.SizeOfHeaders)
        # sections contiguous, ordered, non-overlapping in memory and file
        for a, b in zip(pe.sections, pe.sections[1:]):
            va_end = a.VirtualAddress + pe_patch.align_up(max(a.Misc_VirtualSize, a.SizeOfRawData),
                                                         oh.SectionAlignment)
            self.assertEqual(b.VirtualAddress, va_end)
            self.assertLessEqual(a.PointerToRawData + a.SizeOfRawData, b.PointerToRawData)
        self.assertEqual(oh.CheckSum, self.bpe.OPTIONAL_HEADER.CheckSum)  # 0 stays 0
        self.assertIsNone(pe.get_overlay_data_start_offset())
        # directories untouched
        for d0, d1 in zip(self.bpe.OPTIONAL_HEADER.DATA_DIRECTORY, oh.DATA_DIRECTORY):
            self.assertEqual((d0.VirtualAddress, d0.Size), (d1.VirtualAddress, d1.Size))

    # -- detour + trampoline ---------------------------------------------
    def test_detour_decodes_as_jmp(self):
        tramp = self.sec_va  # first label sits at section start
        insns = list(MD.disasm(self.at(IS_AT_WAR, 6), IS_AT_WAR))
        self.assertEqual([text(i) for i in insns], [f"jmp {tramp:#x}", "nop "])
        self.assertEqual(insns[0].size, 5)
        # the instruction after the patched range is untouched
        nxt = next(MD.disasm(self.at(IS_AT_WAR + 6, 2), IS_AT_WAR + 6))
        self.assertEqual(text(nxt), "jae 0x5ba0ba")

    def test_trampoline(self):
        tramp = self.sec_va
        insns = list(MD.disasm(self.at(tramp, 11), tramp))
        self.assertEqual([text(i) for i in insns],
                         ["mov al, byte ptr [esp + 4]", "cmp al, 4", "jmp 0x5ba0a6"])
        self.assertEqual(self.at(tramp, 6), bytes.fromhex("8a4424043c04"))

    def test_untouched_bytes_identical(self):
        base, out = self.base, self.data
        allowed = set()
        fh = self.bpe.FILE_HEADER.get_file_offset()
        oh = self.bpe.OPTIONAL_HEADER.get_file_offset()
        allowed |= set(range(fh + 2, fh + 4))                 # NumberOfSections
        allowed |= set(range(oh + 56, oh + 60))               # SizeOfImage
        allowed |= set(range(oh + 64, oh + 68))               # CheckSum
        new_hdr = self.sec.get_file_offset()
        allowed |= set(range(new_hdr, new_hdr + 40))          # new section header
        for p in self.manifest["patches"]:
            off = int(p["file_offset"], 16)
            allowed |= set(range(off, off + len(p["old"]) // 2))
        # fast compare: chunk by chunk, only inspect differing chunks
        bad = []
        step = 1 << 16
        for c in range(0, len(base), step):
            if base[c:c + step] != out[c:c + step]:
                bad += [i for i in range(c, min(c + step, len(base)))
                        if base[i] != out[i] and i not in allowed]
        self.assertEqual(bad, [], f"unexpected diffs at {[hex(x) for x in bad[:10]]}")
        self.assertEqual(len(self.manifest["patches"]), 1)
        p = self.manifest["patches"][0]
        self.assertEqual((p["va"], p["old"]), (hex(IS_AT_WAR), "8a4424043c04"))

    def test_section_contents(self):
        raw = self.data[self.sec.PointerToRawData:]
        labels = {l["name"]: l for l in self.manifest["section"]["labels"]}
        self.assertEqual(labels["tramp"]["va"], hex(self.sec_va))
        self.assertEqual(labels["tramp"]["size"], 11)
        cnt = int(labels["counter"]["offset"], 16)
        self.assertEqual(raw[cnt:cnt + 4], b"\0\0\0\0")
        self.assertFalse(any(raw[11:]))  # nothing else written

    def test_manifest(self):
        m = json.loads(manifest_path(self.out).read_text())
        self.assertEqual(m["base"]["sha256"], pe_patch.BASE_SHA256)
        self.assertEqual(m["output"]["sha256"], pe_patch.sha256(self.data))
        self.assertEqual(m["section"]["va"], hex(self.sec_va))
        self.assertEqual(m["patches"][0]["new"], (b"\xE9" + struct.pack(
            "<i", self.sec_va - (IS_AT_WAR + 5)) + b"\x90").hex())

    def test_reproducible(self):
        m2 = build(OUT_DIR / "PR2_test_again.exe", [is_at_war_patch], section_size=0x2000)
        self.assertEqual(m2["output"]["sha256"], self.manifest["output"]["sha256"])

    # -- rel32 relocation -------------------------------------------------
    def test_call_relocation(self):
        def patch(b):
            d = b.detour(CALL_SITE, ["call 0x405140"], target="t", desc="call relocation test")
            b.reserve("pad", 0x123, align=1)   # move the trampoline off the section start
            b.place_trampoline("t", d, prologue=b"\x90\x90")
        out = OUT_DIR / "PR2_reloc.exe"
        m = build(out, [patch])
        pe = pefile.PE(data=out.read_bytes())
        ib = pe.OPTIONAL_HEADER.ImageBase
        t = int(next(l for l in m["section"]["labels"] if l["name"] == "t")["va"], 16)
        self.assertNotEqual(t, ib + pe.sections[-1].VirtualAddress)
        code = pe.get_data(t - ib, 12)
        self.assertEqual([text(i) for i in MD.disasm(code, t)],
                         ["nop ", "nop ", "call 0x405140", f"jmp {CALL_SITE + 5:#x}"])
        site = pe.get_data(CALL_SITE - ib, 5)
        self.assertEqual(text(next(MD.disasm(site, CALL_SITE))), f"jmp {t:#x}")

    def test_jcc32_relocation(self):
        ins = pe_patch.DisplacedInsn(0x401000, bytes.fromhex("0f8400010000"), "je 0x401106",
                                     "jcc32", 0x401106)
        enc = ins.relocated(0x9F7000)
        self.assertEqual(text(next(MD.disasm(enc, 0x9F7000))), "je 0x401106")

    # -- rejections -------------------------------------------------------
    def test_rejects_wrong_expected(self):
        b = PatchBuilder()
        with self.assertRaisesRegex(PatchError, "expected"):
            b.detour(IS_AT_WAR, ["mov al, byte ptr [esp + 8]", "cmp al, 4"], target=0x401000)

    def test_rejects_short_branch(self):
        b = PatchBuilder()   # cmp al,4 ; jae short -> rel8 branch must be refused
        with self.assertRaisesRegex(PatchError, "cannot relocate 'jae 0x5ba0ba'"):
            b.detour(IS_AT_WAR + 4, ["cmp al, 4", "jae 0x5ba0ba", "movzx eax, al"],
                     target=0x401000)

    def test_rejects_overlap(self):
        b = PatchBuilder()
        b.detour(IS_AT_WAR, IS_AT_WAR_INSNS, target=0x401000)
        with self.assertRaises(PatchError):
            b.patch_bytes(IS_AT_WAR + 5, b"\x90")

    def test_rejects_bad_hash(self):
        with self.assertRaises(PatchError):
            PatchBuilder(expected_sha256="0" * 64)

    def test_rejects_section_overflow(self):
        b = PatchBuilder(section_size=0x10)
        with self.assertRaises(PatchError):
            b.reserve("big", 0x11)

    def test_rejects_overwriting_base(self):
        b = PatchBuilder()
        with self.assertRaisesRegex(PatchError, "overwrite the base"):
            b.build(pe_patch.BASE_EXE)

    def test_rejects_steam_library(self):
        b = PatchBuilder()
        target = OUT_DIR / "steamapps" / "common" / "Port Royale 2" / "PR2.exe"
        with self.assertRaisesRegex(PatchError, "Steam library"):
            b.build(target)
        self.assertFalse(target.exists())

    def test_section_name_option(self):
        m = build(OUT_DIR / "PR2_named.exe", [is_at_war_patch], section_name=".hello")
        self.assertEqual(m["section"]["name"], ".hello")


if __name__ == "__main__":
    unittest.main(verbosity=2)
