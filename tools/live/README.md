# tools/live: inspect a running game

Tools that look into the game while it runs. Python 3.12, stdlib only (ctypes on the Win32
API, from 64-bit Python against the 32-bit game). Every tool has `--help`.

| Tool | What it does | Needs |
|---|---|---|
| `dump_module.py` | Dumps a loaded module from the running game as a static PE: the way to read the UPX-packed engine DLLs (`Walhalla.dll` and friends) in a disassembler, at their real load addresses | a running test game |
| `memscan.py` | Snapshot-diff memory scanner: `snap A`, `snap B`, `snap C`, then `narrow A B C` lists the dwords that came back with the state and differed in between; also `changed`, `find`, `watch` | a running test game |
| `crashcatch.py` | Starts the game under a debugger and pins C++ exceptions to their origin: thrown type, throw site (Module+offset of the function that threw), the exception object's strings and FILE:LINE candidates, the call chain; first vs second chance | a test install |
| `proc.py` | Shared: process lookup, memory reads, the guarded launch and the test-install lock | |

Outputs go to `<PR2_BUILD>/live/` (default `_build/live/`).

## Safety

- `dump_module.py` and `memscan.py` only **read** memory (no debugger; the game keeps
  running). By default they attach to the one running process named `PR2_*.exe` (the test
  install's exes; `--process` or `--pid` to choose). They never attach to a process named
  `PR2.exe` or one running from a Steam library (`steamapps`).
- `crashcatch.py` starts only an exe inside the test install (`PR2_TEST_ENV`; default
  `PR2_TEST_EXE` or `<PR2_TEST_ENV>/PR2_target.exe`), refuses `steamapps` paths and the
  SteamStub retail exe, takes the shared test-install lock (`--wait` waits), shows the game
  window without activating it, and passes every exception on to the game unchanged.

## Examples

```
python dump_module.py --list
python dump_module.py Walhalla.dll                 # -> _build/live/Walhalla.unpacked.dll

python memscan.py snap A        # e.g. a town's stock of wood is 40
python memscan.py snap B        # ... sell some: 25
python memscan.py snap C        # ... buy it back: 40
python memscan.py narrow A B C
python memscan.py changed A B --from 40 --to 25
python memscan.py watch 0x0A1B2C30

python crashcatch.py --exe PR2_mymod.exe
python crashcatch.py --throw-helper Walhalla.dll+0x9BB26   # an engine function that throws for its caller
```

The game and its engine are built with VC7.1 and use `MSVCR71.dll`, so the throw site is
found through `_CxxThrowException`; an engine wrapper that always throws for its caller is
worth naming with `--throw-helper` so the reported site is the caller, not the wrapper. The
engine's exception objects carry the source file and line of the failed check; the
`FILE:LINE candidates` line shows them. For breakpoints, input and everyday test runs use
`tools/test/gamehost.py` instead (it also tags FIRST/SECOND chance and decodes type names).

## Known limits

- A module dump is a snapshot for reading: imports are already resolved to absolute
  addresses (the import table may not parse), relocations are applied for the load address
  and globals hold runtime values. Unreadable pages are zero-filled.
- Snapshots are tens to hundreds of MB each; heap addresses change between game sessions
  (re-find the owning object before relying on an address). Narrowing compares dwords at
  4-byte alignment only; `find --unaligned` searches every offset.
- FILE:LINE candidates are a heuristic (a source-path string member and small integers next
  to it); the type name and the throw site are exact. A C runtime linked statically into
  a module has no exported `_CxxThrowException`: pass it with `--throw-helper`.
- `crashcatch.py` logs at most `--throws` first-chance C++ throws and each (type, site) at
  most `--per-site` times; the game throws and catches some exceptions routinely, so read
  the SECOND-chance block first.
