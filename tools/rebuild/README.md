# tools/rebuild: rebuilding a function from source

Rewrite one function of the exe in C/C++, compile it with the game's own compiler, prove
that the result matches the original byte for byte, then change it and carve it back into
a copy of the exe, in place. The technique is described in
`docs/techniques/04-finding-things.md` section 5; read `docs/dead-ends.md` first: rebuilding
is a reading aid and a last resort for changing a function, not a goal of its own.

| Tool | What it does |
|---|---|
| `build.py` | compile one source file with cl 13.10 (`cl /c /O2`), place it at its VA with references resolved, compare with the original bytes; prints the two listings side by side and hints on a miss |
| `relink.py` | carve one or more compiled `.obj` into a **copy** of the exe, references resolved to the original addresses, the rest of each body filled with int3 |
| `validate_patch.py` | check that a patched exe differs from the original only inside the declared function bodies |
| `bisect_patch.py` | find which of several rebuilt functions breaks the game, with your own test command |
| `classify.py` | static facts about functions: convention, stack arguments, globals, calls, x87, and whether an execution oracle can test them (method A) or not (method B) |
| `scaffold.py` | write a compiling skeleton: signature, convention, extern declarations for every callee and global, import names, the disassembly as comments |
| `gen_harness.py` | generate (and run) an execution oracle: original bytes and your rebuild on random inputs |
| `names_bootstrap.py` | a name table from the import directory (IAT slot -> DLL!function), plus names you supply |
| `extract_fn.py` | pull a function's bytes out of a `cl /FAcs` listing and check that they are position-independent |
| `patch_fn.py` | drop position-independent bytes (from `extract_fn.py`) over a function in a copy of the exe |
| `pe_image.py` | shared helpers (not a command) |

Every tool has `--help`. All of them read the exe given by `--exe`, default `PR2_BASE_EXE`
(the DRM-free PR2.exe 1.1.2.3, see `tools/kit_paths.py`); any other 32-bit PE works too,
for example one of the engine DLLs (the VA is looked up in whichever section holds it, so
an unpacked image whose code is not in `.text` works). They write only under the work
folder (default `_build/rebuild/`, git-ignored) or to a path you give, and **never write
an exe in place**: `relink.py` and `patch_fn.py` refuse an output that is their input.

Files these tools generate from your exe (scaffolds, harnesses, listings) contain its
disassembly or point into it. Keep them in `_build/` or your own work folder; do not
publish them.

## 1. Install the compiler: Visual C++ Toolkit 2003 (cl 13.10) on Windows 11

The game was built with VC7.1 (Visual Studio .NET 2003). Its free command-line build, the
Visual C++ Toolkit 2003, produces the same code. Microsoft no longer distributes it, and
its stock installer **does not work on 64-bit Windows 11**. This is how it was installed
anyway (checked on Windows 11; needs an elevated prompt for the `msiexec` and `reg` steps).

**Why the stock installer fails.** `VCToolkitSetup.exe` is an InstallShield "Package for
the Web" stub that (1) installs .NET Framework 1.1, (2) installs `ISScript9.Msi`, (3)
installs the toolkit MSI. On 64-bit Windows 11 step 1 fails with `1603: Error installing
Microsoft(R) .NET Framework` and aborts everything (the compiler does not need .NET 1.1),
and the InstallShield engine itself fails with "Failed to load resources from resource
file". Compatibility modes do not fix the .NET step. So: skip the .NET check, let the stub
extract the MSIs, and install them with `msiexec`.

1. **Get the installer.** A copy is on the Internet Archive:
   `https://archive.org/download/microsoft-visual-c-toolkit-2003/VCToolkitSetup.exe`,
   32,952,488 bytes, MD5 `3e1e1cad2e4b4e2427dbe4616cccaa02`,
   SHA1 `956c81c3106b97042c4126b23c81885c4b5211f4`.

   Version note: that copy is toolkit v1.0, **cl 13.10.3052**. The later v1.01 has
   13.10.3077, the build number of Visual Studio .NET 2003. Microsoft states there are no
   code-generation changes between them, only the build stamp, and 3052 is what the
   rebuilds in this kit's history were matched with. (One unmatched code pattern was
   suspected, not proven, to be a compiler-build difference; see section 5.)

2. **Make the stub skip .NET 1.1.** Save this as `fake_net11.reg` and import it
   (`reg import fake_net11.reg`, elevated). It plants a ".NET 1.1 installed" detection key
   in both registry views:

   ```reg
   Windows Registry Editor Version 5.00

   ; TEMPORARY: makes the VC++ Toolkit 2003 stub believe .NET 1.1 is installed.
   [HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\NET Framework Setup\NDP\v1.1.4322]
   "Install"=dword:00000001
   "SP"=dword:00000001
   "Version"="1.1.4322"

   [HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\NET Framework Setup\NDP\v1.1.4322]
   "Install"=dword:00000001
   "SP"=dword:00000001
   "Version"="1.1.4322"
   ```

3. **Catch the MSIs.** Run `VCToolkitSetup.exe`. It extracts its payload to a folder
   `%TEMP%\_isXXXX\`, including `Microsoft Visual C++ Toolkit 2003.msi` (about 7.0 MB) and
   `ISScript9.Msi` (about 0.8 MB), then the InstallShield engine dies and the folder is
   **deleted within about a second**. Copy the MSIs out while they exist: start this loop
   in a second PowerShell window first, then the installer, and stop the loop (Ctrl+C) once
   both files are in the target folder:

   ```powershell
   $dst = "$env:USERPROFILE\vctk-msi"; New-Item -ItemType Directory -Force $dst | Out-Null
   while ($true) {
     Get-ChildItem "$env:TEMP\_is*" -Recurse -Filter *.msi -ErrorAction SilentlyContinue |
       ForEach-Object { Copy-Item $_.FullName $dst -Force -ErrorAction SilentlyContinue }
     Start-Sleep -Milliseconds 15
   }
   ```

   Windows XP SP3 compatibility mode on `VCToolkitSetup.exe` helps the stub start; it is
   not needed for the extraction itself.

4. **Install the MSIs directly** (elevated; both should exit with 0). The toolkit MSI's
   only launch condition is "not Windows 9x/NT4", which passes on Windows 11, so no MSI
   editing is needed. It is an InstallScript MSI, so the script runtime goes first:

   ```bat
   msiexec /i "ISScript9.Msi" /qn /norestart
   msiexec /i "Microsoft Visual C++ Toolkit 2003.msi" /qn /norestart
   ```

5. **Remove the fake key** (elevated):
   `reg delete "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v1.1.4322" /f` and the same
   under `HKLM\SOFTWARE\WOW6432Node\...`.

6. **Point the kit at it.** The toolkit installs to
   `%ProgramFiles(x86)%\Microsoft Visual C++ Toolkit 2003`. Set `VC71_BIN` to its `bin`
   folder (there is no default), then check:

   ```bat
   set VC71_BIN=%ProgramFiles(x86)%\Microsoft Visual C++ Toolkit 2003\bin
   python tools\kit_paths.py
   "%VC71_BIN%\cl.exe"
   ```

   `cl` prints `Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 13.10.30xx for
   80x86`. The tools set `INCLUDE` and `LIB` to the toolkit's own folders themselves.

The toolkit ships the compiler, the C runtime and the C++ library, but **not** the
Platform SDK (`windows.h`) or most import libraries (`kernel32.lib` is included). Pure
functions need none of it. If a rebuilt function needs Win32 declarations, declare the
few you need yourself (as `gen_harness.py` does for `VirtualAlloc`) or add a 2003-era
Platform SDK with `build.py --include`.

## 2. The workflow

```bat
rem 1. what is it, how can it be checked?
python tools\rebuild\classify.py 0x00XXXXXX:SIZE
rem 2. a skeleton to fill in (SIZE from your disassembler; without --size it is guessed)
python tools\rebuild\scaffold.py 0x00XXXXXX --size SIZE --out work\0x00XXXXXX.cpp
rem 3. write the body, then loop until exact=True
python tools\rebuild\build.py work\0x00XXXXXX.cpp --size SIZE
rem 4. optional, method-A functions only: run it against the original on random input
python tools\rebuild\gen_harness.py 0x00XXXXXX --size SIZE --run --source work\0x00XXXXXX.cpp
rem 5. now change the function, rebuild (no longer exact, by design), carve it into a copy
python tools\rebuild\relink.py --out _build\rebuild\PR2_test.exe 0x00XXXXXX:_build\rebuild\0x00XXXXXX.obj:SIZE
rem 6. nothing else changed?
python tools\rebuild\validate_patch.py _build\rebuild\PR2_test.exe 0x00XXXXXX:SIZE
```

Then test the copy in the disposable test install (`docs/techniques/07-testing.md`). If
several rebuilt functions break the game together, `bisect_patch.py --test "<command>
{exe}"` finds the one: your command gets the step's exe and answers with its exit code.

Matching first matters: a byte-identical rebuild proves you have understood the function
(types, convention, every branch). Change it only after that, as little as possible.

`extract_fn.py` + `patch_fn.py` are the older, simpler path for a function with no calls
and no global references: compile with `cl /c /O2 /FAcs`, extract the bytes from the
listing, drop them in. `build.py` + `relink.py` do everything they do and also resolve
references.

**The function size.** Take it from a disassembler (Ghidra, `tools/ghidra`). Never find
it by scanning for int3 padding: functions are often packed back to back with no int3
between them, so a scan runs into the next function and the patch corrupts it (this
happened). Disassemblers can also *under*-count: a function whose epilogue is reached only
by forward jumps (common with exception frames) can end a few bytes past the reported
size. `build.py` compares over the whole recompiled length for that reason.

**Before replacing a body**, check that nothing jumps into the middle of it (a shared
tail, a jump table): the function's cross-references must all point at its entry. A
replacement keeps the entry address, not the inner ones.

## 3. Conventions for rebuilt code

- **One function per file**, compiled with exactly `cl /c /O2` (the flags the game was
  built with, found by experiment: `/O1`, `/Ot`, `/Os`, `/G6`, `/G7`, `/GB` all matched
  fewer functions). `build.py` passes only `/nologo /c /O2`; add flags with `--cl-flag`.
- **Name what you reference by its original address**: callees `sub_<VA>`, globals
  `DAT_<VA>` (also `FUN_`, `loc_`, `LAB_`, `g_`, `byte_`, `word_`, `dword_`, `unk_`). The
  relinker finds the `<tag>_<hex>` token anywhere in a decorated or C++-mangled symbol name
  and fixes the relocation (`DIR32` absolute, `REL32` relative) to that address, so no map
  file is needed. Use `extern "C"` to keep names readable. The compiler's 64-bit helpers
  (`__allmul`, `__alldiv`, `__allshr`) resolve to the game's copies automatically when the
  target is PR2.exe 1.1.2.3; anything else: `--symbol NAME=VA`.
- **`__thiscall` as `__fastcall(ecx, edx)`.** cl 13.10 refuses an explicit `__thiscall`
  keyword (error C4234, "reserved for future use"). A method is written `extern "C" R __fastcall sub_<VA>(void* ecx, void*
  edx, <stack args>)`: the object arrives in ECX, EDX is a dummy, the stack arguments and
  the callee-pops-them `ret N` are the same as `__thiscall`. A function that reads neither
  register (`__stdcall`) can use the same form. A function that ends in a plain `ret` but
  reads stack arguments is `__cdecl` (the caller pops them); `classify.py` reports this.
- **Calling a method** from rebuilt code: `sub_X(obj, 0, a)` through the fastcall form
  makes the compiler emit `xor edx, edx` first. When the original has no such
  instruction, declare the callee as a member of a dummy struct
  (`struct T { void sub_X(int a); };` then `((T*)obj)->sub_X(a)`); a member call is a true
  `__thiscall` and leaves EDX alone. The struct name does not matter; the method name
  carries the address.
- **Return `this`.** A function that starts with `mov eax, ecx` returns its object: return
  type `void*`, `return ecx;`. Forgetting this was the single most common reason a rebuild
  did not match.
- **Imported functions** are called through their IAT slot (`call dword ptr [slot]`):
  `extern "C" void* DAT_<slot>;` and `(*(R (__stdcall**)(ARGS))&DAT_<slot>)(...)`.
  `names_bootstrap.py` and `scaffold.py` tell you which import a slot is. **Imported
  methods** (a C++ DLL's class): with no arguments, a one-argument fastcall pointer puts the
  object in ECX without touching EDX; with arguments, load a member-function pointer from
  the slot (`typedef void (T::*M)(int); M m = *(M*)&DAT_<slot>; (obj->*m)(a);`), or declare
  `struct __declspec(dllimport) DAT_<slot>_x { void f(int); };`: the compiler then calls
  through `__imp_...DAT_<slot>_x...`, which the relinker resolves to the slot by the
  address token in the mangled name.
- **Virtual calls**: a struct whose vtable has placeholder virtual methods up to the slot
  you need, then `obj->slot_n(...)`.
- **No new data.** In-place rebuilding adds no section and no globals. A string literal, a
  float constant or a `static` in your source becomes data in the `.obj` that cannot be
  placed, and `relink.py` refuses it as unresolved. Reference the original's copy instead:
  `extern "C" const float DAT_<VA>;` (`const double` for 8 bytes), `extern "C" char
  DAT_<VA>[];` for a string. Switch jump tables are fine: cl puts them in `.text` after the
  function and the relinker places them with it.
- **Fit.** The compiled code must fit the original body. A change that needs more room or
  new global state is not a rebuild: use `tools/exe/pe_patch.py` (a new section) and a DLL
  (`docs/techniques/03-the-exe.md`).

## 4. Verifying: method A and method B

`classify.py` sorts functions into two classes:

- **Method A, execution oracle.** A *self-contained* function (no global or image address,
  no call, no jump out, no x87) can run on its own: `gen_harness.py` copies its original
  bytes into executable memory and runs them side by side with your rebuild on random
  inputs (a large random `this` object and random scalar arguments), comparing the return
  value and the whole object afterwards. A function that dereferences a pointer argument,
  or a pointer stored in its object, faults on random data; `clean_auto` marks the ones
  that do neither.
- **Method B, compare the code.** Everything else (globals, calls, floating point) cannot
  run standalone. It is checked by `build.py`: byte identity, or at least the same
  instruction structure.

What the history of this pipeline taught:

- **Byte identity is the only gate that is safe by construction.** Identical machine code
  is identical behaviour, register use and ABI. Behaviour on random inputs is evidence,
  not proof: functions that passed tens of thousands of random cases still crashed the
  game when about 80 of them were patched in together. They depended on a global or a
  pointer member the classifier missed, and behaved correctly on random data by luck.
- **Inline asm hides ABI bugs.** A `__declspec(naked)` body that clobbers EBX, ESI, EDI or
  EBP without saving it, or cleans the stack wrongly, passes the oracle (the harness does
  not depend on those registers across the call) and corrupts the real caller.
  C-compiled code cannot have this bug: the compiler writes the prologue and epilogue.
  `build.py` refuses `__asm`, `naked` and `_emit` unless the original function has a C++
  exception frame (`mov eax, fs:[0]` near its start), whose prologue cannot be written in
  C (`--allow-asm` overrides). Audit any asm body for callee-saved registers and stack
  cleanup before patching it in.
- **`_emit` is not a rebuild.** Pasting the original bytes back proves nothing.
- **Patch few functions at a time.** One missed dependency anywhere crashes the game, and
  the risk multiplies with the number of patched functions. Safest first: pure leaves (no
  `this`, no globals, no pointers), then getters and setters on `this` only, everything
  else only byte-identical.
- **The classifier errs.** Absolute addresses reached through an index register
  (`[reg*4 + table]`), or an address loaded as an immediate and dereferenced later, were
  missed at first; `classify.py` now treats any image address, in a memory operand or an
  immediate (`imm_image_ref`), as not self-contained. A stored vtable pointer is also an
  image-address immediate and is harmless; `gen_harness.py --force` accepts it, check the
  disassembly first.

## 5. Matching tactics for cl 13.10 `/O2`

General patterns that decide whether a rebuild matches. Read the two listings `build.py`
prints and change the C, never the behaviour.

**Types decide opcodes.**
- Signed compares (`jl`, `jg`, `jle`, `jge`) and `movsx` mean `int`/`short`/`signed char`;
  unsigned ones (`jb`, `ja`, `jbe`, `jae`) and `movzx` mean unsigned. A wrong signedness or
  width changes compare, extend and load bytes.
- A result cleared or returned in a byte register (`xor al, al`, a flag kept in BL then
  `mov al, bl`) is an `unsigned char`, not a `bool` (which tends to give `setcc`) and not
  an `int` (`xor eax, eax`). The same for 16-bit and `unsigned short`.
- Operand size in a memory access (`byte ptr` / `word ptr` / `dword ptr`) is the width of
  the pointer you cast to.
- Fields declared as integers can be updated in memory in one instruction
  (`add dword ptr [esi+4], 4`); declared as pointers, the compiler may load, add and store.

**Arithmetic.**
- cl chooses `lea`, shifts or `imul` itself for small constant multiplies (for example
  `x * 3` as `lea eax, [eax+eax*2]`). If the original multiplies, write the multiply with
  the exact constant, signed if the immediate is negative.
- `(x >> n) << n` with no other use of the shifted value folds to one `and`; if the
  original has the two shifts, put `x >> n` in a named variable and multiply it back
  (`n * 4`), which keeps `sar` + `shl`.
- `abs()` from `<stdlib.h>` gives the `cdq; xor; sub` sequence; `v < 0 ? -v : v` gives a
  branch.
- `memset(p, 0xFF, n)` and block copies through a struct assignment produce `rep stosd`
  and the `lea reg, [base+off]` plus small-offset copy forms; individual stores do not.
- 64-bit compares: `< 0` and `<= 0` on an `__int64` produce different final branches
  (`jae` vs `ja` after the high-word test).

**Order, aliasing and registers.**
- The compiler keeps source order only where it cannot prove two accesses independent.
  A store that the original does early but yours does late: write it through a named
  pointer; a load the original repeats but yours reuses: read it through `volatile`, or
  re-declare the pointer inside the loop body.
- To avoid repeated reloads of a pointer field, copy it into a local first.
- Arguments are pushed right to left; an argument the original pushes early is the
  rightmost parameter.
- `__declspec(noreturn)` on a throw helper stops the compiler from saving values across
  a call that never returns.
- Which register holds a local (ESI vs EDI, EAX vs ECX) is decided by the whole function.
  Reordering declarations or which value is computed first sometimes flips it; often it
  does not (see below).

**Control flow.**
- The order of `if`/`else` branches, early returns and a shared exit decide block layout
  and which way a conditional jump points. Write the exit the original jumps to as one
  shared statement at the end when several paths reach it.
- `do { } while` vs `while` vs a guard plus loop changes loop rotation (an extra `jmp`
  into the loop, or not); a redundant `while (cond)` after an `if (cond)` guard can stop
  the compiler from inverting the loop.
- A separate variable for a value computed in a loop body can keep a dead `xor eax, eax`
  the original has.

**Functions with a C++ exception frame** (`mov eax, fs:[0]` near the start: try/catch,
or objects with destructors). cl always builds an EBP frame for them, and the exact
frame and state bookkeeping are rarely reproducible in C. Inline asm is the accepted
exception there (`build.py` allows it), with these MSVC inline-assembler rules:
- Use `__declspec(naked)` and write the whole body in one `__asm` block; a plain `__asm`
  block inside a normal function still gets a compiler prologue in front of it.
- `push offset SYM` pushes the address; `push SYM` pushes the value stored there.
- `mov reg, [SYM]` with `SYM` declared as a scalar (`extern "C" void* DAT_<VA>;`) gives
  the memory load; a bare hex address in brackets (`[0x...]`) assembles as an immediate,
  and an array-typed symbol may change the encoding.
- Callees called from asm are best declared with an empty `__cdecl` parameter list, so
  the name decoration is plain and no C call sequence is generated around them.
- `align 16` before a loop label reproduces the multi-byte `lea`-NOP padding the
  compiler put before 16-byte-aligned loop tops.
- x87 instructions need both operands written out (`fsubr st, st(1)`); the one-operand
  forms are refused (error C2415).
- Labels defined twice: the last definition wins, silently.

**Often not reachable in C** (stop after a few tries; a behaviour-equal rebuild is still a
reading aid):
- differences in register allocation only (same instructions, other registers);
- functions that match only when compiled together with their neighbours, because the
  optimizer shared constants or common subexpressions across the original translation
  unit;
- long constant mask chains that `/O2` folds to one immediate in a standalone function;
- an extra widening (`movsx`) the original has and cl 13.10.3052 omits, suspected to be a
  compiler-build difference (not proven).

## 6. Reference

- `docs/techniques/04-finding-things.md`: where this technique sits among the others.
- `docs/techniques/03-the-exe.md`: the exe, its DRM-free build, and patching with a new
  section instead.
- `docs/dead-ends.md`: why a broad rebuild of the whole exe was abandoned.
