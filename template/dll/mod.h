/* Your mod's name and its entry points. Rename MOD_NAME when you copy the
 * template: it names the DLL (build.py reads it from here), the log file
 * (<MOD_NAME>.log), the dev switch (<MOD_NAME>.dev) and the console file
 * (<MOD_NAME>.cmd), all next to the game exe. */
#ifndef MOD_H
#define MOD_H
#include <stdint.h>

#define MOD_NAME "HelloMod"

/* Called once from DllMain, before the game's CRT starts (no game objects yet). */
void mod_install_hooks(void);

/* Called on the game's main thread once per frame (A2dLib input hook). */
void mod_frame(void);

/* Implemented in hello.c: the example Lua functions. */
void hello_register_lua(void);
int  hello_lua_registered(void);

/* Implemented in player.c: the human player's gold (CPlayer+0x3C). */
int64_t mod_player_gold(void);
int     mod_player_add_gold(int64_t delta);   /* 0 = refused (no game, overflow, debt) */

#endif
