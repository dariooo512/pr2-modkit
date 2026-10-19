/* Vendored unchanged from Sovereign Nations (dll/lua.h in https://github.com/dariooo512/pr2-sovereign-nations). */
/* Guarded access to the game's "Missions" Lua state (research/lua_bridge.md).
 * The engine's _ERRORMESSAGE throws a C++ exception, so every call from the
 * DLL swaps in a harmless handler first. Main thread only. */
#ifndef PR2N_LUA_H
#define PR2N_LUA_H
#include <stdint.h>

typedef struct lua_State lua_State;
typedef int (__cdecl *lua_CFunction)(lua_State *L);

int  plua_init(void);                         /* resolve lua.dll exports */
lua_State *plua_state(void);                  /* NULL until the Missions state exists */
int  plua_run(const char *code);              /* 0 = ok; logs errors */
void plua_quiet_enter(lua_State *L);          /* count quiet-handler installs (see lua.c) */
void plua_quiet_leave(void);
void plua_frame_check(void);                  /* frame hook: repair a handler left installed */
int  plua_eval_int(const char *expr, int *out);  /* runs "PR2N_R=(expr)"; 1 = ok */
void plua_register(const char *name, lua_CFunction f);
int  plua_global_is_nil(const char *name);

/* argument helpers for our C functions (1-based Lua args) */
int  plua_argc(lua_State *L);
int  plua_int(lua_State *L, int i, int dflt);
const char *plua_str(lua_State *L, int i, const char *dflt);
void plua_push_int(lua_State *L, int v);
void plua_push_str(lua_State *L, const char *s);
void plua_push_nil(lua_State *L);

#endif
