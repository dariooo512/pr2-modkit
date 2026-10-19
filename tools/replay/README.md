# tools/replay: record, replay, differential capture, fuzz, coverage

Runtime analysis of the game's own code. All of it is Python 3.12 (stdlib + `pefile` +
`capstone`) driving the Win32 debug API against the 32-bit game from 64-bit Python.
Every tool has `--help`.

| Tool | What it does | Needs |
|---|---|---|
| `functions.py` | Lists function entry points (direct `call` targets that are instruction starts) as a target list, `_build/replay/functions.tsv` | the DRM-free exe (`PR2_BASE_EXE`) |
| `recorder.py` | Starts the game under a debugger, puts an INT3 on every target and records calls: entry registers, the memory the call sees, the pages it changes. Its **differential capture** (`census`, then `record-new`) lists the functions one action runs | a test install with the DRM-free exe |
| `replay.py` | Re-runs recorded cases inside a fresh, suspended game process (original code, or a rebuilt function from a DLL) and compares the result to the recording | the same exe the cases were recorded with |
| `slice_src.py` | Builds that DLL from one rebuilt function in a `.c`/`.cpp` file, with its callees and globals bound to the game's real addresses | 32-bit MinGW g++ (`MINGW32_BIN`) |
| `fuzz.py` | Runs the original bytes and a rebuilt function side by side on random inputs (no game) | `PR2_BASE_EXE`, Visual C++ Toolkit 2003 (cl 13.10, `VC71_BIN`) |
| `coverage.py` | One-shot INT3 on every target: which functions ran in a session | a test install |
| `common.py` | Shared: settings, the guarded game launch, the test-install lock, target lists | |

Outputs go to `<PR2_BUILD>/replay/` (default `_build/replay/`): `functions.tsv`,
`baseline.tsv`, `corpus/session_<pid>/`, `recon/`, `fuzz/`, `coverage/`.

## Safety

Every tool that starts the game takes the shared test-install lock (`PR2_TEST_LOCK`; `--wait`
waits for it), and starts only an exe **inside the test install** (`PR2_TEST_ENV`; default
`PR2_TEST_EXE` or `<PR2_TEST_ENV>/PR2_target.exe`, see `tools/test/`). It refuses any path
containing `steamapps` and the SteamStub retail exe, starts the game from its own folder with
its window shown but not activated, and kills it when the tool ends. Record with the DRM-free
exe: the INT3s go at fixed addresses of the 1.1.2.3 image.

## Find the code one action runs (differential capture)

```
python functions.py --min-callers 2          # once; a target list
python recorder.py census --targets _build/replay/functions.tsv
    # BASELINE: play to where your action starts (say, the main menu, or a loaded save),
    # do nothing else, quit the game -> _build/replay/baseline.tsv
python recorder.py record-new --targets _build/replay/functions.tsv
    # ACTION: same start, do the one thing, quit. Only targets the baseline never ran are
    # armed -> corpus/session_<pid>/new_targets.tsv (+ light state cases)
```

`new_targets.tsv` lists every function the action ran that the baseline did not, `captured=1`
first (a replayable case exists), then smallest. That list is where to start reading. Make
the baseline as close to the action as you can: whatever differs between the two sessions
(a different save, a screen you opened) shows up as "new".

## Record and replay

```
python recorder.py probe                              # the debugger works on this machine
python recorder.py record 0x0041C6F0 --max 4          # full snapshots (hundreds of MB/case)
python recorder.py record --targets list.tsv --light --interactive
python replay.py selftest _build/replay/corpus/session_1234     # original vs its recording
python slice_src.py build 0x0041C6F0 my_rebuild.cpp
python replay.py recon _build/replay/corpus/session_1234        # rebuilds from _build/replay/recon
```

Replay compares the return value (`--ret-width`, default 32 bits; give `0` for a void
function, or a `--ret-widths` TSV with `addr` and `ret_width` columns) and the pages of the
object (`ecx`) and pointer arguments. Run `selftest` first: a case where the original code
does not reproduce its own recording cannot judge a rebuild.

## Fuzz a rebuild

```
python fuzz.py one 0x0041C6F0 my_rebuild.cpp --iters 300000
python fuzz.py sweep --list vas.txt --src-dir my_src/       # my_src/0x<VA>.cpp each
```

Only for position-independent functions (no calls, no globals, no switch tables); the tool
checks the original's disassembly and answers `REFUSED` otherwise.

## Known limits

- **The corpus grows huge.** Full snapshots are hundreds of MB per case; a long full-capture
  session reached tens of GB. Use light capture (`--light`, the default for `record-new`) and
  small `--max`/`--per-fn`; delete sessions you no longer need.
- **Writes to globals are not compared.** Replay checks the return value and the object and
  pointer-argument pages only. Much of the game's state is global (nation and town tables),
  so a rebuild that writes a global wrongly can still PASS. Fuzz compares only the `ecx`
  buffer and the return value.
- **Promote nothing on fewer than 10 passing cases.** A handful of cases covers a handful of
  paths; random fuzz inputs rarely reach rare branches.
- Targets come from direct calls only (`functions.py`); functions reached only through
  vtables or function pointers need a list from Ghidra/Reko. A target that is not a real
  instruction start corrupts the game's code when armed.
- While a captured call runs, every other thread is frozen and the function is
  single-stepped past its INT3: the game is slow while hot functions are armed. Each target
  disarms after `--per-fn` cases.
- Allocation-dependent pointers and debug-CRT fill are treated as noise; a function that
  stores a freshly allocated pointer in its object can still fail the self-test (it is
  nondeterministic, not wrong).
- Replay starts a fresh game process per case (to the exe entry point only, about a second).
