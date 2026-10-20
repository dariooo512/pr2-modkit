# Make your own mod

This walkthrough builds a tiny code mod from the `template/` folder: a DLL that adds Lua
functions to the game, one of which changes a game value (the player's gold) the way the
game itself does. It assumes you finished `getting-started.md` (Python, MSYS2 GCC, a test
install, and for steps 5–6 the DRM-free exe of your own copy). Run the commands from the kit's
folder; the mod goes next to it, in `..\my-mod`.

Status of each step:

| Step | Checked for these docs? |
|---|---|
| 1–4 copy, rename, write a function, build | **yes**: copied, renamed to `MyMod`, built with GCC 16.1 (i686), no warnings |
| 5 make a test exe that loads your DLL | **yes**: built with the kit's `pe_patch.py`; the patched entry point disassembles to the expected stub |
| 6 run it in the test install | **yes**: run in game with the kit's tools on 19 Oct 2026 (below) |
| 7 build an installer | **yes**: the template's release built with the kit's tools, installed by its setup into a copy of the test install, run in game alone and next to Sovereign Nations, uninstalled byte-identical (20 Oct 2026, `techniques/09-releasing-a-mod.md`) |

## 1. Copy the template

```
xcopy /E /I template ..\my-mod
```

## 2. Name your mod

Edit `..\my-mod\dll\mod.h`:

```c
#define MOD_NAME "MyMod"
```

This one name is used for the DLL (`MyMod.dll`), the log (`MyMod.log`), the dev switch
(`MyMod.dev`) and the console file (`MyMod.cmd`), all next to the game exe. `install.json`
has its own copy of the name (`name`, `dll`, `patch_data`, `logs`): change it there too. Pick a prefix
for your Lua functions too (`MyMod_...`): Lua globals are shared with the game and with
other mods.

## 3. Write a Lua function

Open `..\my-mod\dll\hello.c`. A Lua function in C looks like this:

```c
static int __cdecl l_gift(lua_State *L)
{
    int amount = plua_int(L, 1, 0);                 /* argument 1, default 0 */
    int ok = amount != 0 && mod_player_add_gold(amount);
    plua_push_int(L, ok);                           /* one result */
    return 1;
}
```

and is listed in `k_api[]` with its Lua name. Rename the functions to your prefix and
change the sentinel in `hello_lua_registered()` to one of your names; `main.c`
re-registers the whole table whenever that sentinel is nil (after every New Game and
Load, which replace the Lua globals; see `techniques/02-lua.md`).

What `mod_player_add_gold` does (`dll/player.c`) is the pattern for changing any game
value: find the object (the human player is `pool 0x701EC0 + 0x94 * index`, index from
the handle at `0x701ED0`), and change the value **through the game's own function** (here
`0x60E570`, which also books the amount in the balance sheet) rather than by writing
memory. `techniques/05-game-systems.md` lists the functions known for towns, nations,
convoys, permits and the UI.

Rules (all from experience, `techniques/08-debugging.md`): never raise a Lua error or let
a C++ exception out of a Lua function; touch game objects only from the frame hook;
check pointers (`human()` returns NULL in the main menu, and for about 90 game days after
a New Game starts: the game pre-simulates the world before it creates the player, and the
handle at `0x701ED0` is `0x0009FFFF` then, index `0xFFFF` = no player).

## 4. Build

```
python ..\my-mod\dll\build.py
```

Expected: `built ...\my-mod\_build\MyMod.dll (242970 bytes)` (the size varies with the
compiler). Warnings are errors you should
fix; the build uses `-Wall -Wextra`.

## 5. A test exe that loads your DLL (modders only)

Your test copy of the game needs a way to load the DLL. For quick tests the simplest way is the
same entry-point patch Sovereign Nations uses (P06), applied to the DRM-free
exe of your own copy:

```
python tools\exe\pe_patch.py ..\my-mod\_build\PR2_mymod.exe ^
    --patch-module ..\my-mod\patches\load_mod_dll.py
python tools\exe\disasm_func.py 0x64E03F --len 5 --exe ..\my-mod\_build\PR2_mymod.exe
```

Expected: `wrote ...PR2_mymod.exe  sha256 ...`, `section .mod @ 0x9f7000  1 patch(es),
2 label(s)`, and `jmp 0x9f7010` at the entry point. `pe_patch.py` refuses any base exe other than the pinned DRM-free 1.1.2.3
image. This exe is for your own test install only; never distribute it.

## 6. Run it in the test install

All in the background: the game runs in a corner window, gets no focus and makes no sound, and
nothing uses your mouse or keyboard.

1. If other tests share the test install, take its lock first:
   `python tools\test\testlock.py take "my-mod test"`.
2. Back up your saves (the test install shares them with your Steam copy):
   `python tools\test\env.py backup-saves`.
3. Put the exe, the DLL and an empty dev switch `MyMod.dev` (it enables the console) into the
   test install:
   ```
   type nul > ..\my-mod\_build\MyMod.dev
   python tools\test\env.py deploy ..\my-mod\_build\PR2_mymod.exe ..\my-mod\_build\MyMod.dll ..\my-mod\_build\MyMod.dev
   ```
4. Start a free-play game with your exe: `python tools\test\session.py newgame --exe PR2_mymod.exe`.
   It switches the game to windowed mode, mutes it, starts it under `gamehost.py`, clicks
   through the menu by injecting input on the game's UI thread, and returns with the game
   running (about a minute).
5. `MyMod.log` next to the exe in the test install should show:
   ```
   hh:mm:ss MyMod.dll loaded (build ...)
   hh:mm:ss HOOK A2d.InputProcessor @xxxxxxxx ok (trampoline ...)
   hh:mm:ss Hello_* Lua API registered (4 functions)
   hh:mm:ss dev mode ON (MyMod.dev)
   hh:mm:ss Hello_* Lua API registered (4 functions)
   ```
   The first registration happens about 2 s after start, before any game (the Missions Lua
   state already exists then); New Game replaces the Lua globals, and the template registers
   again within half a second.
6. Run Lua in the game:
   ```
   python tools\test\console.py --mod MyMod -e "Hello_Log(Hello_Version(), Hello_GetGold(), Hello_Gift(1000), Hello_GetGold())"
   ```
   It writes `MyMod.cmd` (atomically) next to the exe; within a second the DLL runs and deletes
   it, and `console.py` prints the new log lines: `CONSOLE run`, the `Hello_Gift` line, the
   `LUA ...` line with the gold before and after, and `CONSOLE status 0`. The gold in the
   game's top bar goes up by 1000 (`python tools\test\screen.py shot after` takes a picture
   into `_build\shots\`), and the balance sheet books it under scripts.
7. End the game and clean up:
   ```
   python tools\test\session.py stop
   python tools\test\env.py undeploy
   python tools\test\env.py restore-saves
   python tools\test\testlock.py release "my-mod test"
   ```
   (delete `MyMod.log` from the test install if you no longer need it).

If another mod's DLL hooked the frame first, the hook line ends in `chained behind another
mod's hook` and both run (`dll/hook.c`). If it says `original bytes differ, NOT installed`,
something other than a hook changed those bytes (or the other DLL has an older `hook.c` that
does not chain): test your mod alone. Loose
files another mod left in the test install (`env.py status` lists them) take part in your test
too; move them aside first.

**The run that checked this step** (the commands above, from the kit, in a shared test install
with the other mod's loose files moved aside for the run): `session.py newgame` had the game
running in 52 s (60 towns found); `MyMod.log` showed exactly the lines of item 5; `console.py`
printed

```
CONSOLE run (78 bytes)
Hello_Gift(1000) -> 1, gold now 31000
LUA MyMod 0.1 | 30000 | 1 | 31000
CONSOLE status 0
```

and the top bar in the screenshot read 31.000. `gamehost.py` logged no unhandled exception.
Afterwards `undeploy` and `restore-saves` left the test install and the Save folder exactly as
before (all 752 files of the install compared by hash).

## 7. Make an installer

The kit builds the player's release from `install.json` (`techniques/09-releasing-a-mod.md`):
the setup program, the loader, your DLL, its patch data and your data files, zipped. Edit
`..\my-mod\install.json` first: `name` and `dll` (`_build/MyMod.dll`), `patch_data`
(`_build/loader/MyMod.patches`: the loader reads every `*.patches` next to the exe, so the file
name must be your mod's), `logs` (`MyMod.log`) and the setup's `title`. Keep `"patches": []`
while your mod needs no exe patch: the loader loads your DLL without one. Then:

```
python tools\release\build_release.py ..\my-mod\install.json
python tools\release\test_setup.py --release ..\my-mod\_build\release\MyMod_v0.1
```

Expected: `built ...MyMod.dll`, `built ..._build\loader\version.dll`, `wrote
..._build\loader\MyMod.patches: 0 patches from 0 module(s), no section, relocs 0+0, dll
MyMod.dll`, `check: none of 9 release files is one of the game's ... files`, and
`..._build\release\MyMod_v0.1.zip (... KB), setup exe` (without PyInstaller the setup is a
script and a `.bat`). `test_setup.py` installs, shares, updates and uninstalls it on throw-away
folders: `setup tests: 9 pass, 0 fail`.

The release folder has `MyMod Setup.exe`, `HOW TO INSTALL.txt` / `.html`, `VERSION.txt`, your
`docs`, and `mod\` (`version.dll`, `MyMod.dll`, `MyMod.patches`, `MANIFEST.json`). A player
runs the setup, which finds the Steam install, and starts the game from Steam as always.

Your mod can be installed next to other kit mods, Sovereign Nations included: one loader serves
all of them, the setups share it, and two DLLs may hook the same function (the frame hook
chains). Two mods cannot run together only if their exe patches overlap; the loader then starts
the first one in file-name order and says why in `PR2Loader.log` (the rules:
`techniques/09-releasing-a-mod.md`).

Before a public release, test it in a Steam install you may test on: install, launch from
Steam, check `PR2Loader.log`, uninstall.

**The run that checked this step**: see `techniques/09-releasing-a-mod.md`, "Checked end to end".

## Where to go next

- Add a button or a window: `techniques/05-game-systems.md` §11 and Sovereign Nations'
  `dll/gui.c` and `dll/panel.c`.
- Save your mod's state with the game: `techniques/06-saving-mod-state.md`.
- Add a mission script and popups: `techniques/02-lua.md` §3.
- Hook an engine function: `techniques/03-the-exe.md` §5 and
  `techniques/04-finding-things.md`.
