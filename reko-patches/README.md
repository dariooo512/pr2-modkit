# reko-patches: changes to the Reko decompiler

**Licence: GPL-2.0-or-later.** These files are changes to [Reko](https://github.com/uxmal/reko)
(uxmal/reko) and are licensed like Reko itself, under the GNU General Public License,
version 2 or (at your option) any later version; the licence text is in [COPYING](COPYING).
The rest of this kit is MIT; **this folder is not**.

Both patches apply to upstream tag `version-0.12.2` (commit `06c2de0`), with paths
`a/src/...` / `b/src/...` relative to the root of a Reko checkout:

```
git clone https://github.com/uxmal/reko.git
cd reko
git checkout version-0.12.2
git apply <kit>/reko-patches/reko-0.12.2-perf-and-stackoverflow-fixes.patch
git apply <kit>/reko-patches/reko-0.12.2-emit-cpp-and-flags.patch      # optional, needs the first
```

Then build and install it as described in [tools/reko/README.md](../tools/reko/README.md).

## reko-0.12.2-perf-and-stackoverflow-fixes.patch (4 files, ~240 lines)

Without it, a whole-program decode of a ~6000-function binary does not finish (one run went
19 hours, another died of a StackOverflow). It makes the whole-program decode of PR2.exe
finish in minutes.

- `Core/Types/TypeStore.cs`: `MergeClasses` did two `Remove`s and an `Add` on a
  `SortedList` per type-variable unification, each an O(n) array shift, so type analysis was
  O(n²). Now a `Dictionary`, sorted once when enumerated.
- `Decompiler/Typing/UnionAlternativeChooser.cs`: `Choose` is memoised (the graph does not
  change during the rewrite) and capped at 4000 types per query.
- `Decompiler/Typing/ComplexExpressionBuilder.cs`: a depth cap (200) on union and
  equivalence-class recursion; a cyclic chain recursed until an uncatchable StackOverflow.
- `Decompiler/Typing/TypedExpressionRewriter.cs`: per-procedure progress logging.

## reko-0.12.2-emit-cpp-and-flags.patch (12 files, ~2,900 added lines; applies on top of the first)

The emitter and driver work done for PR2 (comments in the code are tagged `[PR2]`). It
changes Reko's C output itself, not only behind flags: the aim was a `program.c` /
`program.h` that a C/C++ compiler accepts, so stock output differs in many places (a C
prelude of fixed-size types in `program.h`, forward declarations for every procedure and
every called external, casts at mismatched calls, stores and operands, `SEQ`/`SLICE`/
`CONVERT` and `<invalid>` lowered to real C, sized integer mul/div/mod as explicit casts,
C/C++ keyword-safe identifiers, ordered and cycle-broken type declarations). It also fixes a
dataflow abort (an FPU-argument throw in `CallApplicationBuilder` that left phi nodes in the
output) and prints some diagnostics to stderr (`[PHI-DIAG]`, `[ADF-FAIL]`, `[CACHE]`).

Environment variables it adds (all optional; `tools/reko/decode.py` sets them from flags):

| Variable | Effect |
|---|---|
| `REKO_EMIT_CPP=1` | readable C++: native `base.field` / `ptr->field` member access instead of byte-offset reinterprets |
| `REKO_SKIP_LISTINGS=1` | do not write the `.asm` / `.dis` listings (~50 MB for PR2) |
| `REKO_HEADER_ONLY=1` | write `program.h` only |
| `REKO_PHASE_TIMING=1` | log the wall-clock as each phase starts (scan, dataflow, types, structure, emit) |
| `REKO_CACHE_SAVE=<file>` | after structuring, save the analysed state (type graph + structured procedures) |
| `REKO_CACHE_LOAD=<file>` | skip scan, dataflow, type inference and structuring; re-emit from the saved state in seconds |
| `REKO_CACHE_SELFTEST=1` | round-trip a small synthetic graph through the cache serializer, print PASS/FAIL and exit |

Cache limits: a cached run does not restore globals or external-procedure declarations, so
use it to iterate on the emitter, not for a final build; regenerate the cache whenever the
analysis code, the input binary or the project changes.

The patch is the exact difference between upstream 0.12.2 + the first patch and the tree the
kit's results were produced with (verified: applying both to a clean `version-0.12.2`
checkout reproduces every changed file byte for byte).
