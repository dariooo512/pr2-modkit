/* Vendored unchanged from Sovereign Nations (dll/lua.c in https://github.com/dariooo512/pr2-sovereign-nations). */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "lua.h"
#include "log.h"

typedef struct PR2State { lua_State *L; uint8_t borrowed; } PR2State;
#define PR2_GetMissionsState ((PR2State *(__cdecl *)(void))0x004337B0)

static int  (__cdecl *l_dostring)(lua_State *, const char *);
static int  (__cdecl *l_gettop)(lua_State *);
static void (__cdecl *l_settop)(lua_State *, int);
static void (__cdecl *l_pushvalue)(lua_State *, int);
static void (__cdecl *l_getglobal)(lua_State *, const char *);
static void (__cdecl *l_setglobal)(lua_State *, const char *);
static void (__cdecl *l_pushcclosure)(lua_State *, lua_CFunction, int);
static void (__cdecl *l_pushnumber)(lua_State *, int);
static void (__cdecl *l_pushstring)(lua_State *, const char *);
static void (__cdecl *l_pushnil)(lua_State *);
static int  (__cdecl *l_tonumber)(lua_State *, int);
static const char *(__cdecl *l_tostring)(lua_State *, int);
static int  (__cdecl *l_isnumber)(lua_State *, int);
static int  (__cdecl *l_type)(lua_State *, int);
static int  (__cdecl *l_call)(lua_State *, int, int);      /* protected in Lua 4.0 */

#define LUA_TNIL 1

static char g_last_error[512];

int plua_init(void)
{
    HMODULE m = GetModuleHandleA("lua.dll");
    if (!m) { log_line("lua.dll not loaded"); return 0; }
#define R(var, name) var = (void *)GetProcAddress(m, name); if (!var) { log_line("lua.dll: missing %s", name); return 0; }
    R(l_dostring, "lua_dostring"); R(l_gettop, "lua_gettop"); R(l_settop, "lua_settop");
    R(l_pushvalue, "lua_pushvalue"); R(l_getglobal, "lua_getglobal"); R(l_setglobal, "lua_setglobal");
    R(l_pushcclosure, "lua_pushcclosure"); R(l_pushnumber, "lua_pushnumber");
    R(l_pushstring, "lua_pushstring"); R(l_pushnil, "lua_pushnil"); R(l_tonumber, "lua_tonumber");
    R(l_tostring, "lua_tostring"); R(l_isnumber, "lua_isnumber"); R(l_type, "lua_type");
    R(l_call, "lua_call");
#undef R
    return 1;
}

lua_State *plua_state(void)
{
    if (!l_dostring) return NULL;
    PR2State *s = PR2_GetMissionsState();
    return s ? s->L : NULL;
}

static int __cdecl quiet_errormessage(lua_State *L)
{
    const char *m = l_tostring(L, 1);
    snprintf(g_last_error, sizeof g_last_error, "%s", m ? m : "?");
    return 0;
}

/* ---- quiet-handler bookkeeping ------------------------------------------
   While our quiet _ERRORMESSAGE is installed, a C++ exception thrown by an
   engine binding can unwind past us and leave it installed for good (then
   the next top-level Lua error ends in exit(1)). Every install is counted;
   the outermost one records the engine handler in PR2N_ENGINE_EM, and the
   frame hook (outside any Lua call) restores it if the count is not zero. */
static int g_quiet_depth;

void plua_quiet_enter(lua_State *L)
{
    if (g_quiet_depth++ == 0 && l_getglobal && L) {   /* (unresolved in the static-check host) */
        l_getglobal(L, "_ERRORMESSAGE");
        l_setglobal(L, "PR2N_ENGINE_EM");
    }
}

void plua_quiet_leave(void) { if (g_quiet_depth > 0) g_quiet_depth--; }

void plua_frame_check(void)
{
    lua_State *L = plua_state();
    if (!g_quiet_depth || !L) return;
    log_line("LUA: quiet error handler left installed (depth %d, unwound by an exception?) -> restored",
             g_quiet_depth);
    g_quiet_depth = 0;
    l_getglobal(L, "PR2N_ENGINE_EM");
    if (l_type(L, -1) != LUA_TNIL) l_setglobal(L, "_ERRORMESSAGE");
    else l_settop(L, l_gettop(L) - 1);
}

/* Top-level code runs inside a protected lua_call: lua_dostring's parser
   runs the garbage collector *before* it sets up its own error frame, so an
   error there (gc tag method, out of memory) with no frame would reach
   lua.dll's exit(1). */
static const char *g_tramp_code;
static int g_tramp_status;
static int __cdecl tramp(lua_State *L)
{
    g_tramp_status = l_dostring(L, g_tramp_code);
    return 0;
}

int plua_run(const char *code)
{
    lua_State *L = plua_state();
    if (!L) return -1;
    int top = l_gettop(L);
    plua_quiet_enter(L);
    l_getglobal(L, "_ERRORMESSAGE");               /* save the engine handler (throws) */
    l_pushcclosure(L, quiet_errormessage, 0);
    l_setglobal(L, "_ERRORMESSAGE");
    g_last_error[0] = 0;
    const char *prev_code = g_tramp_code;          /* plua_run can nest (bindings call it) */
    g_tramp_code = code;
    g_tramp_status = -1;
    l_pushcclosure(L, tramp, 0);
    int st = l_call(L, 0, 0) ? -2 : g_tramp_status;
    g_tramp_code = prev_code;
    l_pushvalue(L, top + 1);                       /* restore it */
    l_setglobal(L, "_ERRORMESSAGE");
    plua_quiet_leave();
    l_settop(L, top);
    if (st != 0)
        log_line("LUA error %d: %s | in: %.120s", st, g_last_error, code);
    return st;
}

int plua_eval_int(const char *expr, int *out)
{
    char buf[1024];
    snprintf(buf, sizeof buf, "PR2N_R = (%s)", expr);
    if (plua_run(buf) != 0) return 0;
    lua_State *L = plua_state();
    int top = l_gettop(L);
    l_getglobal(L, "PR2N_R");
    int ok = l_isnumber(L, -1);
    if (ok) *out = l_tonumber(L, -1);
    l_settop(L, top);
    return ok;
}

void plua_register(const char *name, lua_CFunction f)
{
    lua_State *L = plua_state();
    if (!L) return;
    l_pushcclosure(L, f, 0);
    l_setglobal(L, name);
}

int plua_global_is_nil(const char *name)
{
    lua_State *L = plua_state();
    if (!L) return 1;
    int top = l_gettop(L);
    l_getglobal(L, name);
    int nil = l_type(L, -1) == LUA_TNIL;
    l_settop(L, top);
    return nil;
}

int plua_argc(lua_State *L) { return l_gettop(L); }
int plua_int(lua_State *L, int i, int dflt) { return i <= l_gettop(L) && l_isnumber(L, i) ? l_tonumber(L, i) : dflt; }
const char *plua_str(lua_State *L, int i, const char *dflt)
{
    if (i > l_gettop(L)) return dflt;
    const char *s = l_tostring(L, i);
    return s ? s : dflt;
}
void plua_push_int(lua_State *L, int v) { l_pushnumber(L, v); }
void plua_push_str(lua_State *L, const char *s) { l_pushstring(L, s); }
void plua_push_nil(lua_State *L) { l_pushnil(L); }
