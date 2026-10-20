"""Load the template mod's DLL at process start (a pe_patch.py patch module).

Same technique as Sovereign Nations' P06 (patches/p06_load_nation_dll.py in
https://github.com/dariooo512/pr2-sovereign-nations):
the exe's entry point 0x64E03F (VC7.1 CRT startup, `push 0x74; push 0x6ae380`) is
detoured to

    push  "<MOD_NAME>.dll"
    call  [LoadLibraryA]        ; IAT slot 0x67F5F4, already imported by PR2.exe
    push  0x74                  ; displaced
    push  0x6ae380              ; displaced
    jmp   0x64E046

LoadLibraryA's result is ignored: without the DLL the game starts unmodded.

Only for a modder's own analysis/test copy of the game. It needs the DRM-free
image (the 1.1.2.3 exe after SteamStub is removed), because pe_patch.py checks
that base's SHA-256. Players never get a patched exe: their Steam copy gets the
kit's loader (loader/README.md), which loads the DLL itself, so this module is
NOT in install.json's "patches" (the loader route needs no exe patch for it).

Conflict: this detours the same 5 bytes as P06, so it cannot be combined with
Sovereign Nations' patch set as is (pe_patch.py refuses overlapping patches,
and the loader refuses a second patch file that changes the same bytes).
To load two DLLs from a test exe, change the prologue to push/call twice.

Use (from the kit's folder):
    python tools/exe/pe_patch.py <out dir>/PR2_hello.exe \
        --patch-module template/patches/load_mod_dll.py
"""
import re
import struct
from pathlib import Path

ENTRY = 0x64E03F
IAT_LOADLIBRARYA = 0x67F5F4
_MOD_H = Path(__file__).resolve().parent.parent / "dll" / "mod.h"
MOD_NAME = re.search(r'#define\s+MOD_NAME\s+"([^"]+)"', _MOD_H.read_text(encoding="utf-8")).group(1)
DLL_NAME = MOD_NAME.encode("ascii") + b".dll\0"


def apply(b):
    b.place("mod_dll_name", DLL_NAME, align=4, desc="LoadLibraryA argument")
    d = b.detour(ENTRY, ["push 0x74", "push 0x6ae380"], target="mod_dll_loader",
                 desc=f"entry point -> load {MOD_NAME}.dll")

    def prologue(va):
        return (b"\x68" + struct.pack("<I", b.va_of("mod_dll_name"))      # push name
                + b"\xFF\x15" + struct.pack("<I", IAT_LOADLIBRARYA))       # call [LoadLibraryA]
    b.place_trampoline("mod_dll_loader", d, prologue=prologue, prologue_size=11,
                       desc=f"LoadLibraryA({MOD_NAME}.dll), then CRT startup")
