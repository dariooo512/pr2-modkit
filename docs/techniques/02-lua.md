# Lua 4.1: what the engine exposes, mission scripts, the bridge to C

PR2's gameplay scripting is Lua, in an Ascaron fork: the engine string is
`Lua 4.1.0 (ASCARON_LUA, modified 4.0.1 source)`. The game's own missions are compiled
bytecode (`lua\data\missions.bin`, magic `\x1BLuaA`), but several plain-text hooks are run
directly and are the practical way in: `lua\config\default\include.ini`, `mstart.ini`,
`scenario.ini`, `scripts\Credits.lua`. All of them can be overridden by a loose file
(`01-game-files.md`).

Source notes: Sovereign Nations' `research/lua_bridge.md` and `research/ui_entry.md`. Every
engine binding name can be listed from your own game files with
`python tools\files\lua_api_map.py` (from the bytecode's string table and the text scripts;
it writes `_build\API_MAP.md`; checked: 1,719 names, identical to the map these docs were
written from).

## 1. The dialect (SOLID)

- `lua.dll` has 69 exports, **all cdecl**, its own static CRT and heap. Never `free`
  anything it returns.
- **`lua_Number` is a 32-bit int.** `lua_tonumber` returns `int`, `lua_pushnumber(L, int)`.
  Floats go through `lua_tofloat`/`lua_pushfloat`. Type ids: 0 userdata, 1 nil,
  2 number (int), 3 float, 4 string, 5 table, 6 function.
- Lua 4 syntax: `for k, v in t do ... end`, `getn(t)`, upvalues as `%name`. No `pairs`,
  no `#`, no `local function`, no `...` varargs, no `table.*`, no `goto`.
  Sovereign Nations' `tools/check_ui_static.py` rejects these Lua-5 constructs in a script
  and runs it in the game's real `lua.dll` against stubs (it passed on Sovereign Nations'
  `include.ini`; it needs the pristine game copy and the extracted archives).
- No standard library exports; the base library comes from `Ascaron.Scripting-2.6.dll`.

## 2. The game's Lua states

- **`"Missions"`** is the game state: `mstart.ini`, `missions.bin`, about 559 engine
  bindings, saved with the game. Getter `0x004337B0` (cdecl, no arguments) returns a
  `struct { lua_State *L; bool borrowed; }*`, or NULL while the state does not exist yet.
  SOLID (observed in the template's test run): it already exists about 2 s after start, in
  the main menu, before any game; New Game then replaces its globals. GUESS: the
  underlying `GetState` creates the state if it is missing, so do not call it from
  `DllMain`.
- `"default"` is used only by the Credits screen.
- **Globals are replaced on New Game and on Load.** `0x438D90` makes a new globals table,
  registers the bindings, sets `GLOBALS = {}` and `_ERRORMESSAGE` (callers `0x43E203`,
  `0x43E8CE`). Anything you registered is gone afterwards: **re-register after every
  reset**. The robust way (used by Sovereign Nations and the template) is to poll: every
  30 frames, if a sentinel global of yours is nil, register everything again.
- Run order on **New Game**: reset, `CMission::LoadScriptPath` `0x435DF0` (runs
  `missions.bin`, then `<script path>\include.ini` in the same state), then `mstart.ini`
  (`0x437510`, New Game only). On **Load**: reset, `0x435DF0`, then the saved `GLOBALS`
  are restored with `lua_dostring`. `mstart.ini` does **not** run on Load.
- `GLOBALS` is saved with the game. Its serializer `0x42CC60` writes strings unescaped,
  so store only numbers, simple strings (no `"`, `\`, newline) and tables of those.

## 3. Mission scripts via `include.ini`

The engine looks up every mission callback in `SCRIPT_TABLE[name]`. A mod adds its own
mission like this (Sovereign Nations' `data/lua/config/default/include.ini`):

1. Line 1 stays the unchanged vanilla `USE_GROUPS = {...}`; the mod appends its own group
   (`"fnn"`) to `USE_GROUPS`.
2. `fnn_nation = Script_Register("fnn_nation", "fnn", MTYPE_GLOBAL)` (`0x41D0F0`) returns
   the table; define the callbacks on it.
3. `OnInit` must return non-zero (`0x433943`). Every callback the engine dispatches must
   exist: Sovereign Nations defines `OnInit`, `OnCreate`, `OnDestroy`, `OnTimer`,
   `OnGameMenu`, `OnTownBattleEnd`, `OnPopupText`, `OnPopupClose`. Never set
   `STATE = MSTATE_ENDED (5)`.
4. Create one instance per game. `mstart.ini` runs only on New Game, so Sovereign Nations
   creates it from the DLL instead (which also covers saves made before the mod was
   installed): `Global_CreateMission("fnn_nation", -1) GLOBALS.FNN_MISSION = 1`, guarded by
   the saved `GLOBALS.FNN_MISSION`.
5. `OnCreate` installs the event filter (`Global_InstallEventFilter(MEVENT_GAME_MENU +
   MEVENT_TOWNBATTLE_END)`).
6. Popups: `OnPopupText` returns `headerId, {bodyIds}, {buttonIds}`, **at most 3 buttons**
   (`0x57BEF1`); `OnPopupClose(self, id, button)` gets the button index (GUESS: 0-based,
   so put the consequential choice on button 0 and test it).

Traps (SOLID unless marked):

- `include.ini` is **one file shared by every mod** that overrides it. Two mods each
  shipping their own copy overwrite each other; merge them.
- Scenario games read `lua\config\scenario\00x\include.ini`, not the default one.
- GUESS: removing the script while saves still contain the mission instance breaks those
  loads. Test before you tell players they can uninstall mid-campaign.
- Keep `@` and `$` out of mission strings: the mission text resolver `0x436980` expands
  `@x$Field$` before the text box sees it.

## 4. Engine bindings worth knowing

Names from the API map (`lua_api_map.py`; its occurrence counts are a rough guide to how
central a binding is): `Global_GetPlayer`, `Global_GetFirstTown`/`GetNextTown`, `Global_CreateTimer`,
`Global_CreatePopup`, `Global_CreateMission`, `Global_InstallEventFilter`,
`Global_SaveGame`, `Global_IsWar`, `Player_GetGold`, `Player_AlterGold`,
`Player_HasLicence`, `Town_GetNation`, `Town_GetPopulation`, `Town_GetGoodsAmount`,
`Town_SetNation` (cannot create nation 5, see `05-game-systems.md`), and many more.

Binding traps (SOLID):

- The engine's bindings read their arguments with **negative stack indices**, so a
  missing or extra argument shifts all of them. Pass exactly what the binding expects.
- These need a current mission and crash (null dereference at `0x42DAEE`) when called
  from a bare `lua_dostring` in your DLL: `Global_CreatePopup`, `Global_InstallEventFilter`,
  `Global_CreateLog`, `Global_CreateLetter`, `Global_AddMessage`. Call them only from inside
  a mission callback. `Global_CreateMission` needs no context.
- Town bindings throw `CStadtHandle::operator->() - object pointer is 0` on a bad handle.

## 5. The bridge: C functions in the game's Lua, and Lua from C

**Errors are fatal.** The engine's `_ERRORMESSAGE` (`0x438920`) throws a C++ exception
inside Lua's error path. Any Lua error in the Missions state therefore unwinds through
whatever called Lua, and a Lua error outside any protected frame ends in `exit(1)`.
Every call the DLL makes into Lua must swap in a harmless handler first.

`lua.c` (vendored in `template/dll/`) does this:

| Function | What |
|---|---|
| `plua_init()` | `GetModuleHandleA("lua.dll")` + `GetProcAddress` for the 15 exports used |
| `plua_state()` | the Missions `lua_State*` via `0x004337B0`, NULL until it exists |
| `plua_register(name, f)` | `lua_pushcclosure(L, f, 0); lua_setglobal(L, name)` |
| `plua_global_is_nil(name)` | the sentinel test for re-registration |
| `plua_run(code)` | saves the stack top and `_ERRORMESSAGE`, installs a quiet handler that only records the message, runs `lua_dostring` **inside a protected `lua_call` of a C trampoline** (the parser runs the garbage collector before it sets up its own error frame), restores the handler and the stack; returns 0 or an error status and logs `LUA error st: msg \| in: code` |
| `plua_eval_int(expr, &out)` | runs `PR2N_R = (expr)` and reads the number back |
| `plua_frame_check()` | call once per frame: if a C++ exception unwound past a quiet handler and left it installed, put the engine's handler back |
| `plua_int`, `plua_str`, `plua_push_*`, `plua_argc` | 1-based argument helpers for your C functions |

A Lua function in C is `static int __cdecl f(lua_State *L)`: read arguments with
`plua_int(L, i, default)` / `plua_str`, push results, return how many. Never raise a Lua
error from it and never let a C++ exception out. `template/dll/hello.c` is a complete
example (`Hello_Version`, `Hello_GetGold`, `Hello_Gift`, `Hello_Log`).

To call a **script** function from C safely, Sovereign Nations' `dll/ui.c` (`l_guard`, the
`FNN_Guard` pattern) does the same handler swap, then `getglobal(fname)`, checks it is a
function, pushes the arguments and `lua_call`s it. Script callbacks call into the DLL
only through `FNN_Guard("fname", self, ...)` and check `FNN_Guard ~= nil` first, because
the DLL's functions appear only after re-registration.

**Sending an event from C to a script** (SOLID, used for the Annex/Withdraw popup):
build a 0x1C-byte event on the stack with `0x44DE70(ev, 0x4E41, sub, 1, -1, -1)`
(thiscall, `ret 0x14`) and post it with `0x450090(ecx = 0x702B0C, ev)`; delivery is
synchronous.

**A dev console.** In a test install, a marker file (`PR2Nation.dev` for Sovereign
Nations, `<MOD_NAME>.dev` for the template) enables extra functions and makes the DLL run
any Lua written to `PR2Nation.cmd` / `<MOD_NAME>.cmd` next to the exe, then delete it.
`python tools\test\console.py --mod <MOD_NAME> script.lua` (or `-e "code"`) writes the file
atomically into the test install and prints the log lines the run produced. Never ship the
marker file.

## What was checked for this page

- `check_ui_static.py` (Sovereign Nations): run when these docs were first written, all checks
  passed; not re-run from the kit.
- `lua_api_map.py`: run from the kit on the extracted archives.
- The template's Lua functions, the re-registration after New Game and `console.py`: run in
  game, see `../make-your-own-mod.md` step 6.
- Everything else is from the research notes and the Sovereign Nations sources, which run
  in the in-game feature suite; not re-run for these docs.
