# tools/ghidra: headless Ghidra scripts

[Ghidra](https://ghidra-sre.org/) is the reference decompiler view of the exe. These scripts
run headless (no GUI) through one wrapper, `ghidra.py`, against a Ghidra project the kit keeps
in `<PR2_BUILD>/ghidra/project`. Script output goes to `<PR2_BUILD>/ghidra/out` (default
`_build/ghidra/...`, git-ignored). It is decompiled game code: never commit or publish it.

## Setup

- Install Ghidra (written against 12.1; recent versions should work; it needs the JDK its
  release notes name) and set `GHIDRA_HOME` to its folder, the one that holds
  `support\analyzeHeadless.bat`.
- `PR2_BASE_EXE` is the binary `import` takes by default (the DRM-free 1.1.2.3 exe).
- Import once. Full auto-analysis of the 6 MB exe takes several minutes, then every script
  run reuses the saved analysis and takes seconds:
  ```
  python tools/ghidra/ghidra.py import                  # with analysis (needed for callgraph/count/constants)
  python tools/ghidra/ghidra.py import --no-analysis    # ~20 s; dump/decompile create the function on demand
  python tools/ghidra/ghidra.py import --exe <path to an engine DLL>   # more programs in the same project
  ```
  Pick the program with `--program <file name>` (default: the file name of `PR2_BASE_EXE`).
- `python tools/kit_paths.py` shows what the settings resolve to.

## Commands

| Command | Script | What it does |
|---|---|---|
| `dump VA...` | DumpFunc.java | decompiled C + disassembly + raw bytes per function, to stdout and `out/0x<VA8>.decomp.txt` |
| `decompile VA...` | DecompileFunc.java | decompiled C only, to stdout |
| `callgraph` | CallGraph.java | `out/worklist.tsv`: every function with size, call depth (leaf = 0), fan-in/out, a name class. The function list the Reko drivers read |
| `count` | CountFuncs.java | totals, thunks, leaves, a size histogram |
| `midbody VA...` | MidbodyRefs.java | is the function entered anywhere but its entry point? (`UNSAFE` = do not replace it in place with a body of another length) |
| `constants C...` | ExportFuncs.java | `out/<program>.funcs.tsv`: every function, with the instructions whose operand equals one of the constants (hex `0x..` or decimal). Finds the code behind a magic number (a stride, a cap, a size) |
| `apicalls --out-file F SYM...` | ApiCalls.java | every function that references the named symbols (e.g. `CreateFileW socket`), decompiled, plus callers to `--depth` levels |
| `callers --out-file F VA...` | Callers.java | reference tree upward from addresses, through calls and data references (vtables, pointer tables) |
| `run SCRIPT ARGS...` | any | any Ghidra script in `tools/ghidra` or a `--script-path` folder |

Common options: `--project-dir`, `--project-name` (default `PR2`), `--out`, `--read-only`
(do not save functions the scripts create), `-v` (print the analyzeHeadless command line).
Scripts read their output folder from the env var `PR2_OUTDIR`, which the wrapper sets.

`DecompileFunc.py` is the same as `DecompileFunc.java` in Python. Ghidra 12 runs `.py`
scripts only when started through PyGhidra (`support/pyghidraRun`); the wrapper uses the
Java scripts, which need nothing extra.

## Several decompilers side by side

```
python tools/ghidra/ghidra.py dump 0x41C6F0
python tools/ghidra/gather_decomp.py 0x41C6F0      # -> _build/multi/0x0041C6F0.multi.txt
```

`gather_decomp.py` puts the Ghidra dump, the Reko view (see [tools/reko](../reko/README.md))
and, if you have one, a RetDec view in one file. Where they agree the logic is almost
certainly right; where they differ, the disassembly and the raw bytes decide. All inputs and
the output folder are arguments (`--help`).

## Notes

- A project is locked while a headless run has it open: run one command at a time per
  project (or give parallel runs their own `--project-dir`).
- Addresses are virtual addresses in the 1.1.2.3 image (ImageBase `0x400000`), hex, with or
  without `0x`. Output files are named by the requested address, padded to 8 digits.
- A function body need not be contiguous; `dump` prints the raw bytes range by range.
