# Game files: archives, the loose-file override, towns, screens, text

Everything except the exe and the engine DLLs lives in four `.cpr` archives in the game
folder. Data mods need no DRM work and usually no repacking: a loose file wins over the
archived one.

## 1. The loose-file override (SOLID)

Put a file in the game folder at the **same relative path** it has inside an archive, and
the game loads it instead of the archived copy. Verified in game with
`scripts\Credits.lua` (credits scroll speed changed), Sovereign Nations'
`lua\config\default\include.ini` and its `scripts\*.screen` files, and the 4x-towns mod's
`towns\*.tow`.

How it works (static, Sovereign Nations' `research/flags.md` §3): at start-up PR2
(`0x4042E0`) enables "disc search first". Lookup order is

**loose file > `pr2_patch.cpr` (optional) > `pr2_loca.cpr` > `pr2_arcs.cpr` > `pr2_arct.cpr` > `pr2_arcd.cpr`**

- paths are relative to the current directory (the game folder); keys are case-folded and
  `/` equals `\`;
- files are looked up again on every load (no cache), so you can edit and reload;
- a **new** file name needs no archive entry;
- UI screens are found by bare name: `CA2dFile::Open(name)`, else `"scripts\\" + name`
  (A2dLib `0x10040DD0`), so `<game>\scripts\MY_WINDOW_800x600.screen` loads as
  `MY_WINDOW_800x600.screen`.

Traps:

- Texture atlases cannot be overridden one part at a time: replace the whole
  `TexPage_*.aim`, or the `.tex` and `Partmap.007` together. An unknown part name throws
  (A2dLib `0x10045544`).
- An existing **save** keeps its towns' structures inside the `.pr2`, so editing a `.tow`
  does not move an existing save's town (REPORTED).
- Loose files are global: two mods that override the same file overwrite each other.
- Never test in an install that still has another mod's loose files in it.

## 2. The `.cpr` archive format (`ASCARON_ARCHIVE V0.9`)

Little-endian. Verified on all four archives of the 1.1.2.3 game by walking every segment
to the end of the file, and by a byte-exact rebuild of all four from their extracted files
(`cpr_pack.py --like ... --check`, below).

```
[0 .. 32)    magic "ASCARON_ARCHIVE V0.9", NUL padded
segment header at H (H = 32 for the first segment):
  u32 data_start_rel   = 0x1000   (relative to H)
  u32 dir_len          = entry bytes + 16
  u32 count
  u32 data_size
directory, `count` entries:
  u32 offset (absolute in the file), u32 size, u32 flag (= 1), name + '\0' (backslash path)
data: [H + 0x1000, H + 0x1000 + data_size), each file 32-byte aligned, end padded to 32
the next segment's 16-byte header (no magic) follows right after the data
```

The directory of one segment must fit in `0x1000 - 16` bytes, which is why the big
archives are split into segments of about 60–100 files. Duplicate names are allowed
(`pr2_loca.cpr` lists `scripts\ERFOLGSPAGE_800x600.screen` twice, identical bytes).

| Archive | Segments | Files | Contents |
|---|---|---|---|
| `pr2_arcs.cpr` | 1 | 91 | `Data\*.ini` (`staedte.ini` towns, `waren.ini` goods, `BattleConst.ini`, …), `lua\config\default\{include,mstart}.ini`, `lua\config\scenario\…`, `lua\data\missions.bin` (Lua bytecode), `NavData\*.dat` |
| `pr2_loca.cpr` | 1 | 33 | fonts (`.TTF` and binary font `.res`), `scripts\Credits.lua`, 6 localised `.screen` files that override the `arcd` ones |
| `pr2_arct.cpr` | 20 | 1,983 | 3D and town data: `.aim` 779, `.let` 417, `.tow`/`.rest`/`.coa`/`.editor`/`.inf` 80 each, `.map` 79, `.lcr` 72, `.3do` 23 |
| `pr2_arcd.cpr` | 53 | 4,132 | `.wav` 256, `.screen` 254, `.anim` 841, `.cla`, `.xml`, `.tga`/`.bmp`, the text tables `strings\…\global.res` |

### Tools

```
python tools\files\cpr_extract.py <game>\pr2_arcs.cpr --map          # segment map
python tools\files\cpr_extract.py <game>\pr2_arcs.cpr -l             # list
python tools\files\cpr_extract.py <game>\pr2_arcs.cpr [-d <out>]     # extract (default _game\extracted\arcs)
python tools\files\cpr_pack.py <dir> <out.cpr> [--like <original.cpr>]
python tools\files\cpr_pack.py <dir> --like <original.cpr> --check   # rebuild in memory, compare
```

Both need your own archives (a pristine copy of the game folder, `getting-started.md` step 4);
run them on a **copy**, not inside your Steam folder. A duplicate name is extracted as
`name__seg1.ext`. `cpr_pack.py --like` takes the directory order, the segments and the
duplicates from the original archive; files the original does not list go into new segments.
Without `--like` the files are sorted and fill segments greedily. Checked: all four archives
extracted and rebuilt with `--like ... --check`, each **identical** to the original byte for
byte (`arcs` 1 segment, `loca` 1, `arct` 20, `arcd` 53). Not checked: whether the game loads a
repacked archive with changed or added files. Because of the loose-file override you rarely
need to repack anything.

## 3. Towns: `.tow` (SOLID unless marked)

`towns\<Town>_Stadtplatz.tow` in `arct`, 80 files. The loader uses the format string
`towns\%s_Stadtplatz.tow` at `0x6ABDD8` (function `0x61A410`); the deserializer
`0x618FC0` switches on the version byte; the town object is `0x8DE0` bytes and mirrors the
file.

| Offset | Size | Content |
|---|---|---|
| `0` | 1 | version: `0x08` (78 files, 36,319 bytes), `0x07` (the two pirate hideouts, 36,318 bytes) |
| `1` | 28,928 | terrain grid, 1 byte per tile, **128 major rows × 226 minor columns**, index = `major * 0xE2 + minor` |
| `28929` | 3,616 | bitmap 1 (1 bit per tile), all zero in every shipped file |
| `32545` | 3,616 | bitmap 2, all zero |
| `36161` | 158 (157 in v7) | footer: per-town records and metadata, loaded at object `+0x8D41` |

The grid is **128 × 226, not 113 × 256** as an older note says: rendered at width 226 the
map is clean, at 113 the rows interlace. Tile codes by frequency: `0x02` land, `0x00`
water, `0x1E` sand/coast, then `0x05`, `0x04`, `0x0F`, … (meanings beyond these GUESS).
Converting coast to land gives at most about 1.7x building land (`make_maxland_tow.py`
is the simplest data mod of this kind). Sibling files per town:
`_Coastmap.map` (a 128 × 226 bitmap), `_Heightmap.dat`, `_SurfaceObjects.dat` (decor
objects), `.rest`, `.editor`, `.inf`, terrain `.aim` tiles.

Tools (they read the extracted `arct` archive, `getting-started.md` step 6):

```
python tools\files\tow_analyze.py                     # constant/variable byte map over all towns
python tools\files\tow_render.py Antigua [--widths 113 226]   # PNG into _build\tow_render\
python tools\files\make_maxland_tow.py -o <out> Antigua      # coast -> land, an example data mod
```

Checked: `tow_analyze.py` over the 80 towns (it flags the two version-7 files), `tow_render.py`
(width 226 is a clean map, 113 interlaces), `make_maxland_tow.py` on one town. The `.tow`
files these write are derived from the game's own files: keep them out of every repository.

Larger towns (4x) are a documented dead end for building space (`../dead-ends.md`).

## 4. UI layouts: `.screen` (SOLID)

`.screen` files are **binary**, not text: an A2dLib `A2DFILE` stream (28-byte header
`"A2DFILE\0"`, `u32 0x1C`, `u32 version` 14 or 13, 12 zero bytes) holding one tree of
drawables, normally a `CScreen` root. 254 in `arcd` `scripts\`, 6 localised ones in
`loca`. Each drawable is a fixed `SH_DWFAB` block (class at `+0x10`, **name** at `+0x50`,
an optional PR2 subclass `ext_class` at `+0x158`), an `SH_DWB` block (position `+0xC/+0x10`
relative to the parent, size `+0x14/+0x18`, flags `+0x1C`: bit 0 enabled, bit 1 visible;
tooltip id), a class-specific block, and its children. Coordinates are pixels of a
**1024 × 768** back buffer; the `_800x600` in the names is historical. Full field tables:
Sovereign Nations' `research/screen_format.md`.

`tools\files\screen_dump.py` reads, prints, edits and writes them:

```
python tools\files\screen_dump.py dump FILE.screen [--all]     # the control tree
python tools\files\screen_dump.py json FILE.screen OUT.json    # editable JSON
python tools\files\screen_dump.py build IN.json OUT.screen     # write a screen from (edited) JSON
python tools\files\screen_dump.py grep REGEX [DIR]             # find controls by name, class, art, text id
python tools\files\screen_dump.py verify [DIR_OR_FILES]        # parse->write and parse->json->write byte-identical
```

The default folder is the extracted `arcd` archive's `scripts\`. Checked: `verify` on all 254
vanilla screens (4,326 controls) and the 7 localised ones in `loca`, all byte-identical. It also
has a Python API (load, find, clone, set, rename children, set text id) that Sovereign Nations'
generator scripts (`art/make_*.py`) use to build new screens from vanilla ones on the player's
machine, so no vanilla screen has to be shipped.

Traps: an `ext_class` that is not registered **throws** (reuse registered names:
`CIconButton`, `CStaticEx`, `CDynamicImg`, `CFlag`, …; `screen_dump.py grep` over the vanilla
screens shows which classes the game itself uses);
control names are global at runtime, so prefix yours; register your string table before a
screen that uses its ids is loaded.

## 5. Text: `.res` string tables and markup (SOLID)

- **Format**: `u32 count`, then `count × {u32 hash; u32 offset (from byte 4); u32 nargs;
  u32 byteLen}`, then UTF-16LE text without terminators. Entries sorted by signed hash;
  hash -1 is rejected. The id hash is `h = (h * 0x71 + (signed char)c) % 999999991`.
- The UI table is `strings\Original\global\global.res` (German in this build); mission and
  NPC texts are in the exe's resource `BINARY #167` (XOR-chained UTF-16). The `.res` files
  in `loca\fonts\` are font descriptors, not text.
- **Adding your own table at runtime**: A2dLib's `CStringManager` (`g_pStringMgr`, exe IAT
  `0x67F4D0`): `Add(name, data, size)` copies the table (IAT `0x67F4CC`), `Remove(name)`
  (IAT `0x67F4C4`). `Add` with an existing name fails silently, so `Remove` first. Lookup
  walks every table, so make your ids unique (`IDM_MYMOD_*`). Sovereign Nations does this in
  its `dll/strings.c`; its `tools/check_ui_static.py` checks the format against the game's own
  tables. `screen_dump.res_hash(name)` computes the id hash.
- **Markup** is parsed only by `CTextBoxEx` (a plain `CTextBox` shows tags literally):
  `<n>` newline, `<l> <r> <c> <b>` alignment/bold, `<o:0xAARRGGBB>` colour, `<o>` reset,
  `<f:fonts/X.res>` font, `<g:FILE.screen>` inline graphic, tabs `<ts:X> <t>`, line height
  `<z:N>`, literal brackets `<al> <ar>`. Numbers are parsed like `%i`: `0x` works and **a
  leading 0 means octal**. Tag state is sticky; unknown tags vanish silently with their
  text. Game colours: body `0xFF131512`, heading brown `0xFF502814`, green `0xFF008000`, red
  `0xFFC00000`. Full list: Sovereign Nations' `research/text_markup.md`.
- Use ASCII in your strings (the mod's two conversion paths use different code pages).

## 6. Other data

- `Data\*.ini` in `arcs` (towns, goods, building goods, battle constants, start
  distribution) are plain text and override like any other file.
- `lua\data\missions.bin` is compiled Lua; leave it alone and add scripts through
  `include.ini` (`02-lua.md`).
- Flags and logos: Sovereign Nations' `research/flags.md` (the `.aim` image container and how
  to write one).
