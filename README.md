# pr2-modkit: a modding kit for Port Royale 2

> **Status: early release.** The docs, the template mod and the general tools are in this
> repository; every command in the docs runs from the kit's folder. Each doc says which
> commands were checked and which were not.
> Players install mods with the mod's setup program; the mod is applied in memory at launch
> (`docs/playing-a-mod.md`, `docs/techniques/03-the-exe.md` section 6).

Port Royale 2 (Ascaron, 2004; Steam app 12470, exe version 1.1.2.3) has no official
modding support beyond its loose data files. This kit is everything that was learned,
built and tested while making **Sovereign Nations**, a mod that lets the player found
their own nation. It is written for a capable programmer who has never looked inside PR2.

## What the kit can do

- **Data mods.** Extract and repack the game's `.cpr` archives (byte-exact), and override
  any archived file with a loose copy in the game folder (no repacking needed). Read, edit
  and write UI layouts (`.screen`), inspect town maps (`.tow`), and write plain-text Lua
  mission scripts (`include.ini`).
- **Code mods.** Load your own C DLL into the game, hook engine functions at runtime with
  byte-verified detours, call engine functions and Lua bindings, add Lua functions,
  add windows, tabs and buttons to the game's own UI, and save your mod's state inside the
  game's save files. `template/` is a working starting point.
- **Find things in the engine.** A map of the engine (nations, towns, war, fleets,
  conquest, economy, UI, saves) with addresses for the 1.1.2.3 build, plus the tools that
  produced it: disassembly and xref scripts, a checker for your own address facts, Ghidra
  and Reko drivers, a source-rebuild pipeline, and record/replay tooling.
- **Test without touching your real install.** A disposable test install, a debugger host
  that classifies crashes and injects input in the background, screenshots, a Lua console,
  hang dumps, and a lock so several test runs can share one install.

### The example: Sovereign Nations

Sovereign Nations (the mod this kit was extracted from) shows how far the techniques go: the
player founds a nation on a town of theirs, annexes towns, sets taxes, declares war on the four
colonial powers, conquers towns in real land battles, runs a national treasury and navy, picks a
government, and all of it is saved in normal save games. It is one C DLL (about 10,500 lines,
plus 1,600 lines of unit tests), seven small exe patches, a mission script and six UI screens.
Its sources, research notes, test suites, loader and setup program are in their own repository,
**https://github.com/dariooo512/pr2-sovereign-nations**; these docs call it *the Sovereign
Nations repository* and give paths in it as, for example, Sovereign Nations' `research/flags.md`.

## What you need

| | Players | Modders |
|---|---|---|
| A legal Steam copy of Port Royale 2 (version 1.1.2.3) | yes | yes |
| Windows 10 or 11 | yes | yes |
| The mod's setup program | yes | to test releases |
| Python 3.12 with `pefile`, `capstone`, `Pillow` | no | yes |
| MSYS2 with the 32-bit MinGW GCC (`mingw-w64-i686-gcc`) | no | for DLLs |
| A pristine copy of your game folder (your own files) | no | yes |
| A DRM-free copy of **your own** exe for static analysis and the test install (see *Getting started*) | **no** | yes |
| Optional: Ghidra, Reko, x64dbg, VC++ Toolkit 2003 | no | for deeper reverse engineering |

The kit contains none of the game's files. The tools read **your** copy: put it in `_game\`
inside the kit (git-ignored) or point the settings at it (`python tools\kit_paths.py` shows
where each tool looks).

## Map of the docs

| Doc | For | What |
|---|---|---|
| [docs/playing-a-mod.md](docs/playing-a-mod.md) | players | Install a mod into your Steam copy, play, uninstall |
| [docs/getting-started.md](docs/getting-started.md) | modders | From a fresh Steam install to a dev setup and a running modded game |
| [docs/make-your-own-mod.md](docs/make-your-own-mod.md) | modders | Walkthrough: build the `template/` mod (a DLL that adds Lua functions) and run it |
| [docs/techniques/01-game-files.md](docs/techniques/01-game-files.md) | modders | `.cpr` archives, loose-file override, `.tow`, `.screen`, `.res`, text markup |
| [docs/techniques/02-lua.md](docs/techniques/02-lua.md) | modders | Lua 4.1 in PR2, mission scripts (`include.ini`), the Lua bridge to C |
| [docs/techniques/03-the-exe.md](docs/techniques/03-the-exe.md) | modders | SteamStub DRM, PE patching, patches P01–P07, loading a DLL, hooks |
| [docs/techniques/04-finding-things.md](docs/techniques/04-finding-things.md) | modders | Disassembly/xref tools, Reko, Ghidra, source reconstruction, mapping structures |
| [docs/techniques/05-game-systems.md](docs/techniques/05-game-systems.md) | modders | The engine map: nations, towns, war, fleets, conquest, officials, economy, flags, sight, UI |
| [docs/techniques/06-saving-mod-state.md](docs/techniques/06-saving-mod-state.md) | modders | Putting mod state in save games and keeping old saves loadable |
| [docs/techniques/07-testing.md](docs/techniques/07-testing.md) | modders | Test install, game host, console, probes, suites, record/replay |
| [docs/techniques/08-debugging.md](docs/techniques/08-debugging.md) | modders | Logs, watchdog, hang dumps, the quit hang, common crashes |
| [docs/techniques/09-releasing-a-mod.md](docs/techniques/09-releasing-a-mod.md) | modders | Building the setup and the release zip |
| [docs/dead-ends.md](docs/dead-ends.md) | everyone | Tried, doesn't work, because … (read before you repeat an experiment) |
| [docs/how-it-was-built.md](docs/how-it-was-built.md) | everyone | The workflow: AI agents on numbered task files, tests as the gate |
| [template/](template/) | modders | The mod folder to copy |
| [THIRD_PARTY.md](THIRD_PARTY.md) | everyone | Third-party code and tools, and their licences |

## The tools

All Python tools take `--help`. Run them from the kit's folder (`python tools\...`).

| Folder | What | Needs |
|---|---|---|
| [tools/kit_paths.py](tools/kit_paths.py) | the settings every tool reads (where your game files, the test install and the build output are); run it to print them | — |
| [tools/files/](tools/files/) | `.cpr` extract and pack, `.screen` read/edit/write, `.tow` analysis and rendering, a coast-to-land `.tow` example, the Lua API name map | your game's archives (extracted) |
| [tools/exe/](tools/exe/) | `pe_patch.py` (reproducible exe patches) and its tests, disassembly, xrefs, operand search, a checker for your own address facts, function byte dumps | the DRM-free exe |
| [tools/test/](tools/test/) | the test install (`env.py`, with the DPI flag it removes again), the lock, the debugger host (`gamehost.py`) with background input (`cmd.py`), scripted sessions with readiness waits, screenshots, the Lua console, hang dumps, keep-awake, mute | a test install |
| [tools/live/](tools/live/) | inspecting a running game: unpacked module dumps, memory snapshot diffs, crash catching | a running test game |
| [tools/replay/](tools/replay/) | record/replay, the differential capture (find the code behind an action), coverage, a differential fuzzer | a running test game; cl 13.10 for the fuzzer |
| [tools/ghidra/](tools/ghidra/) | headless Ghidra scripts and a wrapper (decompile by address, call graph, constant consumers) | Ghidra |
| [tools/reko/](tools/reko/) | Reko drivers (project generation, per-function and whole-program decoding) | a Reko build |
| [tools/rebuild/](tools/rebuild/) | rebuilding a function from source with the game's compiler and carving it into the exe | VC++ Toolkit 2003 (cl 13.10) |
| [reko-patches/](reko-patches/) | our changes to Reko, as patches (GPL, not MIT) | Reko 0.12.2 sources |

### Using the tools from your mod's repository

A mod's own repository should use these tools, not copies of them. Sovereign Nations does it
this way, and the pattern is worth copying: clone the kit next to the mod
(`git clone https://github.com/dariooo512/pr2-modkit`, so the two folders are side by side) or
point the setting `PR2_KIT` at a checkout; the mod's `tools/sn_paths.py` finds it, puts the
kit's `tools\`, `tools\test\`, `tools\exe\` and `tools\files\` on `sys.path`, and writes the
mod's defaults for the kit's settings (above all its own test install, `PR2_TEST_ENV`) into the
environment. Everything the mod adds is a small wrapper that imports the kit's module (its
`sn_env.py`, `sn_session.py`, `sn_patch.py`); no submodule, no fork.
`docs/techniques/07-testing.md` section 7 has the details.

## Conventions in these docs

- **Addresses** are virtual addresses in the 1.1.2.3 image (ImageBase `0x400000`). They
  are the same in the Steam exe after SteamStub has decrypted it in memory and in a
  DRM-free copy. DLL addresses are given as `Module.dll+RVA`.
- **Evidence labels.** Every engine fact is marked **SOLID** (verified against the exe's
  bytes, by the project's fact checker or a tool run, or observed in the running game) or
  **GUESS** (an inference nobody has tested). Treat GUESS as a lead, not as truth.
- **Paths.** Commands run from the kit's folder. `<game>` is a Port Royale 2 install folder;
  `<test install>` is the disposable copy used for tests (`%USERPROFILE%\PR2-test-env` by
  default, setting `PR2_TEST_ENV`). Paths in the Sovereign Nations repository are written
  as "Sovereign Nations' `path`".
- Commands were run on Windows 11 with Python 3.12.10 and MSYS2 GCC 16.1 (i686).

## Licence

Code (tools, `template/`): MIT, see [LICENSE](LICENSE). Docs: CC BY 4.0, see
[docs/LICENSE](docs/LICENSE). `reko-patches/` holds changes to Reko and is GPL-2.0-or-later,
like Reko itself (see its README). The kit contains none of Port Royale 2's files.
