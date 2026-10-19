# The exe: DRM, PE patching, loading a DLL, hooks

Code mods for PR2 have two layers:

1. **A few exe patches** that must exist before the game's code runs: crash fixes for
   states vanilla never produces, new data the patched code reads, and a loader stub that
   loads your DLL. Sovereign Nations has seven (P01–P07).
2. **A DLL** that installs everything else at runtime: detours, byte patches, Lua
   functions, UI. Most of a mod lives here, because runtime hooks verify the bytes they
   replace and can be fixed without rebuilding an exe.

## 1. The build and the SteamStub DRM

| Build | SHA-256 | Notes |
|---|---|---|
| Steam `PR2.exe`, file version 1.1.2.3 | `5295c06689147f7565f72510aeb686719cf0ae38aa581a6df3e4d61d0cf76264` | SteamStub v2.1 (x86) wrapped. Started directly, it fails with `Application load error 5:0000065434`; started through Steam (`steam://run/12470`) the stub decrypts the code in memory and jumps to the original entry point. |
| The same exe with SteamStub removed | `9c9250bc2b7a8068f658c0d9935bf2e7d8f9865bc31b875d9fb3b220d01cf448` | The target of every address in these docs, and the base that `pe_patch.py` pins. It runs standalone next to the install's DLLs (reaches the main menu without Steam). |

Image layout of the DRM-free exe (SOLID): ImageBase `0x400000`, no relocations, no NX
flag (`DllCharacteristics = 0`); `.text` at `0x401000`, `.rdata` `0x67F000`, `.data`
`0x6E0000`, `.rsrc` `0x704000`. The original entry point (OEP) is `0x64E03F` (VC7.1 CRT
startup). Only the exe is protected: `.cpr` archives and loose data files are read
normally, so **data mods need no DRM work at all**.

**For modders only.** Static analysis (the `tools\exe\` scripts, Ghidra, Reko) and the
file-patch build for a test install need the DRM-free image. A modder makes it from **their
own** copy, for their own analysis: this project did this with Steamless 3.1, which wrote
`PR2.exe.unpacked.exe` next to the input (the exact invocation was not recorded; not
re-checked for these docs). Verify the result against the SHA-256 above. The kit does not ship Steamless or any unpacked exe.
Players never do this: the mod is applied in memory at launch (section 6).

## 2. `pe_patch.py`: reproducible exe patches

`tools\exe\pe_patch.py` turns the pinned base exe (setting `PR2_BASE_EXE`) and an ordered
list of patch modules into an output exe plus a manifest:

1. It reads the base exe and refuses to continue unless its SHA-256 matches the pinned
   DRM-free hash.
2. It appends **one** new section (default `.mod`, `--section-name` to change it; RWX, at
   `0x9F7000`) after `.rsrc`.
3. Patch modules place code and data at symbolic labels in that section, overwrite bytes
   in the original image (old bytes checked), and write 5-byte `E9` detours whose
   displaced instructions are verified with capstone against the instruction text the
   module expects.
4. It writes the exe and `<out>.manifest.json`: base hash, every patch with old and new
   bytes, the section layout, the output hash. Two builds give the same hash.

Header changes are only `NumberOfSections`, `SizeOfImage`, the checksum if non-zero (PR2
ships 0) and one section header.

A patch module is a Python file with `apply(b)`, where `b` is a `PatchBuilder`:

| Call | Does |
|---|---|
| `b.place(label, bytes_or_fn, size=, align=16, desc=)` | put data or code in the new section, returns its VA (a callable gets its own VA and needs `size`) |
| `b.reserve(label, size)` / `b.va_of(label)` | reserve space / look up a label |
| `b.detour(va, ["exact capstone text", ...], target=label_or_va, desc=)` | `E9` jump + NOP padding; refuses rel8 branches, `ret`, `int3`, and branches into the overwritten range |
| `b.place_trampoline(label, detour, prologue=bytes_or_fn, prologue_size=)` | your prologue + the relocated displaced instructions + jump back |
| `b.patch_bytes(va, new, expected_old=, desc=)` | in-place edit, old bytes checked |
| `b.decode(va, n)` | capstone instructions, for your own checks |

```
python tools\exe\pe_patch.py <out dir>\PR2_mod.exe --patch-module <your patch>.py [--patch-module ...]
python tools\exe\test_pe_patch.py
```

The output is for your own test install. `pe_patch.py` refuses to overwrite the base exe and to
write into a Steam library folder; any other path works. Checked: `test_pe_patch.py` passes
(17 tests); the template's `load_mod_dll.py` builds and its entry point disassembles as in §4;
and Sovereign Nations' seven patch modules (from its repository, in order, with
`--section-name .nation`) build to the same exe, byte for byte, as Sovereign Nations' own copy
of the builder (`section .nation @ 0x9f7000  22 patch(es), 11 label(s)`). Order matters: P04
uses P01's label, and the DLL hard-codes `player_rel` at `0x9F7000`, so **P01 must stay
first**. Overlapping patches are refused.

Traps:

- A detour needs 5 bytes of **whole, position-independent** instructions at the site and no
  branch from elsewhere into bytes 1..4. Many good sites fail because a `jcc rel8` jumps
  into them; move the detour a few instructions (P04 hooks `0x5D2D96` instead of the entry
  `0x5D2D90` for this reason).
- Put every site you patch into a facts file for `tools\exe\check_sites.py` (address +
  expected instructions or bytes; `tools\exe\sites.example.txt`) and run it on every change,
  so a doc and the bytes cannot drift apart. Sovereign Nations kept 197 such facts; that file is
  not published (it holds the original bytes of every site).

## 3. The Sovereign Nations patches (P01–P07)

| Patch | Site | Why | Adds code? |
|---|---|---|---|
| P01 `p01_isatwar_player` | detour `0x5BA0A0` (`CNation::IsAtWar`) | IsAtWar with a NULL `this` (the player nation as source) crashed; and a power looking at nation 5 now reads a 4-byte table `player_rel` at `0x9F7000`, which makes war and peace with the player nation possible | yes: `player_rel`, stub |
| P02 `p02_userflag_null` | `0x4C1411` `je` target (2 bytes) | the image loader reported success for a failed `-userflag-` lookup, then dereferenced NULL at `0x4C1D9F` | no |
| P03 `p03_display_byte_reads` | six disp32 edits (`0x5C6A86`, `0x5A80B0`, `0x5A822C`, `0x6352B7`, `0x634902`, `0x57D0B6`) | per-nation tables indexed with the raw nation byte 5 read out of bounds; the display byte (host power) gives a valid row | no |
| P04 `p04_reputation_war` | detours `0x5D2AC0` (`GetReputation`), `0x5D2D96` (inside `IsRepAtLeast25`) | hostility toward the player is reputation-based; at war (per `player_rel`) reputation reads 0 / not-at-least-25 | yes |
| P05 `p05_nation5_town_safety` | detours and disp32 edits in `0x5C8650`, `0x5B40D0`, `0x5BC310`, `0x5E74B0`, `0x5BC100` | the 12 NULL-nation dereferences reachable from a nation-5 town (one was an observed crash at `0x5C86BD`) | yes |
| P06 `p06_load_nation_dll` | detour at the entry point `0x64E03F` | loads `PR2Nation.dll` with `LoadLibraryA` (IAT `0x67F5F4`) before the CRT starts | yes: name + stub |
| P07 `p07_town_info_flags` | disp32 at `0x4A3DDB` | the town-info dialog indexed a 4-entry flag table with the raw nation byte | no |

The patch modules are in the Sovereign Nations repository (`patches/`). The research behind
each is summarised in `05-game-systems.md`; their static tests are Sovereign Nations'
`tools/test_patches.py` (P01–P03), `test_p04.py`, `test_p05.py`.

## 4. Loading your DLL (P06)

The OEP `0x64E03F` starts with `push 0x74; push 0x6ae380` (7 bytes). P06 replaces them
with a jump to a stub in the new section:

```
push  offset "PR2Nation.dll"
call  dword ptr [0x67F5F4]      ; LoadLibraryA, already in PR2.exe's import table
push  0x74                      ; displaced
push  0x6ae380                  ; displaced
jmp   0x64E046
```

The result is ignored, so the game runs unmodded if the DLL is missing. The DLL is loaded
before the CRT and before any game object exists; `A2dLib-2.20A.dll`, `lua.dll` and the
other static imports are already mapped. `template/patches/load_mod_dll.py` is the same
patch for your own DLL name (checked: built against the DRM-free exe, the entry
disassembles to `jmp 0x9f7010` and the stub to the sequence above). Two DLLs cannot both
own these 7 bytes; to load two, push/call twice in one stub.

## 5. Runtime hooks in the DLL (`hook.c`)

`hook.c` (vendored in `template/dll/`) gives four primitives. Each one first compares the
bytes at the site with the bytes you expect; on a mismatch it logs
`HOOK <name> @VA: original bytes differ, NOT installed` and does nothing. That check is
the safety net against a different exe build and against two mods hooking one site.

| Function | Trampoline behaviour |
|---|---|
| `hook_install(name, at, expect, len, fn)` | `pushad; pushfd; push esp; call fn; add esp,4; popfd; popad; <displaced bytes>; jmp at+len`. `fn(Regs *r)` is cdecl; it may change any register in `r` except `esp`, and cannot change flags. |
| `hook_install_ret(..., fn, ret_pop)` | for a function entry: if `fn` returns non-zero, return to the caller with `ret ret_pop` and `r->eax` as the result, skipping the function |
| `hook_install_jmp(..., fn)` | if `fn` returns an address, continue there instead of at the displaced code |
| `patch_bytes(name, at, expect, repl, len)` | verified in-place edit |

`Regs` is `{eflags, edi, esi, ebp, esp, ebx, edx, ecx, eax}` (PUSHFD over PUSHAD);
`stack_arg(r, i)` reads `[esp + 4*i]` at the hook point (at a function entry `i = 0` is
the return address, `i = 1` the first stack argument; `this` is in `r->ecx` for
thiscall).

Traps (SOLID unless marked):

- `len` must be 5..16 for `hook_install` (the jump buffer is 16 bytes; not checked) and
  5..32 for the other two.
- The displaced bytes are copied from `expect` and re-executed in the trampoline, so they
  must be position-independent: no `call`/`jmp`/`jcc` rel. `esp`-relative operands are
  fine (the detour is a `jmp`, `esp` is unchanged).
- No other code may branch into the middle of the displaced range (a branch to its first
  byte is fine). Check with `pe_xref.py` before you pick a site.
- Install hooks from `DllMain` (before the game runs) or on the main thread; threads are
  not suspended while the bytes change.
- Engine functions are mostly `__thiscall` (`this` in `ecx`, callee pops). Declare them
  with `__attribute__((thiscall))` in GCC; get the `ret N` from the disassembly.
- A module that loads after your DLL (for example `WalExtPR2.dll`) cannot be hooked from
  `DllMain`; Sovereign Nations installs such hooks lazily from the frame hook.

**The frame hook.** Game objects must only be touched on the game's main thread.
Sovereign Nations and the template hook `A2dLib-2.20A.dll+0x2F2C0`
(`CWalhallaClient::InputProcessor`, bytes `53 55 56 57 8B E9`), which runs once per frame
on the main thread, also in the main menu. Everything else (Lua registration, UI, game
logic) runs from there.

## 6. How patches reach players: in memory, at launch

Players never get a patched exe. The game folder gets a **loader** and **patch data**, and
`PR2.exe` stays byte-identical; the game starts from Steam as always and the Steam DRM runs
untouched. Source: Sovereign Nations' `loader/`, design and test results in its
`research/user_install.md`. SOLID: verified in game on the
DRM-free image (3/3); the start through Steam itself is not yet tested.

- **The loader is `version.dll`.** `PR2.exe` imports `VERSION.dll` (3 functions) and
  `WINMM.dll`; neither is a KnownDLL, so Windows loads them from the game folder first.
  `version.dll` has 17 exports, all forwarded to the system copy (`GetSystemDirectory`,
  SysWOW64 for this 32-bit process), and it does not collide with dgVoodoo's files.
- **When it patches.** `DllMain` only swaps the import slot `0x67F6F0`
  (`MSVCR71!__set_app_type`). The CRT calls it right after the entry point (return address
  `0x64E0B6`): SteamStub has decrypted `.text` by then, and no game code has run. At that
  call the loader checks the original bytes of every patch site and patches nothing on any
  mismatch (the game then starts unmodded and `PR2Loader.log` says why).
- **The new section.** There is no PE section in memory. The loader allocates the section
  at its file address `0x9F7000` if free, else anywhere, applies the patch data's
  relocations and passes the address to the mod DLL in `PR2NATION_SECTION`. In the tests
  `0x9F7000` was taken and the relocated section worked.
- **Patch data** (`PR2Nation.patches`) is exported from the same patch modules by
  Sovereign Nations' `tools/export_patches.py` (it uses `pe_patch.py`'s `materialize()`), so
  the file build and the memory route run the same bytes (`--check` proves it). Format:
  Sovereign Nations' `loader/README.md`.
- **An already patched exe** (the file route on test machines) is detected and the loader
  stays idle. `PR2NATION_OFF=1` starts the game without the mod.
- **GOG**: not checked. A GOG `PR2.exe` byte-identical to the DRM-free image would work with
  the same data.
