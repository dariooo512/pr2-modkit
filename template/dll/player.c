/* The human player's gold. Adapted from Sovereign Nations (dll/player.c in
 * https://github.com/dariooo512/pr2-sovereign-nations);
 * addresses from its research/economy_data.md, all for the 1.1.2.3 image:
 *
 *   0x00701EC0  CPlayer pool base (inline array, stride 0x94)          [static]
 *   0x00701ED0  handle of the human player (low 16 bits = pool index)  [static]
 *   CPlayer+0x3C  gold, int32                                          [static]
 *   0x0060E570  void __thiscall Counter_Add(Counter *gold, int amount, int ledgerCat), ret 8 [static]
 *               clamps to +-2,000,000,000 and books the amount in the balance-sheet
 *               ledger; category 9 = "scripts" (what Lua Player_AlterGold uses).
 *
 * Never write CPlayer+0x3C directly: the ledger and the clamp would be skipped. */
#include <stdint.h>
#include "mod.h"

#define PLR_BASE  (*(uint8_t **)0x00701EC0)
#define HUMAN_H   (*(uint32_t *)0x00701ED0)
enum { P_GOLD = 0x3C, P_STRIDE = 0x94, LEDGER_SCRIPTS = 9 };

typedef void (__attribute__((thiscall)) *AddGold_t)(void *counter, int amount, int cat);
#define PR2_AddGold ((AddGold_t)0x0060E570)

static uint8_t *human(void)
{
    if (!PLR_BASE || !HUMAN_H) return NULL;               /* no game running */
    return PLR_BASE + P_STRIDE * (HUMAN_H & 0xFFFF);
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
