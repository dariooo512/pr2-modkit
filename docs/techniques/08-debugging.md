# Debugging

## 1. First, classify the fault

Most hours lost in this project went into "crashes" that were not crashes: the tester
closing the game with Alt+F4, or a C++ exception the game throws and catches itself (an
`Arial.ttf` font error, thrown and handled by the game, was chased as a "crash" for hours). Before chasing anything:

1. **First chance or second chance?** A first-chance exception may be handled by the
   game. Only a second-chance (unhandled) exception is a crash.
2. **Did the game close or crash?** Look at the process exit code, not at the window.
3. **Does it reproduce from a clean save, on a clean install** (no other mods' loose
   files, see `07-testing.md`)?

`tools\test\gamehost.py` answers all three: it runs a test exe under a debugger, tags every
exception FIRST or SECOND, decodes the thrown C++ type name, dumps a call stack on a real
crash, logs who called `ExitProcess`, and logs the exit code
(`_build\runs\<timestamp>\gamehost.log`).

## 2. The mod's log

`log.c` (vendored in the template) writes `<name>.log` **next to the game exe**
(`PR2Nation.log` for Sovereign Nations, `HelloMod.log` for the template). It is
recreated at every start, one flushed line per call, `HH:MM:SS text`. It uses a Win32
handle under its own lock rather than a CRT `FILE*`, because a CRT stream lock held by a
thread that Windows kills at exit would block the CRT's exit-time flush.

Lines worth grepping for:

| Line | Meaning |
|---|---|
| `HOOK <name> @VA ok` / `original bytes differ, NOT installed` | a runtime hook went in, or was refused (wrong exe build, or another mod owns the site) |
| `PATCH <name> @VA ok` / `NOT patched` | same for in-place byte patches |
| `LUA error <st>: <msg> \| in: <code>` | a Lua call from the DLL failed (the quiet handler caught it) |
| `LUA: quiet error handler left installed ... -> restored` | a C++ exception unwound past a Lua call; repaired |
| `SAVE: ...`, `LOAD: ...` | the save blocks (`06-saving-mod-state.md`) |
| `WATCHDOG: no frame for N s ...` | the main thread stopped producing frames (below) |
| `CLOCK: ...` | the uptime fix (below) |

## 3. Hangs

- **Watchdog** (`template/extras/watchdog.c`, from Sovereign Nations): started on the first
  frame. A small
  thread checks the frame counter once a second; after 20 s without a frame (and again at
  60 s) it suspends the main thread, copies its context and up to 16 KB of stack **without
  taking any lock**, resumes it, and logs the main thread's `eip` as `module+offset` plus
  up to 28 candidate return addresses. A long load or save can trigger it once,
  harmlessly.
- **From outside**: `python tools\test\hangdump.py [--pid N] [--exe PR2_mod.exe]` suspends each
  thread of a running game, reads its WOW64 context, scans the stack for return
  addresses into loaded modules, prints them with each thread's CPU time, and resumes.
  All threads waiting with flat CPU = deadlock; one thread spinning = busy loop.
- **The quit hang** (Sovereign Nations' `research/quit_hang.md`): a reported "freeze after autosave" turned
  out to be the Quit sequence (two world resets back to back in the log = Quit → Yes:
  autosave `0x57ECC0`, leave `0x4037A0`, `PostQuitMessage`, shutdown `0x40A9D0`).
  It could not be reproduced (many runs exit in 2 s). Cause: GUESS, a teardown race in
  D3D8/DirectSound/the driver, or a lock held by a thread killed at `ExitProcess`.
  Mitigations: the lock-free log above and the watchdog. Note that the sea-battle popup
  pauses the world on purpose; that is not a hang.
- **Black window on entering the world after ~25 days of Windows uptime.** Once uptime
  passes 2^31 ms (24.86 days), a catch-up loop in `WalExtPR2.dll` (`+0x13F18`) stalls,
  because D3D sets the FPU to 24-bit precision and the float timer stops advancing.
  **Vanilla has the bug too**; a reboot fixes it. `template/extras/clock.c` (from Sovereign
  Nations) fixes it in a mod: in `DllMain` (before the game reads a clock) it overwrites the entries of
  `kernelbase!GetTickCount`, `kernel32!GetTickCount` and `winmm!timeGetTime` with a jump
  to a function that returns milliseconds since the DLL loaded (QueryPerformanceCounter)
  plus 100000. It is process-wide.
- **The game will not start at all while a screen saver or the lock screen is up**
  (Direct3D 8 cannot create its device). For long unattended test runs see
  `tools\test\keepawake.py` (`07-testing.md`).

## 4. Common crashes and their causes

| Symptom | Cause | Status |
|---|---|---|
| Access violation at `0x4C1D9F` | a nation-5 town without the player-town bit sends `-userflag-` to an image loader that reports success with a NULL handle | fixed by P02 (SOLID) |
| Access violation at `0x5C86BD` (governor town) and 11 similar sites | `g_nations[n]` is NULL for n ≥ 4 | fixed by P01/P05 (SOLID) |
| Out-of-bounds icon read at `0x4F6484` | a nation byte ≥ 6 | never write such a byte (SOLID) |
| C++ exception from Lua, or the game exits with code 1 | any Lua error in the Missions state: the engine's error handler throws; outside a protected frame it calls `exit(1)` | always use `plua_run` (SOLID) |
| Null dereference at `0x42DAEE` | `Global_CreatePopup` and friends called from a bare `lua_dostring` (no current mission) | call them from a mission callback (SOLID) |
| `CStadtHandle::operator->() - object pointer is 0` | a bad town handle passed to a `Town_*` binding | validate handles first (SOLID) |
| C++ exception while opening a window | a missing `.screen`, an unregistered class name in a screen, or an unknown atlas part | ship the file, reuse registered class names (SOLID) |
| Heap corruption after saving | the fill pass wrote more than the measuring pass | identical byte counts (SOLID) |
| World wiped after "loading" | the loader read past the end of the stream (`CLoadExeption`) | check your block lengths (SOLID) |
| Heap corruption after calling an engine function that takes a string | STLport string not built with the game's allocator (IAT `0x67F9B8`) | build strings the game's way (SOLID) |
| Strange behaviour after two windows opened | control names are global; the first match wins | prefix names, one window (SOLID mechanism) |
| Crash after closing a window from its button | destroyed inside the click handler | defer to the next frame (GUESS) |
| An exception thrown by the game through your GCC-compiled frames | MSVC C++ exceptions unwinding through MinGW frames | GUESS-unsafe: catch nothing, keep engine calls that may throw out of deep C call chains |

## 5. Tools for live inspection

- `tools\test\cmd.py` (talks to a running `gamehost.py`): `read 0xADDR n`, `write 0xADDR hex`,
  `bp`, `call 0xVA THIS [args]`, `towns`, `click`, `key`.
- `gamehost.py --bp VA=reg,reg+off:len,...` logs registers and memory at a breakpoint
  without stopping the game, for example
  `--bp 0x5C9DD7=esi,esi+0x358:2,esp+0x1c:4` (AddToNation stamping nation 5).
- The dev console (`02-lua.md`) for anything reachable from Lua.
- `tools\live\memscan.py` (snapshot-diff memory search, globals included), `tools\live\dump_module.py`
  (an unpacked engine DLL from the running game) and `tools\live\crashcatch.py` (a crash catcher
  that also finds the throw site of a C++ exception and FILE:LINE candidates in the exception
  object); details in `tools/live/README.md`. For breakpoint probes use `gamehost.py --bp`.

## What was checked for this page

- `gamehost.py`, `cmd.py` and the template's log: run in game for `../make-your-own-mod.md`
  step 6 (see there).
- `hangdump.py`, `memscan.py`, `dump_module.py`: compile and answer `--help`; not run against
  the game for these docs.
- `crashcatch.py`: run against a small throwing test program built for the purpose (types,
  throw site, FILE:LINE, first/second chance and exit code decoded); not run against the game.
- `template/extras/clock.c` and `watchdog.c`: built into the template; they run in Sovereign
  Nations' builds, not in the template's test run.
- The crash table and the hang analyses are from the project's research notes and run logs.
