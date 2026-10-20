# Releasing a mod

A public release is the setup program plus a zip of the mod's own files: the loader, the DLL,
the patch data and the loose data files. No game file, no patched or unpacked exe, no `.cpr`
archive or anything extracted from one. The kit builds it from the mod's `install.json`:

```
python tools/release/build_release.py ..\my-mod\install.json
```

→ `..\my-mod\_build\release\MyMod_<version>\` and `MyMod_<version>.zip`. Sovereign Nations'
release is made the same way (its `tools/release.py` calls this builder with its own
`install.json`).

## install.json

Every key is described at the top of `tools/release/install_json.py`; `template/install.json` is
a working example. Paths are relative to the file.

| Key | What |
|---|---|
| `name`, `id` | the mod's name for players; `id` (default: the name without spaces) names `<id>.install.json`, `_<id>_backup` in the game folder and the release folder |
| `version` | default `v<date>-<git commit>` |
| `dll` | the built DLL (installed next to `PR2.exe` under its file name) |
| `patch_data` | where the patch data is written, e.g. `_build/loader/MyMod.patches`: the file name is the mod's own |
| `patches` | the `pe_patch.py` modules the loader applies in memory (globs allowed); `[]` when the mod needs no exe patch |
| `section_name`, `section_size`, `section_va` | the patch section (see the rules below for `section_va`) |
| `data_dir` | loose files installed at the same relative path (README.md left out) |
| `supported_exe_sha256` | the `PR2.exe` builds the setup accepts (list, or `{sha256: label}`) |
| `build` | python scripts run first, e.g. `["dll/build.py"]` |
| `docs`, `how_to`, `how_to_banner` | files copied into the release; your own HOW TO INSTALL text |
| `logs` | files the mod writes while playing; Uninstall removes them |
| `setup` | `{"title": ..., "image": "a PNG for the top of the window", "dgvoodoo": true}` |
| `warn_if_present` | another mod's loose files the setup asks about before installing |
| `check_ignore` | your own files in the test install that the game-file check skips |

## The pieces (all in the kit)

- **The loader** (`loader/`, `loader/README.md`): one `version.dll` for every mod. It reads every
  `*.patches` file next to the exe, applies each mod's patches in memory at the C runtime's first
  call and loads the DLL that file names. Never change it for your mod: a game folder can only
  have one, and each mod's setup carries the same file.
- **Patch data** (`tools/release/export_patches.py install.json --check`): the same patch modules
  you build and test with (the kit's `pe_patch.py`), written as address / original bytes / new
  bytes, the code-cave section and its relocations, the mod's name and its DLL. `--check` proves
  the memory image equals the file build and that the data still works when the section moves.
- **The setup** (`tools/release/mod_setup.py`, frozen into `<name> Setup.exe`): the same program
  for every mod; its name, window title and image come from `mod/MANIFEST.json`. It finds the Steam
  install, checks the `PR2.exe` hash, installs from the folder `mod\` (sha256 per file), backs up
  replaced files, records everything in `<id>.install.json` for Uninstall, updates in place,
  repairs, and asks before installing next to files `warn_if_present` lists.
- **The release builder** (`tools/release/build_release.py`): runs your build scripts, builds the
  loader, exports the patch data with `--check`, copies the files, writes `MANIFEST.json`, the
  player's `HOW TO INSTALL.txt` / `.html` and `VERSION.txt`, freezes the setup with PyInstaller when
  it is installed (otherwise a script and a `.bat` that need Python), runs the game-file check and
  zips it. `--dgvoodoo DIR` adds dgVoodoo2's unmodified files from an official release (its licence
  allows shipping them with a game mod).

## One loader, many mods: the rules

- **Exe patches must not overlap.** At start the loader takes the `*.patches` files in file-name
  order and refuses a file whose patch sites or section overlap a file accepted before it;
  `PR2Loader.log` names both files and the address, and the game starts with the other mods.
  A mod whose patches do not match the game build is left out the same way (it patches nothing).
- **Sections:** every section defaults to the address after the exe's last section (`0x9F7000`), so
  two mods with code caves overlap there. Give yours another `section_va` (for example
  `0x00A10000`) if it must run next to a mod with a section, such as Sovereign Nations. If the
  memory is taken at start the loader moves the section anyway and tells your DLL in
  `<STEM>_SECTION` (the patch data's file name in capitals: `MYMOD_SECTION`).
- **No exe patch, no patch data problem:** a mod that only needs its DLL loaded (the template) has
  `"patches": []`; its patch data holds the trigger and the DLL, and it overlaps nothing. Do not
  put `template/patches/load_mod_dll.py` in `patches`: its entry-point detour is for a modder's test
  exe only, and it collides with every other mod that loads its DLL that way (Sovereign Nations'
  P06).
- **Runtime hooks chain.** `hook.c` (template and Sovereign Nations) finds another mod's 5-byte jmp
  where the original code should be and chains behind it, so two DLLs can hook the same function
  (both use the per-frame hook on `A2dLib!CWalhallaClient::InputProcessor`). Byte patches
  (`patch_bytes`) still refuse bytes that differ.
- **One loader file:** each setup installs the loader only if the one in the folder is older
  (`PR2-LOADER <n>` in the file); Uninstall leaves it while another mod's `<id>.install.json`
  lists it, and the last Uninstall removes it or puts back what was there before the first mod.
  Sovereign Nations' first loader (it read only `PR2Nation.patches`) is replaced by any newer
  setup, which also updates Sovereign Nations' install record to the new file.
- **Shared names:** prefix your Lua functions; two mods cannot install the same loose file with
  different contents (the second setup refuses); the same file with the same contents (dgVoodoo2's
  D3D8.dll) is shared until the last uninstall.
- **Switches:** `<STEM>_OFF=1` in the environment starts the game without that mod,
  `PR2LOADER_OFF=1` without any.

## Safety checks before you publish

- `build_release.py` refuses to build when any release file equals one of the game's files (it
  hashes the pristine game folder and the test install, settings `PR2_PRISTINE` and
  `PR2_TEST_ENV`, and the extracted archive contents, `PR2_WORKDIR`) or is a game file type.
  Files *derived* from game data (screens cloned from vanilla controls, an extended
  `include.ini`) pass this check. The safer choice is not to ship them and to generate them
  on the player's machine from the player's own game files.
- Scan the release for personal data: absolute user paths, machine names, emails, save names.

## Tests

- `tools/release/test_setup.py`: the setup on throw-away folders with a copy of the Steam
  `PR2.exe`: install, status and uninstall (byte-identical), two mods sharing the loader and
  uninstalled in either order, a foreign `version.dll` handed over between mods, the newest loader
  winning, conflicting and shared data files, the migration from Sovereign Nations' first loader,
  and the overlap rule of the patch data.
- In game, in a test install: start the DRM-free `PR2_target.exe` (which imports `version.dll`
  like the Steam exe) with the release installed by its setup, and read `PR2Loader.log` and your
  mod's log; then uninstall and compare the folder.
- Before a public release: in a Steam install you may test on, run the setup, start the game from
  Steam, check `PR2Loader.log`, play, uninstall, and diff the folder against a clean install.

## Checked end to end

On 20 Oct 2026, with the kit's tools only for the template, in a throw-away copy of the pristine
game folder with the DRM-free `PR2_target.exe` (it imports `version.dll` like the Steam exe; the
Steam exe itself was not started). The test is Sovereign Nations' `tools/loader_probe.py --mods`.

1. `build_release.py template/install.json`: `HelloMod_v0.1.zip`, 9 files, `check: none of 9
   release files is one of the game's 6420 files`; `test_setup.py`: 9 pass, 0 fail.
2. `HelloMod Setup.exe install --game <copy>`: 3 files and `HelloMod.install.json`. In game,
   `PR2Loader.log`: `HelloMod.patches (HelloMod): format 2, 0 patches, no section; loads
   HelloMod.dll` and `... 0 patches applied in memory, no section; HelloMod.dll loaded`;
   `HelloMod.log`: `HelloMod.dll loaded`, `HOOK A2d.InputProcessor @... ok`, `Hello_* Lua API
   registered (4 functions)`; its dev console answered `LUA e2e | HelloMod 0.1`.
3. `HelloMod Setup.exe uninstall`: the folder byte-identical to before (every file hashed).
4. Sovereign Nations' release (built with the same builder from its `install.json`) and HelloMod
   installed side by side by their setups: both DLLs loaded, the second frame hook chained behind
   the first, a save loaded with its nation, and `Nation_GetInfo()` and `Hello_Version()`,
   `Hello_GetGold()` answered in the same game (`HelloMod 0.1`, the player's 180,000 gold).
5. A third file overlapping Sovereign Nations' patches (`load_mod_dll.py` exported as
   `ZClash.patches`): `ZClash.patches (ZClash): conflicts with PR2Nation.patches (Sovereign
   Nations): both change the bytes at 009F7000. Only one of the two mods can run`; the other two
   loaded and the game ran.
6. HelloMod uninstalled: the loader stayed, `loader 2: 1 patch file(s)`, Sovereign Nations worked
   alone. Then Sovereign Nations uninstalled: the folder byte-identical to before.

Not checked yet: the start through Steam itself with the generic loader (the Steam exe runs the
same CRT start-up, so the trigger is the same; the test above never starts it).

## Not for public releases

The project also had tools that copy a patched exe into an install, or zip a whole test install
(about 500 MB, the whole game), for its own test machines. They are not published: a public
release never carries a game file or a patched exe.
