/* A game clock that starts at zero instead of at boot.
 * From Sovereign Nations (dll/clock.c in https://github.com/dariooo512/pr2-sovereign-nations).
 *
 * GetTickCount and timeGetTime count milliseconds since Windows started. Once
 * a PC has been up for 24.86 days (2^31 ms) the value is negative as a signed
 * int, and WalExtPR2.dll's catch-up loop at +0x13F18 (next = ftol(next +
 * interval) while next + interval <= now, in single precision because D3D sets
 * the FPU to 24-bit mantissa) can no longer advance: an interval below half a
 * float step (128 ms at 2^31) adds nothing. The game then hangs with a black
 * window as soon as a world is entered. The unmodded game has the same bug.
 *
 * Both functions are redirected at their entry, for every module (including
 * WalExtPR2.dll, which is loaded later), to milliseconds since this DLL loaded
 * plus CLOCK_BASE. Call clock_install() from DllMain: a mod DLL loads at CRT start
 * (patches/load_mod_dll.py, or the in-memory loader), before the game reads a
 * clock, so every module sees one consistent clock. Process-wide: if two mods
 * install it, the last one wins, which is harmless. It runs from QPC, so it
 * neither wraps nor jumps for the life of the process.
 */
#include <windows.h>
#include <stdint.h>
#include "clock.h"
#include "log.h"

#define CLOCK_BASE 100000u           /* a little past zero: some code treats 0 as "unset" */

static LARGE_INTEGER g_freq, g_start;

static DWORD WINAPI game_ms(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    return (DWORD)((uint64_t)(now.QuadPart - g_start.QuadPart) * 1000u / (uint64_t)g_freq.QuadPart) + CLOCK_BASE;
}

static int redirect(const char *dll, const char *fn)
{
    HMODULE m = GetModuleHandleA(dll);
    uint8_t *at = m ? (uint8_t *)GetProcAddress(m, fn) : NULL;   /* follows forwarders */
    DWORD old;
    if (!at) return 0;
    if (!VirtualProtect(at, 5, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("CLOCK: cannot unprotect %s!%s (%lu)", dll, fn, GetLastError());
        return 0;
    }
    int32_t rel = (int32_t)((uintptr_t)game_ms - ((uintptr_t)at + 5));
    at[0] = 0xE9;                                               /* jmp game_ms */
    memcpy(at + 1, &rel, 4);
    VirtualProtect(at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, 5);
    return 1;
}

void clock_install(void)
{
    QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&g_start);
    if (!g_freq.QuadPart) {
        log_line("CLOCK: no performance counter; system clock left alone");
        return;
    }
    DWORD boot = GetTickCount();
    int n = redirect("kernelbase.dll", "GetTickCount") + redirect("kernel32.dll", "GetTickCount")
          + redirect("winmm.dll", "timeGetTime");
    log_line("CLOCK: %d entry points now count from game start (Windows up %lu.%lu days)",
             n, boot / 86400000u, boot / 8640000u % 10u);
}
