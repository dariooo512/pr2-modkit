# tools/reko: Reko decompiler views of the exe

[Reko](https://github.com/uxmal/reko) 0.12.2 is a .NET decompiler that runs natively on
Windows. It gives a second view next to Ghidra and, unlike Ghidra, can decode the whole
program in one dataflow pass. These drivers produce one C file per function, by two
routes with different trade-offs.

Everything they write goes to `<PR2_BUILD>/reko/` (default `_build/reko/`, git-ignored). It is
decompiled game code: never commit or publish it.

## Setup

1. **Get Reko.** Download the `CmdLine-0.12.2-x64` asset of the
   [0.12.2 release](https://github.com/uxmal/reko/releases/tag/version-0.12.2) and extract it
   to a folder. Point `REKO_HOME` at that folder (the one with `decompile.exe`). It needs the
   .NET 8 runtime or newer. This stock build is enough for `decode_isolated.py`.
2. **Patch it** for the whole-program route (required: without the fixes a whole-program
   decode of PR2 does not finish). See [reko-patches/](../../reko-patches/README.md), GPL:
   ```
   git clone https://github.com/uxmal/reko.git && cd reko
   git checkout version-0.12.2
   git apply <kit>/reko-patches/reko-0.12.2-perf-and-stackoverflow-fixes.patch
   git apply <kit>/reko-patches/reko-0.12.2-emit-cpp-and-flags.patch   # optional
   dotnet build src/Decompiler/Decompiler.csproj -c Release -f net8.0   # builds Core too
   ```
   Copy `Reko.Core.dll` and `Reko.Decompiler.dll` from `src/Decompiler/bin/Release/net8.0/`
   over the ones in `REKO_HOME`. Only those two assemblies change; `decompile.exe` stays.
   (`Core.csproj` treats missing XML doc comments as errors: give any public member you add a
   `/// <summary>`.)
3. **A function list.** Reko's own scan from the PE entry point finds only about **24
   procedures** in PR2.exe: everything else is reached through vtables and indirect calls.
   So every driver is seeded with the functions Ghidra found:
   ```
   python tools/ghidra/ghidra.py import            # with analysis, once
   python tools/ghidra/ghidra.py callgraph         # -> _build/ghidra/out/worklist.tsv
   ```
   Any TSV with `addr` (hex) and `size` columns works (`--funcs`); a `cls` or `nameClass`
   column filters it (`--classes`, default `game,suspect_util`; `--include-all` takes every row).

The exe is `PR2_BASE_EXE` (the DRM-free 1.1.2.3 build); it is only read.

## Route 1: per-function, isolated (`decode_isolated.py`)

```
python tools/reko/decode_isolated.py --jobs 8          # every function in the list -> _build/reko/raw/0x<VA8>.c
python tools/reko/decode_isolated.py 0x41C6F0          # one function
python tools/reko/decode_isolated.py --status
```

Runs `decompile --data <hex> --base <VA>` on each function's bytes alone. Fast (all ~6000
game functions in about 12 minutes at `--jobs 8`), small memory, resumable, never crashes on
the SSA bug, and complete (in the original run: 6032/6032 decoded, 0 failures, ~1.7% empty
bodies, 2 functions too big for the Windows command line, written as stubs).

The price: with no caller in view, Reko's liveness analysis decides nobody reads `eax`, so
**return values are dropped** (functions come out `void`; a tiny leaf can be eliminated
entirely, e.g. `0x4D5AD0` becomes `{}`), **`this` is lost**, imports show as
`(*(<anonymous>**)<addr>)()` and globals as raw constants. Control flow and calls survive
well: read it as a logic map, not as source.

## Route 2: whole program (`run_whole.py`, `decode.py`, `split_funcs.py`)

```
python tools/reko/run_whole.py          # -> _build/reko/whole/program.reko/program.c (+ program.h)
python tools/reko/split_funcs.py        # -> _build/reko/byfn/0x<VA8>.c
python tools/reko/decode.py [knobs]     # later re-decodes of the same project
```

`gen_project.py` (called by `run_whole.py`) writes `all.dcproject` with every listed function
as a `<procedure>` root, so Reko decodes the call graph in one pass: direct calls give it
inter-procedural liveness, and callees keep their returns and parameters, named globals and
RTTI types.

Two things are needed to make it finish:

- **The perf patch** (reko-patches/). Stock 0.12.2 has O(n²) type-variable merging and an
  unbounded recursion; on PR2 it ran 19 hours or overflowed the stack.
- **Stubbing the functions that crash Reko.** A few functions trip an SSA bug ("Offset must
  be non-negative (Parameter 'bitOffset')"), and one of them aborts the whole run with no C
  output. `decompile="false"` does not help. `run_whole.py` overwrites each crasher's entry
  byte with `ret` (0xC3) in a throwaway copy of the exe, runs, reads the next crasher from
  `program.reko/analysis_99_crash.txt`, adds it to the skip list and repeats until a run is
  clean. The skip list is `_build/reko/skip.txt`, seeded from [skip.txt](skip.txt), which is
  an example of the format holding the two crashers found for PR2.exe 1.1.2.3.

With the patch, the run took **about 4 minutes of CPU** when first measured (6965 functions,
a 20 MB `program.c`); later measurements with the emitter patch put it nearer 30 minutes,
~95% of it in type inference, which is single-threaded. Memory peaks high: run one decode at
a time, kill a leftover `decompile.exe` before the next one (it also locks the DLLs you
deploy), and keep `PR2_BUILD` on a local disk, not a synced folder (the 6 MB exe copy is
rewritten every iteration). An unpatched whole-program decode of all roots needed 22 GB+ and
ran out of memory on a 32 GB machine.

Quality: much better naming and types than route 1, but not compilable C, and stock output
carries phi-node noise (`<unknown> ϕ`) and some `return <invalid>`. The emitter patch removes
most of that and aims at output a compiler accepts.

`decode.py` knobs (environment variables of the emitter patch; stock Reko ignores them):
`--keep-listings` (listings are skipped by default), `--header-only`, `--phase-timing`,
`--emit-cpp` (readable C++ member access), `--cache-save FILE` / `--cache-load FILE` (save
the analysed state once, then re-emit in seconds while you change only the emitter;
regenerate after any analysis change). Details in
[reko-patches/README.md](../../reko-patches/README.md).

## Side by side

`python tools/ghidra/gather_decomp.py 0x41C6F0` puts the Ghidra dump, the Reko view (`byfn/`
first, then `raw/`, then carved from `program.c`) and an optional RetDec view in one
`_build/multi/0x<VA8>.multi.txt`.

## Gotchas

- Reko addresses are hex **without** `0x` (`--base 004D5AD0`, `<address>004D5AD0</address>`);
  with `0x` it reports an invalid address. The drivers do this for you.
- Each `--data` run writes `image.reko/` into its working folder, so `decode_isolated.py` gives
  every job its own temporary folder under `_build/reko/work/`.
- `--data` passes the bytes on the command line (2 characters per byte, 32767 maximum), so
  functions over ~14 KB get a stub file pointing at the whole-program route.
