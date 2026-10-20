# The loader (`version.dll`): one for every mod

A player's game folder gets one loader, whatever the number of mods. Each mod brings its own
patch data, `<Mod>.patches`, and its DLL; the loader reads every `*.patches` next to `PR2.exe`.
`PR2.exe` itself is never changed on disk, so the Steam DRM runs as always.

Build: `python loader/build.py [--out FILE]` (32-bit MinGW GCC, setting `MINGW32_BIN`) →
`_build/loader/version.dll`. The release builder (`tools/release/build_release.py`) builds it for
each release; patch data comes from `tools/release/export_patches.py` (both are driven by the mod's
`install.json`, see `docs/techniques/09-releasing-a-mod.md`).

## How it works

The game imports `VERSION.dll`, which is not a KnownDLL, so Windows loads this file from the game
folder first; every export forwards to the real `version.dll` from the system folder.

1. In `DllMain` the loader reads the `*.patches` files **in file-name order** (case-insensitive)
   and accepts each one that
   * is valid patch data with an intact checksum (format 2 below, or Sovereign Nations' format 1);
   * is not switched off: `<STEM>_OFF` in the environment (`HELLOMOD_OFF=1`, `PR2NATION_OFF=1`);
     `PR2LOADER_OFF=1` switches off every mod;
   * has the same trigger (`hook` record) as the files accepted before it;
   * **changes no byte that an accepted file changes**: its patch sites and its section must not
     overlap any accepted file's patch sites or section. A file that overlaps is refused; the log
     names the file it collides with and the address, and the game starts with the other mods.
2. It hooks one import slot, `MSVCR71!__set_app_type` (the `hook` record). The C runtime calls it
   right after the entry point, when the game code (decrypted by the Steam DRM) is final.
3. At that call it puts the slot back and, for each accepted file in order:
   * if the exe already carries the file's patches (a patched test exe, the file route), it does
     nothing for that mod: that exe loads the DLL itself;
   * it checks the original bytes of **every** patch site of the file; on any mismatch it patches
     nothing of that mod and does not load its DLL (not the game build it was made for);
   * else it puts the section at its address if that memory is free, otherwise anywhere, applying
     the file's relocations and setting `<STEM>_SECTION=<hex address>` for the mod's DLL; writes the
     patches; loads the DLL the `dll` record names (a file name next to the exe).

`PR2Loader.log` (next to the exe) has one line per file and decision, each naming the file and the
mod (`name` record), for example:

```
12:00:01 loader 2: 2 patch file(s) next to the game
12:00:01 HelloMod.patches (HelloMod): format 2, 0 patches, no section; loads HelloMod.dll
12:00:01 PR2Nation.patches (Sovereign Nations): format 2, 22 patches, a section; loads PR2Nation.dll
12:00:01 HelloMod.patches (HelloMod): 0 patches applied in memory, no section; HelloMod.dll loaded
12:00:01 PR2Nation.patches (Sovereign Nations): 22 patches applied in memory, section at 009F7000; PR2Nation.dll loaded
```

and for a refused file `ZClash.patches (ZClash): conflicts with PR2Nation.patches (Sovereign
Nations): both change the bytes at 0064E03F. Only one of the two mods can run; ...`.

## Several mods: the rules

* **Exe patches must not overlap.** Two mods that patch the same instruction, or whose sections
  overlap, cannot run together: the second in file-name order is left out. Every section defaults
  to the address right after the exe's last section (`0x9F7000`), so a mod with a code cave that
  wants to run next to another such mod picks its own address (`install.json` `section_va`, for
  example `0x00A10000`; the export proves the data still matches the file build).
* **A mod that needs no exe patch has none** (`install.json` `"patches": []`): its patch data holds
  the trigger and the DLL, and the loader loads the DLL. That is the template's case. Do not export
  `template/patches/load_mod_dll.py` for players: its entry-point detour is only for a modder's test
  exe, and it overlaps every other mod that loads a DLL that way (Sovereign Nations' P06).
* **Runtime hooks chain.** The DLLs' own hooks (`hook.c`, for example the per-frame hook on
  `A2dLib!CWalhallaClient::InputProcessor` that both the template and Sovereign Nations use) find
  another mod's 5-byte `jmp` where the original code should be and chain behind it: both handlers
  run, then the original code. Byte patches of a DLL (`patch_bytes`) still refuse different bytes.
* **One loader file.** Every mod's setup carries the loader; Install keeps the newest
  (`PR2-LOADER <n>` in the file), Uninstall leaves it while another mod's `<id>.install.json` lists
  it, and the last Uninstall removes it (`tools/release/mod_setup.py`).
* **Lua names and loose files are shared**: prefix your Lua functions; two mods cannot both install
  the same loose file with different contents (the setup refuses the second).

## Patch data format 2 (text, one record per line)

```
PR2-PATCHDATA 2                            magic and format version (first line)
# ...                                      comments
name <the mod's name>                      for the log (the rest of the line)
mod <version>                              the mod build the data was exported from
base <sha256>                              the DRM-free PR2.exe image the patches were made against
hook <slot VA> <return VA> <dll> <name>    the import slot to hook and the CRT call that triggers
section <VA> <size> <sha256>               the patch section: address, size (optional)
data <offset> <hex>                        section contents (32 bytes per line)
patch <VA> <original hex> <new hex> ; ...  one image patch (up to 32 bytes; optional)
reloc S <offset> <+|->                     a 4-byte field in the section that moves with it
reloc P <patch index> <offset> <+|->       a 4-byte field in a patch's new bytes that moves with it
dll <name>                                 the DLL to load after patching (a file next to the exe)
end <sha256>                               sha256 of every line before this one ("\n" line ends)
```

`+` fields gain the section's displacement (absolute addresses or rel32 into the section), `-`
fields lose it (rel32 from the section back into the image).

Format 1 (`PR2NATION-PATCHDATA 1`, Sovereign Nations' releases before the kit had a loader) is the
same without `name`, and needs a section and at least one patch. The loader still reads it.

Limits: 16 mods, 512 patches and 1024 relocations per file, a section of at most 1 MB, file names
of at most 59 characters.

## Tests

* `python tools/release/test_setup.py`: the setup's rules for several mods (shared loader, newest
  wins, hand-over of a backed-up file, conflicts, migration) and the overlap rule, on fake folders.
* In game: `docs/techniques/09-releasing-a-mod.md` ("Checked end to end").
