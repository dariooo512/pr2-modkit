# Dead ends: tried, doesn't work, because …

Read this before you repeat an experiment. Each entry says what was tried, why it fails,
and what works instead. Sources: the project's post-mortem of its earlier attempts (an
internal note) and Sovereign Nations' research notes. Names in parentheses such as
`reverse-engineering-v2/` are folders of the original development workspace, which is not
published; the general tools that came out of those attempts are in this kit's `tools/`.

## Whole projects

| Tried | Why it doesn't work | Instead |
|---|---|---|
| **Rebuild all ~6,000 game functions as byte-identical C++** (`reverse-engineering-v2/`) | the rebuild became the goal; 3,175 functions matched byte for byte, about 29% of them typed-up assembly, and it never shipped a gameplay change on its own; 84 "fuzz-passed" functions crashed when patched in together | read only the functions on your call path; use reconstructions as reading aids |
| **Compile the whole game as C from Reko's output** (`reko/`, `manual program fix/`) | never compiled to one object file; stalled at thousands of type-reconstruction errors | keep Reko's `program.c` as a grep-able map (its exception strings name classes and methods) |
| **"Behavioural equivalence" from 1–2 recorded cases per function** (`behavioral-equiv/`) | thin evidence; a function its own README calls buggy was marked verified; global writes ignored | promote only with 10+ cases, and compare global state too |
| **Record every call during create-game and replay it** (`record-replay-tests/`) | works and found real bugs, but the corpus reached 76 GB and the comparison ignores writes to globals (nation state is global, `g_nations` at `0x702B14`) | use its **differential capture** (record an action against a baseline that does nothing) to find the code an action runs |
| **Find the "128-row cap" in the engine DLL** (`walhalla-re/`) | there is no such cap; the "crash" was Alt+F4 plus a harmless, caught font exception | classify faults first (`techniques/08-debugging.md`) |
| **4x larger towns** (`mods/town-4x-build-area/`) | a 4x grid generates and loads, but object placement rows are single bytes, so building tops out around 2x; the render layer belongs to a view class shared with the sea map; a use-after-free was hidden by switching off birds and fish | before building on a field, list every consumer (byte, word and dword widths) and write down the ceiling |
| **An early verdict that literal 4x towns were "infeasible by patching"** | done by patching a few hours later | state ceilings with evidence, then test them |
| **The isqrt "source reconstruction MVP"** | a leaf function with no structures and no observable in-game effect; proved the toolchain, not a technique for mods | pick targets on a gameplay path |

## The player's nation

| Tried | Why it doesn't work | Instead |
|---|---|---|
| **A real fifth `CNation`** (grow `g_nations`) | `g_nations[4]` and `[5]` overlap the next World member; relocating needs 124+ reference edits, ~100 guards, the `rel[4]` and peace-pair tables and a save-layout change, and every site still tests `n < 4` first | use vanilla's nation-5 slot plus a private mirror for the few systems that need a `CNation` (`techniques/05-game-systems.md` §3) |
| **Per-site detours for the `n < 4` guards** | the guards are 16-byte ternaries with rel8 jumps; a 5-byte detour cannot displace them | same-length immediate/disp32 edits ("opt-ins") |
| **Refresh the mirror table every frame** | right after a load it hands out freed pointers | refresh at the world's writer hooks |
| **Lua `Town_SetNation` to make a nation-5 town** | it calls `AddToNation(n, 0)`, which silently ignores n ≥ 4 | `AddToNation(n, 1)` after setting the byte, with the town-list bookkeeping |
| **Flip the nation byte directly** | the old owner's saved town list goes stale | detach, `AddToNation`, notify |
| **Set the vanilla player-town bits (`0x600000`) on a converted town** | still crashed; the bit assumes a store extension the town lacks | leave the bits alone; patch the few display sites (P02, P07) |
| **Reuse vanilla's "Your Own Town" function `0x61BDE0`** | it creates a new town, it does not convert one | the recipe above |
| **`CNation::AnnexTown` for nation 5** | needs a `CNation*` and a viceroy annexation order (targets are always < 4); the chronicle entry throws for ≥ 4 | the mod's own transfer after a won battle |
| **`CPlayer::SetNation(5)`** | writes `0xFF` | never; this is also why autonomous national trade convoys are impossible |
| **Patch the target picker or a battle site to use the display byte** | the host power's enemies or warships act instead | real per-site logic in the DLL |
| **Plain type-4 military fleets to besiege towns** | they never besiege: a sortie anchors once and turns home | type-9 war missions (`AssignWarMission`) |
| **Give a docked fleet a Circle task** | the harbour AI skips its cycle; the fleet never leaves port (observed) | give tasks at sea |
| **Hook the coast/harbour "execute" functions on the normal conquest path** | an earlier hook clears the annex bit, so they are never reached; sea assaults are impractical to automate | hook the offer at `0x6011BE` |
| **The first minimap hook (`0x47F2F1`)** | wrong minimap; the dot stayed white | `0x480504` |
| **Vanilla permit grant/sell functions** | post a message and a chronicle entry every time, walk all towns, and the sale pays the player | write the permit byte, remember the old value |
| **Find the caller of the viceroy's "Your Own Town" slot statically** | not found | a breakpoint, if you ever need it |

## Patching technique

| Tried | Why it doesn't work | Instead |
|---|---|---|
| **Detour sites with a rel8 branch into the first 5 bytes** (`0x6011B4`, `0x5D2D90`, `0x5C86AC`) | a branch would land inside the jump | move the detour a few instructions (`0x5D2D96`, `0x5C86A5`) |
| **`relink.py` in-place patching for a new feature** | in place only: no room for new code or new global state | `pe_patch.py` (one new section) plus a DLL |
| **Opting in the attack-mask block without its inner ternary** | crash (`0x5BBB00(NULL)`) | opt in both |
| **Calling `Global_CreatePopup` and friends from a bare `lua_dostring`** | null dereference: no current mission | call them from a mission callback |
| **Letting a Lua error happen in the Missions state** | the engine's handler throws a C++ exception, or `exit(1)` outside a frame | `plua_run` (`techniques/02-lua.md` §5) |

## Process

| Tried | Why it failed | Instead |
|---|---|---|
| Up to ~96 concurrent agents | about 60% of runs lost | a few agents, test-gated tasks |
| Docs that were never corrected (a town grid described as 113 × 256; it is 128 × 226) | the next person trusts the doc | binary claims as checked facts; fix the doc in the same commit |
| ~30 hand-made patched exe variants | nobody knew what was in each | one build script, a manifest per build |
| Testing in the Steam install with another mod's files still deployed | "vanilla" runs were not vanilla | a disposable test install, deploy only by script |
