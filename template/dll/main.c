/* Template mod DLL for Port Royale 2 (Steam 1.1.2.3, the DRM-free image).
 *
 * How it runs (docs/make-your-own-mod.md):
 *   1. Something loads this DLL at process start: in a modder's test install a
 *      5-byte detour at the exe's entry point (patches/load_mod_dll.py, the same
 *      technique as Sovereign Nations' P06); in a player's Steam install the
 *      kit's loader (loader/README.md), which reads HelloMod.patches (made by
 *      tools/release/export_patches.py from install.json). The DLL itself does
 *      not care which.
 *   2. DllMain opens the log and installs one runtime hook on A2dLib's
 *      CWalhallaClient::InputProcessor, which the game calls once per frame on
 *      its main thread. Everything that touches game objects runs from there.
 *   3. Each frame, mod_frame() (re)registers the mod's Lua functions when the
 *      game's Missions Lua state exists and does not have them yet (New Game
 *      and Load replace the Lua globals), and, in a dev install only, runs the
 *      Lua console file <MOD_NAME>.cmd.
 */
#include <windows.h>
#include <stdio.h>
#include "hook.h"
#include "log.h"
#include "lua.h"
#include "mod.h"

/* A2dLib-2.20A.dll + 0x2F2C0 = CWalhallaClient::InputProcessor.
 * Original bytes: push ebx; push ebp; push esi; push edi; mov ebp, ecx.
 * Sovereign Nations hooks it too: whichever DLL comes second finds the
 * other's jmp there and chains behind it (hook.c), so both run each frame. */
#define A2D_INPUTPROCESSOR_RVA 0x2F2C0

static void exe_dir_file(char *path, const char *name)
{
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    while (n && path[n - 1] != '\\') n--;
    lstrcpynA(path + n, name, MAX_PATH - (int)n);
}

/* Dev mode = a file <MOD_NAME>.dev next to the exe. Never ship that file. */
static int dev_mode(void)
{
    static int cached = -1;
    if (cached < 0) {
        char path[MAX_PATH];
        exe_dir_file(path, MOD_NAME ".dev");
        cached = GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
        log_line("dev mode %s (" MOD_NAME ".dev)", cached ? "ON" : "off");
    }
    return cached;
}

/* Dev console: write Lua into <MOD_NAME>.cmd next to the exe (write it under a
 * temporary name and rename it, so the DLL never reads half a file); the next
 * frame runs it in the Missions state and deletes it. Output goes to the log. */
static void dev_console(void)
{
    char path[MAX_PATH];
    exe_dir_file(path, MOD_NAME ".cmd");
    FILE *f = fopen(path, "rb");
    if (!f) return;
    static char code[8192];
    size_t len = fread(code, 1, sizeof code - 1, f);
    fclose(f);
    DeleteFileA(path);
    code[len] = 0;
    log_line("CONSOLE run (%u bytes)", (unsigned)len);
    log_line("CONSOLE status %d", plua_run(code));
}

void mod_frame(void)
{
    static unsigned long tick;
    plua_frame_check();                       /* repair a Lua error handler left behind */
    if (++tick % 30) return;                  /* twice a second is plenty */
    if (!plua_state()) return;                /* main menu: no Missions state yet */
    if (!hello_lua_registered()) hello_register_lua();
    if (dev_mode()) dev_console();
}

static void on_frame(Regs *r)
{
    (void)r;
    mod_frame();
}

void mod_install_hooks(void)
{
    if (!plua_init()) log_line("Lua bridge unavailable");
    HMODULE a2d = GetModuleHandleA("A2dLib-2.20A.dll");
    if (!a2d) {
        log_line("A2dLib not loaded; frame hook unavailable");
        return;
    }
    static const uint8_t ip[] = {0x53, 0x55, 0x56, 0x57, 0x8B, 0xE9};
    hook_install("A2d.InputProcessor", (uint32_t)(uintptr_t)a2d + A2D_INPUTPROCESSOR_RVA,
                 ip, sizeof ip, on_frame);
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID res)
{
    (void)h; (void)res;
    if (reason == DLL_PROCESS_ATTACH) {
        log_open();
        log_line(MOD_NAME ".dll loaded (build " __DATE__ " " __TIME__ ")");
        mod_install_hooks();
    }
    return TRUE;
}
