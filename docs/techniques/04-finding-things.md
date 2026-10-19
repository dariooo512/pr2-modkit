# Finding things in the engine

PR2.exe is about 6 MB of VC++ 7.1 (Visual Studio 2003) code with STLport, about 6,000
game functions and no symbols. This page is how the map in `05-game-systems.md` was made,
and how to extend it. Everything here works on the DRM-free image (`03-the-exe.md` §1).

## 1. The order that worked

1. **Start from a string or a constant.** Exception strings name classes and methods
   (`CNation::...`, `CStadtHandle::operator->() - object pointer is 0`), file names lead to
   loaders (`towns\%s_Stadtplatz.tow` → `0x61A410`), Lua binding names lead to the C
   function behind them. Find the string, then its code references.
2. **Read one function at a time** in a decompiler view, and only the functions on your
   path. Write down every claim with its evidence level.
3. **Check the claim at runtime** with a breakpoint or a memory watch
   (`gamehost.py --bp`, `07-testing.md`) before you build on it.
4. **Find every consumer before you change anything**: every read of the field or
   constant, at byte, word and dword width. This is where the ceilings are (the 4x towns
   failed on a byte-wide row coordinate nobody had listed).
5. **Turn the claim into a fact** in a facts file (`tools\exe\check_sites.py`) so it is
   re-verified on every run.

## 2. Small tools (Python, capstone + pefile)

All in `tools\exe\`, all read-only, all reading the DRM-free exe (setting `PR2_BASE_EXE`,
or `--exe`). Checked from the kit on the DRM-free exe for these docs:

| Command | What | Result of the check |
|---|---|---|
| `python tools\exe\disasm_func.py 0x41C6F0` | disassemble a function (to the CC padding; `--len` for a range, `--bytes` for the encoding) | 38 instructions, `0x41C6F0 .. ~0x41C73A` |
| `python tools\exe\disasm_func.py 0x41C6F0 --callers` | every `E8` call / `E9` jmp rel32 to it | 1 caller, `0x6238D3`; `0x5C9DA0` (AddToNation) has 4 |
| `python tools\exe\pe_xref.py 0x6ABDD8` | byte-level references to a VA, plus the enclosing function | 1 reference, in `0x61A410` (the `.tow` loader) |
| `python tools\exe\find_disp.py 0x702B14` | every instruction whose disp32 or imm32 is the value (an address, a field offset, a constant) | 109 instructions (the `g_nations` census in §4) |
| `python tools\exe\find_disp.py 0x358 --operand [--range LO HI]` | the same over decoded operands, any width (8/16-bit displacements, `cmp eax, 4`) | 45 instructions in `.text`; a linear sweep, so it can miss code right after data: cross-check 32-bit values with the default mode (47) |
| `python tools\exe\check_sites.py FACTS.txt` | your facts: address + expected instructions, bytes or string | `5/5 facts hold` on `sites.example.txt` (and 3 of 5 fail on the encrypted Steam exe, as they should) |
| `python tools\exe\codegen_diff.py --orig-va 0x41C6F0` | dump an original function's bytes (or diff them against a recompiled one) | 74 bytes |
| `python tools\files\lua_api_map.py` | every Lua binding name from `missions.bin` and the text scripts | 1,719 names, identical to the map these docs were written from |

The project's own fact checker (197 facts for Sovereign Nations, each an address with the
instruction text or bytes expected there) is not published, because it holds the original bytes
of every site; `check_sites.py` is the same mechanism for your facts. A FAIL means the exe
changed or a note is wrong; fix the note in the same commit.

For engine DLLs that are packed (`Walhalla.dll` is UPX-compressed),
`python tools\live\dump_module.py Walhalla.dll` dumps the decompressed module from a running test
game and rebuilds a static PE at its real load address (`tools/live/README.md`; not run for these
docs).

## 3. Decompilers (reading aids, not truth)

- **Ghidra** (headless) is the reference view. `tools\ghidra\ghidra.py` imports the exe once into
  a project under `_build\ghidra\` (setting `GHIDRA_HOME` for the install) and runs the scripts:
  `dump VA` (pseudo-C, disassembly and bytes per function), `decompile VA`, `callgraph` (a
  call-graph worklist with depth and fan-in), `midbody VA` (functions entered in the middle of
  their body: unsafe to replace in place), `constants C...` (every operand equal to chosen
  constants: the "list every consumer" check), `apicalls`, `callers`. Details:
  `tools/ghidra/README.md`. Checked with Ghidra 12.1.2: `import --no-analysis` (21 s), `dump
  0x41C6F0` (74 bytes, one range), `decompile`, `midbody`, `constants`, `callers`, `apicalls`,
  `count` and `callgraph` all ran.
- **Reko 0.12.2** (.NET, native on Windows) gives a second view and, unlike Ghidra, one
  20 MB `program.c` for the whole program that is easy to grep (its exception strings name
  classes). Drivers in `tools\reko\` (setting `REKO_HOME`), details in `tools/reko/README.md`:
  - an entry-point scan finds only 24 procedures: seed every function address (from
    Ghidra's `callgraph`) as a root in the `.dcproject` (`gen_project.py`);
  - per-function `--data` decoding is fast and complete (6,032/6,032) but drops return
    values and `this` (`decode_isolated.py`);
  - a whole-program decode needs three performance/stack-overflow fixes to Reko
    (`reko-patches/reko-0.12.2-perf-and-stackoverflow-fixes.patch`, GPL) and stubbing the
    few functions that crash its SSA pass with `ret` (`run_whole.py`, `skip.txt`); it then
    finishes in about 4 minutes. `reko-patches/` also has the readable-C++ emitter and the
    other environment switches used here (`reko-0.12.2-emit-cpp-and-flags.patch`).
  Checked with a patched Reko 0.12.2: `gen_project.py`, `decode_isolated.py` (2 functions
  decoded), `decode.py`, `run_whole.py` on a small function list and `split_funcs.py` ran;
  the patches apply to upstream `version-0.12.2` with `git apply --check`.
- **RetDec** (MIT): the whole-binary run crashes on this exe; per function it lost `this`
  and partial-register context. A sweep produced 4 compilable, byte-exact functions out of
  3,743. Not recommended, and the kit has no RetDec driver.
- **`tools\ghidra\gather_decomp.py VA`** puts the Ghidra and Reko views (and a RetDec one, if
  you have it) side by side per function (`_build\multi\0x<VA>.multi.txt`). When they
  disagree, the bytes decide.

The decompiler outputs themselves are the game's code and are not part of the kit;
regenerate them from your own copy and keep them out of every repository.

## 4. Mapping structures and offsets

Examples of how the facts in `05-game-systems.md` were found:

- **The nation table**: 109 absolute references to `0x702B14` (`find_disp.py 0x702B14`), each
  classified by its guard (`cmp x,4`, fixed 4-slot loops, base walks); that census showed the
  table cannot grow and which sites a nation 5 can reach (Sovereign Nations'
  `research/null_nation_census.md`).
- **Town fields**: the Lua bindings are thin wrappers, so `Town_GetNation` disassembles to
  a read of `CStadt+0x390` with a fallback to `+0x391`; every other binding gives one
  field the same way (Sovereign Nations' `research/economy_data.md` builds a whole C header
  from them).
- **Who changes a value**: find the only setter (`AddToNation` `0x5C9DA0` has exactly 4
  callers) and hook or call that, never the field.
- **Which code an action runs**: record a session where you do the action against a
  baseline session that does nothing (`tools\replay\recorder.py census`, then `record-new`);
  the difference is the action's code (`07-testing.md` §5).
- **Snapshot diffs**: `tools\live\memscan.py` diffs memory snapshots of the running game
  before and after a change (`snap A`, `snap B`, `snap C`, `narrow A B C`; it also finds values
  in the exe's writable sections, the globals).

## 5. Rebuilding a function from source (advanced, rarely needed)

When a change is too large for a hook, the function can be rewritten in C/C++ and
compiled with the game's own compiler so the result drops into the exe:

- Toolchain: **Microsoft Visual C++ Toolkit 2003** (VC7.1, `cl` 13.10; the Toolkit installs
  13.10.3052, Visual Studio .NET 2003 has 13.10.3077), with `cl /c /O2` (the flags the game
  used; measured). It is no longer distributed by Microsoft and the kit does not ship it;
  `tools/rebuild/README.md` describes how it was installed on Windows 11. Setting `VC71_BIN`.
- Conventions: write `__thiscall` methods as `__fastcall(ecx, edx)` (cl rejects an explicit
  `__thiscall`); name callees and globals `sub_<VA>` / `DAT_<VA>`.
- `tools\rebuild\`: `classify.py` (calling convention, arguments, whether a function is
  self-contained), `scaffold.py` (a compilable skeleton with the callees and globals
  declared), `build.py` compiles one function and compares it with the original,
  `relink.py` carves the `.obj` into a copy of the exe and resolves `sub_<VA>`/`DAT_<VA>` to
  the original addresses (switch tables included), `validate_patch.py` checks that a patched
  exe differs only inside the intended function bodies, `gen_harness.py` runs the original
  bytes (read from your exe at run time) against the rebuild on random inputs,
  `bisect_patch.py` finds the one bad function in a set with a test command of yours, and
  `extract_fn.py` / `patch_fn.py` do the same carve from a `/FAcs` listing. Limit: **in place
  only**, no new code section and no new globals (that is why `pe_patch.py` exists).
  Checked with cl 13.10.3052 on functions written for the check (not game code): built,
  carved into a scratch copy and compared exact; `validate_patch.py` VALID on the intended
  range and INVALID otherwise; the harness PASS on a match and FAIL on stubs; `classify.py` and
  `scaffold.py` on 40 game functions (all 40 skeletons compile).
- `tools/rebuild/README.md` §5 sums up the VC7.1 code-generation tactics that make a rebuild
  match. The project's per-function tactic notes and its worked example are not published:
  they quote the game's code.

**Lesson** (`../dead-ends.md`): rebuilding as a goal does not ship features. Rebuild a
function only when a mod must change it, and treat any reconstruction as a reading aid
until it is checked against the original bytes.
