# Releasing a mod

A public release is the setup program plus a zip of the mod's own files: the DLL, the loose
data files and the patch data. No game file, no patched or unpacked exe, no `.cpr` archive or
anything extracted from one. Sovereign Nations' release tools are the template; they are in
the Sovereign Nations repository (paths below are relative to it), not in this kit.

## The pieces

- **Patch data.** Export your exe patches with `tools/export_patches.py --check` (it builds on
  the kit's `pe_patch.py`): the same
  patch modules you build and test with, written as address / original bytes / new bytes, the
  code-cave section and its relocations. `--check` proves the memory image equals the file
  build. Format: `loader/README.md`.
- **The loader** (`loader/`, `python loader/build.py`): reads the patch data
  next to the exe, applies it in memory at the CRT's first call, loads the DLL the data names
  (the `dll <name>` record). Reuse it unchanged; your mod changes only the patch data and the DLL.
- **The setup** (`installer/sn_setup.py`): finds the Steam install, checks the
  `PR2.exe` hash (`KNOWN_EXES`), installs from the folder `mod\` with its `MANIFEST.json`
  (sha256 per file), backs up replaced files, records everything for Uninstall, updates in place,
  repairs, and asks before installing next to other mods' loose files. For your own mod change
  the names at the top (`APP`, `STATE`, `BACKUP`).
- **The release builder** (`tools/release.py`): builds the DLL, the loader and
  the patch data, copies the files, writes `MANIFEST.json`, the player's `HOW TO INSTALL.txt` and
  the docs, freezes the setup into an `.exe` with PyInstaller when it is installed (otherwise a
  script and a `.bat` that need Python), and zips it. `--dgvoodoo DIR` adds dgVoodoo2's
  unmodified files from an official release (its licence allows shipping them with a game mod).

## Safety checks before you publish

- `release.py` refuses to build when any release file equals one of the game's files (it hashes
  the test install's game folder and the extracted archive contents) or is a game file type.
  Files *derived* from game data (screens cloned from vanilla controls, an extended
  `include.ini`) pass this check. The safer choice is not to ship them and to generate them
  on the player's machine from the player's own game files.
- Scan the release for personal data: absolute user paths, machine names, emails, save names.

## Tests

- `tools/test_installer.py`: install, update, repair and uninstall on throw-away folders with a
  copy of the Steam `PR2.exe`; uninstall must leave the folder byte-identical.
- `tools/loader_probe.py`: in a test install, the mod from memory on the DRM-free image, the
  loader idle on a patched exe, the game unmodded on a mismatched patch.
- Before a public release: in a Steam install you may test on, run the setup, start the game from
  Steam, check `PR2Loader.log`, play, uninstall, and diff the folder against a clean install.

## Coexisting with other mods

Only one DLL can own a given detour (the frame hook, a patched call), and loose files such as
`include.ini` or `TOPMENUBAR_800x600.screen` are shared: the setup backs them up and restores
them, but two mods that replace the same file cannot both be active.

## Not for public releases

The project also had tools that copy a patched exe into an install, or zip a whole test install
(about 500 MB, the whole game), for its own test machines. They are not published: a public
release never carries a game file or a patched exe.
