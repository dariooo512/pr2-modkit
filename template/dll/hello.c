/* The example mod: three Lua functions in the game's Missions state.
 *
 *   Hello_Version()      -> "HelloMod 0.1"
 *   Hello_GetGold()      -> the human player's gold
 *   Hello_Gift(amount)   -> 1 if `amount` gold was added (negative = taken), else 0
 *
 * Call them from any mission script (include.ini), or in a dev install from
 * the console file HelloMod.cmd, for example:
 *     Hello_Log(Hello_Version(), Hello_GetGold(), Hello_Gift(1000), Hello_GetGold())
 * which writes one line into HelloMod.log.
 *
 * Rules for a Lua C function in this engine (Lua 4.1, "ASCARON_LUA"):
 *   - __cdecl, takes lua_State*, returns the number of results it pushed;
 *   - arguments are 1-based; read them with plua_int / plua_str (defaults if missing);
 *   - never let a C++ exception or a Lua error escape: the engine's _ERRORMESSAGE
 *     throws, and plua_run swaps in a quiet handler for code the DLL runs itself.
 */
#include <string.h>
#include "lua.h"
#include "log.h"
#include "mod.h"

static int clamp32(int64_t v) { return v > 0x7FFFFFFF ? 0x7FFFFFFF : v < -0x7FFFFFFF ? -0x7FFFFFFF : (int)v; }

static int __cdecl l_version(lua_State *L) { plua_push_str(L, MOD_NAME " 0.1"); return 1; }

static int __cdecl l_get_gold(lua_State *L) { plua_push_int(L, clamp32(mod_player_gold())); return 1; }

static int __cdecl l_gift(lua_State *L)
{
    int amount = plua_int(L, 1, 0);
    int ok = amount != 0 && mod_player_add_gold(amount);
    log_line("Hello_Gift(%d) -> %d, gold now %d", amount, ok, clamp32(mod_player_gold()));
    plua_push_int(L, ok);
    return 1;
}

/* Hello_Log(a, b, ...) writes its arguments to HelloMod.log, joined by " | ". */
static int __cdecl l_log(lua_State *L)
{
    char line[1024] = "";
    for (int i = 1; i <= plua_argc(L) && strlen(line) < 900; i++) {
        strncat(line, plua_str(L, i, "nil"), 120);
        strcat(line, i < plua_argc(L) ? " | " : "");
    }
    log_line("LUA %s", line);
    return 0;
}

static const struct { const char *name; lua_CFunction f; } k_api[] = {
    {"Hello_Version", l_version}, {"Hello_GetGold", l_get_gold},
    {"Hello_Gift", l_gift}, {"Hello_Log", l_log},
};

int hello_lua_registered(void) { return !plua_global_is_nil("Hello_Version"); }

void hello_register_lua(void)
{
    for (unsigned i = 0; i < sizeof k_api / sizeof *k_api; i++)
        plua_register(k_api[i].name, k_api[i].f);
    log_line("Hello_* Lua API registered (%u functions)", (unsigned)(sizeof k_api / sizeof *k_api));
}
