# Saving and loading the mod's state

A mod that keeps its own state (Sovereign Nations keeps the whole nation: name,
government, towns' loyalty and tax, treasury, wars, fleet orders) has to store it in the
save game, or it is lost on every load. This is how Sovereign Nations does it
(Sovereign Nations' `dll/persist.c`; research in its `research/save_hooks.md`).
All sites are SOLID (static, and in daily use by the mod's scenario saves).

## 1. How the game saves

- Saves are `Documents\Ascaron Entertainment\Port Royale 2\Save\*.pr2` (about 360 KB,
  compressed/encoded; the codec is not needed for this technique).
- The serializer writes into a memory stream `{u8 *base; u8 *cur; u32 cap}` (inlined, no
  vtable). `Session::SaveGame` `0x580E70` runs the serializer `0x5807C0` **twice**: once
  with `{0, 0, 0}` to measure (`cap == 0`), then again into a buffer of that size.
- Save version `0x441` (at `0x5807D2`); the loader accepts `>= 0x424`. **Do not bump it.**
- On load, reading past the end throws `CLoadExeption`; the catch handlers
  (`0x581907`, `0x581A55`) wipe the world and return to the menu.

## 2. Where the mod's data goes

`World::Save` `0x5D1120` writes the four `CNation` blobs and then a `'{'` byte, and
`World::Load` `0x5D2250` reads four blobs and then insists on that `'{'`. Sovereign
Nations puts its blocks **between** the last nation and the `'{'`:

| Hook | VA | Original bytes | What the DLL does |
|---|---|---|---|
| World::Save, after the 4 nations | `0x5D11A6` | `8B 46 08 85 C0` | write the blocks into the stream in `esi` |
| World::Load, after the 4 nations | `0x5D23C3` | `8B 46 04 8B 1E` | read the blocks and advance the stream's `cur` (the displaced code re-reads it) |

Blocks are written only when there is something to save (a nation has been founded), so
a save without a nation is byte-identical to a vanilla save and loads in the unmodded
game. Format:

```
u32 'PNAT' (0x54414E50) | u32 len | NationState bytes
u32 'PFLT' (0x544C4650) | u32 len (<= 96)  | per-fleet orders           (optional)
u32 'PNCV' (0x56434E50) | u32 len (<= 128) | national convoy handles    (optional)
```

Rules that make it safe:

1. **Same byte count in both passes.** Compute everything before writing; write nothing
   when `cap == 0` (the measuring pass) except advancing `cur`. Vanilla writes have no
   bounds check, so a fill pass longer than the measuring pass overruns the heap. The
   DLL's `stream_write` refuses to overrun and logs `SAVE: stream full`.
2. **Unknown data must look like vanilla to the loader.** On load, if the next byte is
   `'{'` there is no mod data. If the magic is unknown or the length is impossible, leave
   `cur` alone: vanilla then throws on `'P' != '{'` exactly as it would without the mod.
3. **Damaged data must not crash.** If the magic is right but the block fails to
   deserialize, skip it, reset the mod state and tell the player the nation data was
   damaged or incompatible.

## 3. Keeping older saves loadable

`NationState` starts with `{u32 magic; u16 version; u16 size; ...}` and carries FNV-1a
checksums. The rule is **append only**: new fields go at the end, with their own
checksum, and the version goes up.

| Version | Size | Added |
|---|---|---|
| 1 | 976 | the nation core |
| 2 | 1064 | navy (`checksum2`) |
| 3 | `sizeof(NationState)` | national accounts by category (`ledger`, `checksum3`) |

Loading an older version zeroes the parts it does not have (v1: navy and ledger; v2:
ledger). Every field is sanity-checked after loading (flags are 0/1, names are
NUL-terminated, tariffs are one of the allowed values, and so on). `_Static_assert`s pin
the offsets so a struct change cannot silently move a field. Unit tests
(Sovereign Nations' `dll/tests/run_tests.py`, 695 checks; `695 passed, 0 failed` when these
docs were written) cover the
serialize/deserialize round trips.

## 4. Lifecycle

Mod state must be reset when the world is: the hook at `0x5809B3` (world reset) runs on
New Game, at the start of a load, when leaving to the menu and twice on quit. New Game
done is `0x580E5E`; Load done (success only) is `0x5815CF`. Note that Load done runs
**before** vanilla restores the save's company logo, so anything that touches the logo
must wait a few frames (Sovereign Nations waits 3).

## 5. Alternatives (documented, not used)

- **Trailing blob** (save site `0x580996`, load site `0x5814D4`): data after everything
  vanilla writes. SOLID that vanilla ignores trailing bytes, so even saves *with* mod data
  would load in the unmodded game (without the mod's state). Recommended in the research
  note, not used by Sovereign Nations.
- **`GLOBALS` in Lua**: the engine saves the mission `GLOBALS` table for free. Fine for a
  few numbers and simple strings (no quotes, backslashes or newlines; see `02-lua.md`),
  not for binary state.

## 6. Compatibility with the unmodded game

A save that contains Sovereign Nations' blocks makes the vanilla exe throw at `0x5D240B`
(SOLID). That it then wipes the world gracefully instead of crashing is GUESS. A save with
a nation-5 town crashes the vanilla exe regardless of the blocks. Tell players.
