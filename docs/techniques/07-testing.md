# Testing

The tests are what made Sovereign Nations dependable: every task was accepted on its
tests, and most of them run in the background while you keep using the PC. The general
tools are in this kit (`tools\test\`, `tools\exe\`, `tools\files\`, `tools\replay\`); the
suites that test Sovereign Nations' own features are in the Sovereign Nations repository.
All paths come from the settings in `tools\kit_paths.py`.

## 1. Tests that need no game

| Command | What | Needs | Checked for these docs |
|---|---|---|---|
| `python tools\exe\test_pe_patch.py` | the patch builder: section table, detours, trampolines, relocations, manifest, reproducibility, every refusal | the DRM-free exe | yes: `Ran 17 tests ... OK` |
| `python tools\exe\check_sites.py FACTS.txt` | your binary facts (address + expected instructions, bytes or string) | the DRM-free exe; for `Module.dll+RVA` facts the pristine copy | yes: `5/5 facts hold` on `tools\exe\sites.example.txt` |
| `python tools\files\screen_dump.py verify [DIR]` | parse → write and parse → JSON → write give identical bytes for every `.screen` | the extracted archives | yes: 254/254 vanilla screens, 7/7 localised |
| `python tools\files\cpr_pack.py DIR --like ORIG.cpr --check` | the archive writer reproduces an archive byte for byte | the pristine copy and the extracted archives | yes: all four archives identical |
| `python template\dll\build.py` | the template DLL builds without warnings (`-Wall -Wextra`) | MSYS2 GCC | yes |

Sovereign Nations' own static suites (its 695 unit tests of the nation logic, the patch tests
`test_patches.py`/`test_p04.py`/`test_p05.py`, `check_ui_static.py` for the `.res` format and
the mission script run in the real `lua.dll`, and `m0_suite.py --static-only`) are in its
repository. Its 197-fact checker is not published (it holds the original bytes of each site).

## 2. The test install

`tools\test\env.py` keeps a **disposable copy of the game** (setting `PR2_TEST_ENV`, default
`%USERPROFILE%\PR2-test-env`), built from your pristine copy (`PR2_PRISTINE`), outside any
synced folder and separate from the Steam install, so no other mod leaks into a test:

```
python tools\test\env.py setup              # create/verify; puts the DRM-free exe in as PR2_target.exe
python tools\test\env.py status             # CLEAN or a list of differences from the pristine copy
python tools\test\env.py deploy FILE... [--to SUBDIR]   # your test exe, DLL, loose files (recorded)
python tools\test\env.py undeploy           # remove them, put back anything they replaced
python tools\test\env.py backup-saves       # snapshot the Save folder (with settings.cfg) and the Run folder
python tools\test\env.py restore-saves [DIR]  # put the newest (or a given) snapshot back
python tools\test\env.py windowed on|off
python tools\test\env.py fast-boot on|off   # park the intro videos
python tools\test\env.py dpi-aware on|off   # no 2x window scaling on HiDPI screens (test exes only)
```

`setup` refuses a pristine copy that is not the Steam 1.1.2.3 build, a DRM-free exe with the
wrong hash, and a test install inside a Steam library. `restore-saves` never deletes a save
made since the snapshot: it moves it to `_build\backup\aside-<time>\`.

Trap: the game writes saves and `settings.cfg` to the user's Documents folder (found the way
Windows reports it, also when it is redirected to a synced folder), **shared with the Steam
install**: back both up before a test and restore them afterwards.

**Sharing one test install between several agents or terminals**: take the lock before
starting the game and release it when the game has exited:

```
python tools\test\testlock.py take "who and why"     # waits while someone else holds it
python tools\test\testlock.py release "who and why"  # only the owner can release it
python tools\test\testlock.py status
```

The lock is a directory next to the install (setting `PR2_TEST_LOCK`, default `<test
install>.lock`) with an `owner.txt`; it is created atomically (`mkdir`). From Python,
`with testlock.held("my probe"): ...` releases it in a `finally`. The record/replay and crash
tools take it themselves.

## 3. Driving the game in the background

- **`tools\test\gamehost.py`**: starts a test exe under a debugger (`--exe NAME`, a file in the
  test install; it refuses `PR2.exe` and anything in a Steam library). Classifies exceptions
  (FIRST or SECOND chance, C++ type names), logs breakpoints without stopping (`--bp
  VA=reg,reg+off:len`), logs who calls `ExitProcess`, keeps the window in a 1280×800 corner of
  the screen from its first frame by hooking `CreateWindowEx`/`SetWindowPos`/`MoveWindow`, and
  injects input and function calls on the UI thread **through A2dLib's own handlers**, because
  the game reads DirectInput and ignores window messages. It never takes focus or the real
  mouse. Log: `_build\runs\<time>\gamehost.log`.
- **`tools\test\cmd.py`**: commands to a running gamehost: `click X Y`, `move`, `wheel`,
  `key VK`, `read`, `write`, `bp`, `call 0xVA THIS [int / s:string]`, `towns`, `setnation`.
- **`tools\test\session.py newgame | load NAME | stop`**: switches the test settings on
  (windowed, intro parked, DPI flag, sound muted), refuses to start while the screen saver or
  the lock screen is up, starts the game under gamehost, starts a free-play game (writing the
  60 towns, in `staedte.ini` order with names, to `_build\towns.tsv`) or loads a save, and leaves
  it running; `stop` ends it.
- **`tools\test\screen.py shot NAME`**: a screenshot by PrintWindow, so it works while the
  window is covered (shot pixels = `cmd.py click` coordinates); `place` moves the window. It
  sends no input.
- **`tools\test\console.py --mod NAME script.lua`** (or `-e "code"`): runs Lua in the game
  through a mod DLL's dev console (`02-lua.md` §5) and prints the log lines it produced.
- **`tools\test\mute_game.ps1 -Watch`**: mutes every `PR2_*.exe` in the Windows volume mixer
  (never the Steam `PR2.exe`); `session.py` starts it with `-IdleMinutes 10`.
- **`tools\test\keepawake.py [hours] [--input]`**: keeps the display on during long runs;
  `--input` also resets the input-idle timer for screen savers that ignore Windows' settings
  (some OEM OLED-care tools). Direct3D 8 cannot start while a screen saver runs. Stop it when
  the run ends.
- **`tools\test\hangdump.py`**: the threads of a hung game, from outside (`08-debugging.md`).

Checked for these docs: all of them compile and answer `--help`; `env.py`, `testlock.py`,
`mute_game.ps1`, `gamehost.py`, `cmd.py`, `session.py`, `screen.py` and `console.py` in the
in-game run of `../make-your-own-mod.md` step 6 (see there for what ran).

## 4. In-game suites (Sovereign Nations)

Sovereign Nations' suites show what a full test setup looks like; they are in its repository and
were not re-run for these docs:

| Tool | What it proves |
|---|---|
| `tools/m0_suite.py --minutes 4` | builds P01–P05, deploys, and runs: a vanilla-like game survives N minutes at 20x (REG-01), towns set to nation 5 survive (TER-01), QuickSave → cold restart → load keeps them (SAV-01), war/peace decisions follow the table (MIL-01) |
| `tools/feature_suite.py --days 31` | builds the DLL and all patches, deploys, starts a new game, drives the features through the mod's Lua API and checks game memory |
| `tools/conquest_test.py --route B --no-speedup --no-attack-clicks --zero-defenders --resolve ui` | a real land assault on a town, annexed through the popup (CON-01), and an attack on the own capital refused (CON-02) |
| `tools/scenarios.py --make` / `--run` / `--doc` | builds prepared saves in the Save folder and runs one action on each: founding, month end, insolvency, unrest, secession, war, treaties, conquest, a save round trip, AI strategy |
| `tools/*_probe.py` | one per task: each loads a scenario save, does the task's action and checks the result, with screenshots |

Scenario saves and screenshots contain game content and are not published; make your own from
your copy of the game.

## 5. Record/replay and behavioural equivalence

From the source-reconstruction attempts (see `04-finding-things.md` and `dead-ends.md`); the
tools are in `tools\replay\` (details in its README):

- **`recorder.py`** records real calls of chosen functions in a running game (arguments, the
  objects they touch, the pages they change) and **`replay.py`** replays them against the
  original or a rebuilt function (`slice_src.py` builds that DLL) in a suspended game
  process. Its most useful mode for modders is the **differential capture**: `recorder.py
  census` then `record-new` records a session where you do one thing (declare war, open a
  window) against a baseline session that does nothing, and lists the functions that only
  ran in the first (`new_targets.tsv`). That is the quickest way to find the code behind an
  action. `functions.py` makes the target list from the exe. Limits: the corpus grows huge,
  and writes to globals are not compared.
- **`fuzz.py`** runs a rebuilt function against the original bytes on random inputs (no
  game; cl 13.10), only for self-contained functions; **`coverage.py`** lists which functions
  ran in a session.
- Lesson from the earlier pipeline: promote nothing on fewer than 10 cases, and compare
  global state too.

Checked: all compile and answer `--help`; `fuzz.py` gave PASS 20000/20000 on a byte-exact
rebuild and REFUSED a function that is not self-contained; `slice_src.py` built a DLL from a
test source. The recorder, replay and coverage were not run against the game for these docs.

## 6. Writing tests for your mod

- Put pure logic in a file with no engine calls and test it on the host with the same
  compiler (Sovereign Nations' `dll/tests/run_tests.py` is the pattern).
- Put every address you rely on into a facts file for `check_sites.py`.
- For in-game checks, drive the mod through its own Lua API from a script (the dev
  console), read results from the log, and take screenshots; never use the real mouse or
  focus.
- Classify every failure (`08-debugging.md` §1) before you chase it.
