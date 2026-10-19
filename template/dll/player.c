/* The human player's gold. Adapted from Sovereign Nations (dll/player.c in
 * https://github.com/dariooo512/pr2-sovereign-nations);
 * addresses from its research/economy_data.md, all for the 1.1.2.3 image:
 *
 *   0x00701EC0  CPlayer pool base (inline array, stride 0x94)          [static]
 *   0x00701EC4  the pool's slot table, u32 per slot, generation tag in the high word [static]
 *   0x00701EC8  the pool's capacity, u16, at most 0xFFFE              [static]
 *   0x00701ED0  handle of the human player: bits 0..15 pool index, 16..20 type (9),
 *               21..31 generation. 0x0009FFFF (index 0xFFFF) = no player: from start-up,
 *               through a New Game's pre-simulation (about 90 game days, run before the
 *               player is created) and after the player is destroyed     [static]
 *   CPlayer+0x25  alive flag, u8                                        [static]
 *   CPlayer+0x3C  gold, int32                                          [static]
 *   0x0060E570  void __thiscall Counter_Add(Counter *gold, int amount, int ledgerCat), ret 8 [static]
 *               clamps to +-2,000,000,000 and books the amount in the balance-sheet
 *               ledger; category 9 = "scripts" (what Lua Player_AlterGold uses).
 *
 * Never write CPlayer+0x3C directly: the ledger and the clamp would be skipped. */
#include <stdint.h>
#include "mod.h"

#define PLR_BASE  (*(uint8_t **)0x00701EC0)
#define PLR_SLOTS (*(uint8_t **)0x00701EC4)
#define PLR_CAP   (*(uint16_t *)0x00701EC8)
#define HUMAN_H   (*(uint32_t *)0x00701ED0)
enum { P_ALIVE = 0x25, P_GOLD = 0x3C, P_STRIDE = 0x94, LEDGER_SCRIPTS = 9 };

typedef void (__attribute__((thiscall)) *AddGold_t)(void *counter, int amount, int cat);
#define PR2_AddGold ((AddGold_t)0x0060E570)

/* The human player's CPlayer, or NULL while there is none (main menu, New Game pre-simulation).
 * The same tests as the game's own handle lookup 0x5C29E0, without its C++ throw: a player
 * handle, an index below the pool's capacity (0xFFFF never is), the slot's generation, alive. */
static uint8_t *human(void)
{
    uint8_t *base = PLR_BASE, *slots = PLR_SLOTS;
    uint32_t h = HUMAN_H, i = h & 0xFFFF;
    if (!base || !slots || !h || i == 0xFFFF || ((h >> 16) & 0x1F) != 9 || i >= PLR_CAP)
        return NULL;                                       /* no game, or no player yet */
    if (((h >> 21) ^ *(uint16_t *)(slots + 4 * i + 2)) & 0x7FF)
        return NULL;                                       /* a stale handle */
    uint8_t *p = base + P_STRIDE * i;
    return p[P_ALIVE] ? p : NULL;
}

int64_t mod_player_gold(void)
{
    uint8_t *p = human();
    return p ? *(int32_t *)(p + P_GOLD) : 0;
}

int mod_player_add_gold(int64_t d)
{
    uint8_t *p = human();
    if (!p || d < -2000000000LL || d > 2000000000LL) return 0;
    if (d < 0 && mod_player_gold() + d < 0) return 0;               /* no debt */
    if (d > 0 && mod_player_gold() + d > 2000000000LL) return 0;    /* the purse is an int32 */
    PR2_AddGold(p + P_GOLD, (int)d, LEDGER_SCRIPTS);
    return 1;
}
