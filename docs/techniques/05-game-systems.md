# Game systems already mapped

This is the engine map built for Sovereign Nations, pulled together from its research notes
(`research/*.md` in the Sovereign Nations repository) and its internal feasibility notes. All
addresses are for the 1.1.2.3 image (ImageBase `0x400000`). **SOLID** = verified against the
exe bytes (most of these were facts in the project's fact checker, 197/197 on the pinned exe;
that file is not published, put the ones you rely on into your own facts file for
`tools\exe\check_sites.py`) or observed in the running game. **GUESS** = inference, not
tested. The research note named in brackets (in Sovereign Nations' `research/`) has the
evidence and more detail; `FEASIBILITY` was an internal note and is not published.

Two general rules first:

- Engine classes are German-named: `CStadt` town, `CStadtLager` town store,
  `CKonvoi` convoy, `CNation`, `CPlayer`. Exception strings in the exe (and in Reko's
  `program.c`) name classes and methods (`CNation::...`, `CEventPeace::...`).
- Most methods are `__thiscall`. Towns, players, convoys are reached through handles
  (below), never through stable pointers you keep across frames.

## 1. Handles, pools and the world (SOLID) [economy_data]

A handle is `index (16 bits) | type (5 bits) | serial (11 bits)`. Types: 1 town, 2 convoy,
3 ship, 8 captain, 9 player.

| Pool | Base | Element | Resolver |
|---|---|---|---|
| Towns | `0x702068` (pointer array; links `0x70206C`, cap `0x702070`, head `0x702074`) | `CStadt*` | `0x5C2200`; safe lookup `0x41F620` (`ecx = 0x702068`, NULL if invalid) |
| Players | `0x701EC0` | inline, stride `0x94` | `0x5C29E0`; human player's handle at `0x701ED0` |
| Convoys | `0x701EE8` | pointer | `0x5C2360` |
| Ships | `0x703CD4` (count `0x703CDC`) | inline, stride `0x28` | |

The World is a static singleton at `0x702140`. Date: tick counter `0x702C14` (256 ticks
per day), day `0x702C1C`, month `0x702C1D`, year `0x702C1A`; `World::Tick` `0x5D2790`.
Town index order everywhere in the mod = the order of `Data\staedte.ini` (0..59).

## 2. Nations and towns [FEASIBILITY, player_cnation_design, conquest_transfer]

- Four colonial powers, ids 0–3: England, Frankreich, Holland, Spanien (names `char*[4]`
  at `0x6E41E0`). Lua constants `PIRATEN_NATION = 4` (pirates have no `CNation`) and
  `SPIELER_NATION = 5`. SOLID.
- `CNation* g_nations[4]` at `0x702B14` (= World + `0x9D4`). 109 absolute code references,
  all bounded by `n < 4`; for `n >= 4` the guarded lookups (`GetNation` `0x417DC0`) return
  **NULL**. The table cannot grow in place: World + `0x9E4` follows directly. SOLID.
- `CNation` is `0x170` bytes: ctor `0x5BBC70(idx)`, `rel[4]` at `+0` (low 7 bits, 1 = war),
  military convoy list `+8`, town list `+0x14` (node `{next, prev, handle@+8}`), own index
  `+0x18`, viceroy candidates `+0x144`, annexation timer `+0x154`. `IsAtWar` `0x5BA0A0`.
  Saved as exactly four blobs by `World::Save` `0x5D1120`, followed by a `'{'` marker
  that `World::Load` `0x5D2250` requires. Offsets SOLID, some field meanings GUESS.
- **Town sovereignty.** Nation byte at `CStadt+0x390`, display byte at `+0x391` (the
  `CStadtLager` sub-object is `CStadt+0x38`, so these are Lager `+0x358/+0x359`). Lua
  `Town_GetNation` returns the display byte when the nation is ≥ 4. Every power-owned
  town has display == nation. SOLID.
- **The only setter** is `CStadtLager::AddToNation(n, force)` `0x5C9DA0` (thiscall,
  `ret 8`), with exactly 4 callers. `force = 0`: n ≥ 4 is silently ignored for an owned
  town; otherwise moves the town between the nations' town lists and sets both bytes.
  `force = 1`: acts only if the byte is already ≥ 4, sets display = n % 4, nation = 5,
  without touching any list. SOLID.
- Town flags `CStadt+0x40`: bit 3 plague, bit 4 starvation, bit 25 strike, bits 21/22
  (`0x600000`) = vanilla player town ("Your Own Town"); `CStadt+0x44`: bit 0 governor
  seat, `0x2000` viceroy, `0x8000` "hostile to player". Other fields: population `+0x38A`,
  soldiers `+0x38E`, stock `+0x60[20]`, buildings `+0x9184` (stride `0x14`), permit
  `+0x91AC/+0x91B0`. SOLID.

Traps (SOLID):

- Flipping the nation byte directly leaves the old owner's `+0x14` town list stale, and
  that list is saved. Move a town properly: detach with `0x636630`, then `AddToNation`,
  then notify with `0x624F50` (Sovereign Nations' `eng_town_to_nation`).
- Lua `Town_SetNation` uses `force = 0`, so **Lua cannot create a nation-5 town**.
- Do not set bit 22 or call `0x624D30` on a converted town: that code assumes a store
  extension the town does not have.
- Never call `CPlayer::SetNation` `0x5A9DF0` with 5 (it writes `0xFF`).
- A vanilla exe crashes on a save that contains a nation-5 town.

## 3. The nation-5 slot [vanilla_nation5_path, null_nation_census, nation5_index_safety]

Vanilla already has nation 5: the viceroy's "Your Own Town" offer (`0x447520` →
`0x61C7A0` → `0x61BDE0` → `AddToNation(n, 1)`, gated by the annex counter ≥ 2). New games
never create one (RUNTIME). The engine half-supports it: `GetReputation(5) = 100`, the
flag table's slot 5 is `-userflag-`, the map widget maps 5 to 4, and 33 sites substitute
the display byte for nations ≥ 4. SOLID.

Building the player nation on this slot ("option C") instead of adding a fifth `CNation`
needed only the crash fixes P01–P05 and P07 (`03-the-exe.md`): a census found 60
NULL-nation dereferences, 12 of them reachable from a nation-5 town, plus 6 unguarded
table reads. SOLID. 48 further sites stay latent: they are reachable only if a 5 leaks
into a convoy, NPC or event (GUESS that this never happens in normal play).

For fleets, Sovereign Nations later added a real `CNation` for index 5 without touching
the save layout: a static `CNation` built with the game's ctor plus a private
`g_nations6[6]` mirror, and per-site "opt-ins" that change `cmp r,4` to `cmp r,6` and the
table address to the mirror. Only ternary-shaped guard sites are safe to opt in; the
mirror must be refreshed at the world writer hooks (`0x5D12EB`, `0x5D14F5`, `0x5D23C3`),
not per frame (stale pointers right after a load). SOLID, in Sovereign Nations' `dll/navy.c`.

## 4. War and hostility [hostility_map]

- **Power vs power** is `CNation::rel[]`, read by `IsAtWar` `0x5BA0A0` (18 callers;
  vanilla returns TRUE for any `other >= 4`). `World::SetRelation` `0x5D0FA0` posts the
  war-begin/end messages; `CEventPeace` (Execute `0x5F56E0`) toggles 6 hard-wired pairs
  (table `0x6AAFDC`) at random intervals; Lua `Global_SetRelations` `0x41D850`. SOLID.
- **Power vs player** is **reputation only**: `CPlayer+0x54 + 4*power`, 16.16 fixed point,
  0..100, hostile below 25. `GetReputation` `0x5D2AC0`, `GetReputationLevel` `0x5D2C10`
  (55 callers), `IsRepAtLeast25` `0x5D2D90` (13 callers). SOLID.
- Sovereign Nations: `player_rel u8[4]` at `0x9F7000` (P01's first label). P01 makes
  `IsAtWar(power, 5)` read it, P04 makes reputation read 0 while at war. RUNTIME: at war
  with Spain, Spain was hostile in 6/6 decisions and every other power stayed at peace.

Traps (SOLID): P04 treats any non-zero byte as war; flipping the table posts no message,
plays no video and does not update the town flag `0x8000`; reputation jumps from 100
straight to 0 under P04.

## 5. Military AI and fleets [military_ai, military_combat, navy_operations]

Convoy (`CKonvoi`) fields, SOLID unless marked: attack mask `+0x48` (bits 0–3 powers,
4 pirates, 5 player ships, 6 siege), state `+0x90` (0 at sea, 2 docked, 3 wait,
4 anchored, 9 battle/siege), ship count `+0x95`, town `+0x9C`, handle `+0xA8`, type `+0xB4`
(1 player, 2 trader, 3 pirate, 4 military, 5 transport, 6 treasure, 8 mission, 9 war
mission), nation `+0xB8`, mission fields `+0x170..+0x183`, MTask fields `+0x1B8..+0x1C8`.

Key functions (SOLID): military tick `0x5B5CF0`, decision `0x5B1DF0`, attack-mask builder
`0x63C280`, patrol/target/home pickers `0x63B840`/`0x63B3A0`/`0x63C780`, `NewSeaBattle`
`0x5FEF80`, `AssignWarMission` `0x5B4870(k, target, count)`, `DeleteConvoy` `0x635600`.
`NewKonvoiMilitary` `0x635270` takes its nation from the **town** byte; Lua
`Global_CreateMilitaryConvoy` `0x426810` ignores its nation argument when given a town.

Traps (SOLID unless marked):

- Without the opt-ins, `SetType` silently turns a nation-5 warship into a pirate.
- A type-4 fleet never besieges: a sortie only anchors and turns home. Real assaults are
  type 9.
- An MTask given to a docked fleet traps it in port (RUNTIME).
- Set `+0x7C |= 0x10000` before `AssignWarMission`, or the captain is renamed.
- **Open contradiction**: the meaning of convoy `+0x1C` (GUESS "hunter flag" / "busy" in
  two notes; SOLID "visible now" in `sea_map_sight.md`, which is runtime-backed: trust
  that one). Also, `navy_operations.md` says nation-5 towns carry bit 22; P07 and
  `garrison.md` say Sovereign Nations' towns do not (they do not).
- RUNTIME (`NAVY_SPIKE.md`): in the first spike the fleet's attack mask stayed 0 after
  declaring war on Spain. The finished navy (`navy.c`) drives war missions itself; read
  how it does that before relying on vanilla mask building for nation 5.

## 6. Conquest [conquest_trigger, conquest_transfer, power_siege_nation, garrison]

- The only real ownership transfer in vanilla is `CNation::AnnexTown` `0x5BC310`, reached
  only from the **player's** battles (coast `0x6014EB`, harbour `0x605DD7`) and only with a
  viceroy annexation order. **The AI never changes a town's owner**: `AttackerWon`
  `0x602500` only plunders. SOLID.
- Player attack chain (SOLID): convoy `+0x48 |= 0x40` → arrival `0x5B4B20` →
  `NewTownAttack` `0x603030` creates a **siege** object → question type 2 (land / sea /
  cancel) → `0x601720` creates a `CCoastBattle` (sea side: `CHarbourBattle`). The question
  store is a singleton at `*0x6E7368`; answering a stale question throws.
- Sovereign Nations hooks this chain in `war.c` (refuse attacks on own towns at
  `0x60309F` and `0x5B4B2D`, take over the result at `0x6011BE`, `AttackerWon` at
  `0x602500`) and asks Annex/Withdraw with a mission popup. RUNTIME: a real land assault
  took Coro from Spain and attacking the own capital was refused.
- Powers attacking the nation: patch the immediate at `0x5B4892` (`cmp bl,4` → 6) and call
  `AssignWarMission(k, 5, n)`. The siege resolves instantly (land if sailors > 5 ×
  garrison; bombardment if cannons > 20 × towers). SOLID static, no runtime test.
- Garrisons: nation-5 towns without bit 22 already recruit soldiers and build towers in the
  daily tick `0x5C6D70` (SOLID static; runtime unconfirmed).

## 7. Officials and building permits [officials, building_permit]

- Officials (treasurer, commander, building master) are NPCs held by the World
  (`+0xAE4`, `+0xAF8`, `+0xAE8`). Their nation is set from the town's nation byte, or the
  display byte when ≥ 4, so a member town's officials answer as the former host power.
  Sovereign Nations hooks `GetReputationLevel` `0x5D2C10` with `hook_install_ret` and, for
  16 known return addresses, returns tier 3 when the official's town belongs to the
  nation. SOLID + RUNTIME (33/33).
- Permits: one record per town, `CStadt+0x91B0` granted byte; grant `0x6211A0`, sell
  `0x61B770`, check `0x61B4C0` (3 = may build); Lua `Player_HasLicence` `0x4233F0`. The
  vanilla grant posts a message and chronicle entry and the sale pays the player, so the
  mod writes the byte directly and remembers the previous value. SOLID + RUNTIME. The
  master builder's buy-back price of 0 (hook `0x410A53`) is GUESS (untested).

## 8. Economy [economy_data]

- There is **no** vanilla tax, tribute or toll mechanic. SOLID.
- Gold is `CPlayer+0x3C`, changed **only** through `0x60E570(ecx = &gold, amount,
  ledgerCategory)` (`ret 8`): it clamps to ±2,000,000,000 and books the amount in the
  13 × 11 balance-sheet ledger at `0x703758`. Category 9 = scripts (what Lua
  `Player_AlterGold` `0x41D000` uses). If `CPlayer+0x40` is 0 the call does nothing.
  Never write `+0x3C` directly. SOLID. (`template/dll/player.c` uses exactly this.)
- Living standard: `UpdateLQ` `0x624FA0`; the actual level drifts back toward the
  expected one, so penalties must be re-applied every tick. Unrest levers: `SetStrike`
  `0x624460`, `AlterALQ` `0x624790`. SOLID.
- Messages to the ticker: `PostMessage` `0x5B82A0`, free text `AddText` `0x5B7FF0`. SOLID.
- GUESS: the meaning of `CStadt+0xB0[20]` (industrial demand) and ledger categories ≥ `0xD`.

## 9. Flags and logos [flags, userflag]

- Town icon table `0x6E12EC`: `nation00..03`, `nation_piraten`, and `-userflag-` at
  slot 5 (the player's company logo, `%APPDATA%\Ascaron Entertainment\Port Royale 2\Run\0.bmp`,
  a 96 × 64 24-bit BMP copied at Start Game and embedded in saves). Only the `CFlag`
  setter `0x4C34A0` understands the sentinel; three UI sites pass it to another loader
  that reports success on failure (fixed by P02). SOLID.
- `WalExtPR2.dll` builds `Run\Player<k>.aim` sail textures from `Run\<k>.bmp` for k = 0..7.
  Sovereign Nations keeps the nation's flag apart from the player's logo as `Run\7.bmp`,
  redirecting about a dozen `"0.bmp"` string pushes and the sail index (in `flag.c`).
  RUNTIME. Limits: 3D sea battles still show the player sail; town flagpoles are per-host
  art loaded at boot.

## 10. Sea-map sight [sea_map_sight]

Sea-map objects: `+0x1C` visible now, `+0x1D` visible last pass; the visibility pass
`0x62C3A0` reveals (radius 100, `0x62C760`) only around the local player's convoys.
Minimap dots `0x4803D0` (flag `0x2000` = cyan). View controller
`*(*0x6E58E4 + 0xF0)`, `CenterOnConvoy` `0x516E30`. The mod reveals around its fleets
(hook `0x62C3E7`) and colours them (hook `0x480504`). SOLID + RUNTIME (SEE-01..09).
Autonomous national trade convoys are not possible (a convoy's player cannot be nation 5).

## 11. The UI: screens, tabs, panels, buttons [screen_format, tab_window, text_markup]

- A **`.screen`** is an A2dLib `A2DFILE` (version 14 or 13) tree of drawables; format and
  tool in `01-game-files.md`. To see which widget classes and names the vanilla screens use,
  run `tools\files\screen_dump.py dump` or `grep` over your extracted `arcd\scripts\` (the
  project's catalogue of all 254 screens is not published: it lists every vanilla control).
- Objects: `CGameMenu* gm = *(*(void**)0x6E58E4 + 0xEC)`. The Log window: `OpenLog`
  `0x507ED0`, `CloseLog` `0x5007C0`, `CloseAllMenus` `0x507600`, `SetMenuOpen` `0x5001D0`.
  SOLID.
- **A tabbed window of your own** (what Sovereign Nations' panel does, SOLID because it
  runs): allocate a `CTabControl` (0x1A4 bytes, ctor `0x4DD080`) with A2dLib's operator
  new, create it from `TAB_CONTROL_800x600.screen`, add pages (`AddTab` `0x4DD660`), add the
  close button, position it like the Log (`PositionWindow` `0x4D37A0`). Pages are
  `CBaseControl` (ctor `0x4CF150`) or `CListPage` (ctor `0x4D4F10`) objects whose vtable
  you **copy** into the DLL and override (create, refresh, fill). Call
  `UpdatePages(tab, 0)` yourself every frame; `CGameMenu` only updates its own windows.
- **Buttons into the DLL**: connect a C handler `void __thiscall fn(void *obj, int key)` to
  a button's click signal (`button + 0x9C`) through `0x4AFCD0`/`0x4B0100`/`0x517640`
  (`gui.c` `gui_on_click`); `obj` must be a `CBaseControl`-derived object. Icons with
  `SetIcon` `0x4C4C50` (icon table `0x6E77F0`). Sovereign Nations adds its top-bar button
  by overriding `TOPMENUBAR_800x600.screen` with one extra button.
- Text: `CTextBox` text is a wide string at `+0x9C`, `CTextBoxEx` at `+0xA4` (then
  re-layout with `FuncCreateSubDrawables`). Only `CTextBoxEx` parses markup.

UI traps (SOLID unless marked):

- Control names are **global** (the first match wins): prefix yours and keep one tabbed
  window of your own open at a time.
- Never destroy a window from inside its click handler; set a flag and do it next frame
  (destroying it in the handler: GUESS crash).
- Game hotkeys fire while an edit box has focus; Sovereign Nations hooks `SetKeyState`
  `0x52B840` to swallow them.
- Loading a missing `.screen` or an unregistered class name **throws**.
- STLport strings passed by value must be built with the game's allocator (IAT
  `0x67F9B8`), or the heap is corrupted.

## 12. Game flow [game_flow_entry_points, save_hooks]

Lifecycle hook sites used by Sovereign Nations (SOLID): world reset `0x5809B3` (new game,
load start, leave to menu, quit), new game done `0x580E5E`, load done `0x5815CF`, daily
tick `0x5D286C`, save and load of mod data `0x5D11A6`/`0x5D23C3` (`06-saving-mod-state.md`).
Speed `SetGameSpeed` `0x57F550`, pause/unpause `0x57EDF0`/`0x57EFD0`, QuickSave
`0x57EE90`, `SaveGame` `0x580E70`; "simulation running" byte at `0x700500 + 0xE44`. SOLID.
