# Loose data files

Put files here at the same relative path they have inside the game's `.cpr` archives
(for example `scripts\MY_WINDOW_800x600.screen`, `lua\config\default\include.ini`). The
game loads a loose file in the game folder in place of the archived one
(`../../docs/techniques/01-game-files.md`).

The template ships no data files. Be careful with files other mods also override,
`lua\config\default\include.ini` and `scripts\TOPMENUBAR_800x600.screen` above all: a
second copy replaces the first, so the two mods' changes have to be merged.
