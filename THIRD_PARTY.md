# Third-party code, tools and assets

This kit contains none of Port Royale 2's files. The only third-party material in it is in
`reko-patches/`: our changes to Reko as patch files (which carry a few lines of Reko's own
source as context) and the GPL v2 text, all under Reko's licence. Everything else it refers to
below keeps its own licence, and you get it from its own source.

| Item | Used for | Licence | In this repo? | Notes |
|---|---|---|---|---|
| Port Royale 2 (Ascaron Entertainment) | the game being modded | commercial | **no** | you need your own legal copy; no exe, archive, save or extracted file is in this repo |
| Lua 4.0 / the game's `lua.dll` | the game's scripting engine | Lua 4.0's own licence (permissive); `lua.dll` is part of the game | no | the kit only calls the game's copy |
| dgVoodoo2 (Dege) | Direct3D 8 wrapper for PCs where DX8 hangs (Intel D3D9on12) | freeware; its ReadmeGeneral §1 allows shipping unmodified dgVoodoo files with a game mod | no | a mod's release may add the unmodified files from an official release |
| Steamless (atom0s) | removing SteamStub from a modder's **own** exe for static analysis | CC BY-NC-ND 4.0 | **no, never** | mentioned in the docs only; players never need it |
| Reko (uxmal/reko) | decompiler views | GPL-2.0-or-later | only our patches, in `reko-patches/` (GPL-2.0-or-later, with `COPYING`) | build Reko 0.12.2 from upstream and apply them; `tools/reko/` drives it |
| Ghidra (NSA) | decompiler views | Apache-2.0 | no | the scripts in `tools/ghidra/` are ours (MIT) and use Ghidra's script API |
| RetDec (Avast) | early decompiler sweep | MIT | no | the kit has no RetDec driver |
| capstone, pefile, Pillow (Python packages) | the tools | BSD-3-Clause, MIT, MIT-CMU (HPND) | no | install with pip |
| MSYS2 / MinGW-w64 GCC | building the DLLs | GPL with the GCC runtime library exception | no | statically linked libgcc is covered by the runtime exception |
| VC++ Toolkit 2003 (Microsoft) | byte-exact source reconstruction (cl 13.10) | Microsoft EULA | **no** | not redistributable |

The template's vendored DLL files (`template/dll/hook.c`, `lua.c`, `log.c`) come from
Sovereign Nations and are under this kit's MIT licence.
