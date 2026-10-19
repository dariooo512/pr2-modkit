# Getting started (modders)

From a fresh Steam install of Port Royale 2 to a development setup and a running modded
game. **Players need none of this**; they only need step 1 and the mod's setup
(`playing-a-mod.md`). Run every command from the kit's folder. Each step says who needs it
and whether its commands were checked for these docs.

| Step | Who | Needs | Checked |
|---|---|---|---|
| 1. Steam install | players and modders | a legal copy | n/a |
| 2. Python and packages | modders | — | yes (Python 3.12.10 and 3.10.0) |
| 3. MSYS2 GCC (32-bit) | modders writing DLLs | — | yes (GCC 16.1) |
| 4. A pristine copy of the game | modders | your game folder | no (it is a folder copy) |
| 5. A DRM-free exe for analysis | **modders only, own copy** | your `PR2.exe` | the hash check: yes |
| 6. Extract the archives | modders | step 4 | yes |
| 7. Static checks | modders | steps 5, 6 | yes |
| 8. Build the template DLL | modders writing DLLs | step 3 | yes |
| 9. The test install | modders | steps 4, 5 | partly (status, deploy, saves backup) |
| 10. A running modded game | modders | steps 8, 9 | see `make-your-own-mod.md` step 6 |

## 1. Install the game (players and modders)

Install Port Royale 2 from Steam (app 12470) and start it once from Steam, so the
`Documents\Ascaron Entertainment\Port Royale 2` folders exist. Check the version:
`PR2.exe` must have SHA-256 `5295c06689147f7565f72510aeb686719cf0ae38aa581a6df3e4d61d0cf76264`
(file version 1.1.2.3):

```
certutil -hashfile "<steam>\steamapps\common\Port Royale 2\PR2.exe" SHA256
```

**Never develop or test in the Steam folder.** Everything below works on copies, and the
tools that write an exe or start the game refuse a path inside a Steam library.

## 2. Python (modders)

Python 3.10 or newer (64-bit is fine; the tools read the 32-bit exe as data):

```
py -3.12 -m pip install pefile capstone Pillow
```

Steps 6 to 9 and the template build were also checked with Python 3.10.0.

Checked with pefile 2024.8.26, capstone 5.0.7, Pillow 12.2.0.

## 3. The C toolchain (modders writing DLLs)

Install MSYS2 (default `C:\msys64`, or `winget install MSYS2.MSYS2`), then in the MSYS2 shell:

```
pacman -S mingw-w64-i686-gcc
```

On a fresh install the first `pacman` call can fail with `unable to lock database` while
MSYS2's own first-run setup is still running. Wait for it to finish (or close it), then
run the command again; delete `C:\msys64\var\lib\pacman\db.lck` only when no `pacman.exe`
is running.

The DLL must be **32-bit**. The build scripts look for `gcc.exe` in the folder named by the
environment variable `MINGW32_BIN`, then in MSYS2's default `C:\msys64\mingw32\bin`, then on
`PATH`. Checked: `gcc.exe (Rev5, Built by MSYS2 project) 16.1.0`.

## 4. A pristine copy of the game (modders)

Copy the whole Steam game folder, right after installing and before any mod, to `_game\original\`
inside the kit (or anywhere, and set `PR2_PRISTINE` to it). The test install (step 9) is built
from it. It is the game's data: `_game\` is git-ignored; keep it out of every repository.

## 5. A DRM-free exe for static analysis (modders only)

The disassembly tools, `pe_patch.py` and the test install read the exe's code, which
SteamStub keeps encrypted on disk. A modder makes a DRM-free copy of **their own** exe for
their own analysis and keeps it private; the kit includes no tool for this and no exe. This
project used Steamless 3.1 (`techniques/03-the-exe.md` §1). Put the result at
`_game\PR2.exe.unpacked.exe` (or set `PR2_BASE_EXE`) and check it:

```
certutil -hashfile _game\PR2.exe.unpacked.exe SHA256
python tools\kit_paths.py
```

Expected `9c9250bc2b7a8068f658c0d9935bf2e7d8f9865bc31b875d9fb3b220d01cf448`. The tools
that depend on addresses refuse anything else. `kit_paths.py` prints every setting, where it
comes from (environment or default) and whether the path exists.

> Players never need this: the loader applies the mod in memory to the Steam exe
> (`techniques/03-the-exe.md` section 6).

## 6. Extract the archives (modders)

```
python tools\files\cpr_extract.py _game\original\pr2_arcs.cpr
python tools\files\cpr_extract.py _game\original\pr2_loca.cpr
python tools\files\cpr_extract.py _game\original\pr2_arct.cpr
python tools\files\cpr_extract.py _game\original\pr2_arcd.cpr
```

Each goes to `_game\extracted\<name>\` (`arcs`, `loca`, `arct`, `arcd`; setting
`PR2_WORKDIR`), about 400 MB in all. Checked: all four extracted (`arcs` 91 files, `loca` 33,
`arct` 1,983, `arcd` 4,132); `arcs`, `arct` and `arcd` are identical to an extraction made
earlier with the project's first extractor, and `loca` has the one duplicate that one missed.

## 7. Static checks (modders)

```
python tools\exe\test_pe_patch.py
python tools\exe\check_sites.py tools\exe\sites.example.txt
python tools\files\screen_dump.py verify
python tools\files\cpr_pack.py _game\extracted\arcs --like _game\original\pr2_arcs.cpr --check
```

Expected: `Ran 17 tests ... OK`; `5/5 facts hold`; `round-trip byte-identical: 254 / 254 files
(4326 controls); failures: 0`; `IDENTICAL to ...pr2_arcs.cpr`. All checked. They prove your exe
and data are the ones these docs describe, and that the file tools read and write them
correctly. (`cpr_pack.py --check` was also run on `loca`, `arct` and `arcd`: identical.)

Sovereign Nations has its own static suites (its unit tests, `tools/check_ui_static.py`,
`tools/m0_suite.py --static-only`); they live in the Sovereign Nations repository.

## 8. Build the template DLL (modders writing DLLs)

```
python template\dll\build.py
```

Expected: `built ...\template\_build\HelloMod.dll (242970 bytes)` (the size varies with the
compiler). Checked. `make-your-own-mod.md` takes it from there.

## 9. The test install (modders)

```
python tools\test\env.py setup
python tools\test\env.py backup-saves
python tools\test\env.py windowed on
python tools\test\env.py fast-boot on
python tools\test\env.py dpi-aware on        (only on a scaled HiDPI screen)
```

`setup` copies the pristine copy (step 4) to `%USERPROFILE%\PR2-test-env` (setting
`PR2_TEST_ENV`) and puts the DRM-free exe (step 5) there as `PR2_target.exe`; `status` then
reports CLEAN or every file that differs from the pristine copy. The game always writes saves
and `settings.cfg` to your Documents folder (found the way Windows reports it, also when it is
redirected), **shared with your Steam install**: run `env.py backup-saves` before an experiment
(and before `windowed on`, which edits that `settings.cfg`) and `env.py restore-saves` after
it. Checked: `status`, `deploy`/`undeploy`, `backup-saves`/
`restore-saves` and the settings switches on an existing test install; `setup` itself (a folder
copy) was not re-run for these docs.

## 10. A running modded game (modders)

`make-your-own-mod.md` builds the template mod, makes a test exe that loads it, deploys both to
the test install and runs the game in the background (`tools\test\session.py`), with the result
of the run that checked it. Sovereign Nations' own in-game suites (feature suite, scenarios,
conquest test) are in the Sovereign Nations repository.

To launch a modded game **from Steam** instead, install the mod's release with its setup
(`playing-a-mod.md`): the loader then applies the mod in memory.

## Next

- `make-your-own-mod.md`: build and run the template mod.
- `techniques/`: one page per technique.
- `dead-ends.md`: before you try something big.
