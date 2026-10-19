# Playing a mod (for players)

This page uses Sovereign Nations as the example; other mods built with the kit install the
same way.

## You need

- Port Royale 2 from Steam, installed (version 1.1.2.3, the one Steam sells).
- Windows 10 or 11.
- The mod's release zip (`SovereignNations_v<date>-<build>.zip`).

## Install

1. Extract the zip to any folder.
2. Double-click **Sovereign Nations Setup**. It finds your Steam copy by itself (from the
   registry and Steam's library list); if not, click **Browse** and pick the folder that
   contains `PR2.exe`.
3. Click **Install / Update**. The setup checks that your `PR2.exe` is the build the mod
   was made for and refuses anything else with a clear message.
4. Start Port Royale 2 **from Steam**, as always.

## What the setup changes in your game folder

It adds a small loader (`version.dll`), the mod (`PR2Nation.dll`), its patch data
(`PR2Nation.patches`) and the mod's data files (a few `scripts\*.screen` files,
`lua\config\default\include.ini`, `nation_flags\`). It never changes `PR2.exe` or your
saves. If it has to replace a file, it keeps the original in `_SovereignNations_backup\`.
Everything it did is listed in `SovereignNations.install.json`.

When the game starts, the loader applies the mod in memory. If the game is a build the mod
does not know (for example after a Steam update), it changes nothing and the game starts
without the mod.

## Update, repair, uninstall

- **Update**: run the new version's setup and click **Install / Update**.
- **Repair**: if a mod file was deleted or changed, click **Repair**.
- **Uninstall**: click **Uninstall**. The folder is put back exactly as it was; saves stay.

## Black screen or hang on start

Some PCs (notably Intel graphics, where Windows runs Direct3D 9 through D3D9on12) hang in the
game's Direct3D 8 renderer. Run the setup again, tick **Also install dgVoodoo2** and click
**Install / Update** (offered when the release carries dgVoodoo2).

## Other mods

The setup asks before installing next to other mods' loose files (for example the 4x towns
mod's `towns\*.tow`), because the mod is tested with the original files.

## Saves

Saves made with a nation founded carry the mod's data: keep the mod installed while you play
them.

## Reporting a problem

Send `PR2Loader.log` (what the loader did) and `PR2Nation.log` (the mod) from the game folder,
right after the problem: the logs start fresh at every launch.
