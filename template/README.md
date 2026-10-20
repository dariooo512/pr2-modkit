# Mod template

Copy this folder to start a code mod for Port Royale 2 (Steam 1.1.2.3). It builds a
small DLL, `HelloMod.dll`, that adds four Lua functions to the game:

| Function | Returns |
|---|---|
| `Hello_Version()` | `"HelloMod 0.1"` |
| `Hello_GetGold()` | the human player's gold |
| `Hello_Gift(amount)` | `1` if `amount` gold was added (negative takes gold), else `0` |
| `Hello_Log(...)` | nothing; writes its arguments to `HelloMod.log` |

The walkthrough is `../docs/make-your-own-mod.md`.

## Layout

| Path | What |
|---|---|
| `dll/mod.h` | **your mod's name** (`MOD_NAME`) and entry points; rename it first |
| `dll/main.c` | `DllMain`, the per-frame hook, Lua (re)registration, the dev console |
| `dll/hello.c` | the example Lua functions; replace with your own |
| `dll/player.c` | reading and changing the player's gold the game's way |
| `dll/hook.c`, `hook.h` | runtime detours with byte verification (from Sovereign Nations) |
| `dll/lua.c`, `lua.h` | the guarded Lua bridge (from Sovereign Nations) |
| `dll/log.c`, `log.h` | `<MOD_NAME>.log` next to the exe |
| `dll/build.py` | builds `_build/<MOD_NAME>.dll` with 32-bit MinGW GCC |
| `patches/load_mod_dll.py` | a `pe_patch.py` module that makes the exe load your DLL (modders' test copy only) |
| `data/` | loose files that override files in the game's archives, at the same relative path |
| `install.json` | what this mod's release needs: `python tools/release/build_release.py template/install.json` builds the release (setup, loader, patch data), `../docs/techniques/09-releasing-a-mod.md` |
| `extras/` | optional DLL parts: `clock.c` (the 24.86-day uptime fix) and `watchdog.c` (hang watchdog), with how to wire them in |

## Build

```
python template/dll/build.py
```

Needs MSYS2's `mingw-w64-i686-gcc` (`C:\msys64\mingw32\bin`, or set `MINGW32_BIN`).
Checked: builds `_build/HelloMod.dll` (about 240 KB, imports only `KERNEL32.dll` and
`msvcrt.dll`) with GCC 16.1, and runs in game: renamed to `MyMod`, loaded by the test exe from
`patches/load_mod_dll.py`, its Lua functions registered and `Hello_Gift(1000)` added 1,000 gold
(`../docs/make-your-own-mod.md` step 6). `extras/` has two optional parts (the uptime fix and a
hang watchdog).

## Rules this template follows

- Touch game objects only on the game's main thread (the frame hook), never in `DllMain`.
- Every hook and patch checks the original bytes first and does nothing if they differ
  (a hook that finds another mod's hook there chains behind it, `dll/hook.c`).
- Every Lua call from C goes through `plua_run` (the engine's Lua error handler throws).
- Re-register Lua functions whenever they are missing (New Game and Load reset globals).
- Dev features only when `<MOD_NAME>.dev` exists next to the exe. Never ship that file.

The vendored files are unchanged copies from Sovereign Nations (`log.c` only takes its
file name from `MOD_NAME`; their sources are in https://github.com/dariooo512/pr2-sovereign-nations). They are under the kit's MIT licence
(`../LICENSE`).
