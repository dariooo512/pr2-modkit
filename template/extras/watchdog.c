/* Hang watchdog: when the game's main thread stops running frames, write where it waits.
 * From Sovereign Nations (dll/watchdog.c in https://github.com/dariooo512/pr2-sovereign-nations).
 *
 * A second thread checks the frame counter once a second. When no frame has run for
 * WD_STALL_S seconds it suspends the main thread, copies its registers and the top of
 * its stack, resumes it, and only then (no lock may be taken while the main thread is
 * stopped: it could hold the heap or the loader lock) logs EIP and the stack values
 * that look like return addresses, as module+offset:
 *   WATCHDOG: no frame for 20 s (frame 123456); main thread eip PR2_mod.exe:005B53CF
 *   WATCHDOG:   d3d8.dll+0x4ea48 Walhalla.dll+0x2b2fc ...
 * A load or a long save can also stop frames for a while; the line is then just
 * information. It is logged again if the stall lasts a minute, then not again until
 * frames run. The thread does nothing else and is killed with the process.
 */
#include <windows.h>
#include <stdint.h>
#include <string.h>
#include "log.h"
#include "watchdog.h"

#define WD_STALL_S   20
#define WD_STACK     0x4000           /* bytes of stack copied */
#define WD_MAX_RET   28

static DWORD g_main_tid;
static HANDLE g_main;
static volatile LONG g_started;
static volatile LONG g_frames;

void watchdog_frame(void) { InterlockedIncrement(&g_frames); }
static unsigned long frames(void) { return (unsigned long)g_frames; }

static int code_addr(uint32_t a)
{
    MEMORY_BASIC_INFORMATION mi;
    if (a < 0x10000 || !VirtualQuery((void *)(uintptr_t)a, &mi, sizeof mi)) return 0;
    return mi.State == MEM_COMMIT && mi.Type == MEM_IMAGE &&
           (mi.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY));
}

/* does a call instruction end right before `a`? (E8 rel32; FF /2 in its 2..6-byte forms) */
static int after_call(uint32_t a)
{
    if (!code_addr(a - 6)) return 0;
    const uint8_t *p = (const uint8_t *)(uintptr_t)a;
    return p[-5] == 0xE8 || p[-2] == 0xFF || p[-3] == 0xFF || p[-4] == 0xFF || p[-6] == 0xFF;
}

static void where(uint32_t a, char *out, int cap)
{
    HMODULE m = NULL;
    char path[MAX_PATH];
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (const char *)(uintptr_t)a, &m) && m && GetModuleFileNameA(m, path, sizeof path)) {
        const char *name = strrchr(path, '\\');
        name = name ? name + 1 : path;
        if (m == GetModuleHandleA(NULL)) wsprintfA(out, "%s:%08X", name, a);
        else wsprintfA(out, "%s+0x%X", name, a - (uint32_t)(uintptr_t)m);
    } else {
        wsprintfA(out, "%08X", a);
    }
    (void)cap;
}

static uint8_t g_stack[WD_STACK];

static void report(unsigned long frame, int secs)
{
    CONTEXT c;
    memset(&c, 0, sizeof c);
    c.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER;
    uint32_t n = 0;
    if (SuspendThread(g_main) == (DWORD)-1) return;
    if (GetThreadContext(g_main, &c)) {
        MEMORY_BASIC_INFORMATION mi;                    /* VirtualQuery is a plain system call */
        if (VirtualQuery((void *)(uintptr_t)c.Esp, &mi, sizeof mi) && mi.State == MEM_COMMIT) {
            uint32_t end = (uint32_t)(uintptr_t)mi.BaseAddress + (uint32_t)mi.RegionSize;
            n = end - c.Esp < WD_STACK ? end - c.Esp : WD_STACK;
            memcpy(g_stack, (void *)(uintptr_t)c.Esp, n);
        }
    }
    ResumeThread(g_main);
    char eip[MAX_PATH + 16], line[1024];
    where(c.Eip, eip, sizeof eip);
    log_line("WATCHDOG: no frame for %d s (frame %lu); main thread eip %s esp %08X", secs, frame, eip, (unsigned)c.Esp);
    int len = 0, found = 0;
    line[0] = 0;
    for (uint32_t o = 0; o + 4 <= n && found < WD_MAX_RET; o += 4) {
        uint32_t v;
        memcpy(&v, g_stack + o, 4);
        if (!code_addr(v) || !after_call(v)) continue;
        char w[MAX_PATH + 16];
        where(v, w, sizeof w);
        if (len + (int)strlen(w) + 2 >= (int)sizeof line) break;
        len += wsprintfA(line + len, " %s", w);
        found++;
    }
    log_line("WATCHDOG:  stack%s", found ? line : " (no return addresses found)");
}

static DWORD WINAPI watch(void *arg)
{
    (void)arg;
    unsigned long last = frames();
    int still = 0, reported = 0;
    for (;;) {
        Sleep(1000);
        unsigned long f = frames();
        if (f != last) {
            if (reported) log_line("WATCHDOG: frames run again after %d s", still);
            last = f;
            still = 0;
            reported = 0;
            continue;
        }
        still++;
        if ((still == WD_STALL_S && !reported) || still == 60) {
            report(f, still);
            reported = 1;
        }
    }
    return 0;
}

/* called on the main thread (first frame) */
void watchdog_start(void)
{
    if (InterlockedExchange(&g_started, 1)) return;
    g_main_tid = GetCurrentThreadId();
    g_main = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, g_main_tid);
    if (!g_main) {
        log_line("WATCHDOG: cannot open the main thread (%lu); off", GetLastError());
        return;
    }
    HANDLE t = CreateThread(NULL, 64 * 1024, watch, NULL, 0, NULL);
    if (t) CloseHandle(t);
    log_line("WATCHDOG: %s (main thread %lu)", t ? "on" : "could not start", g_main_tid);
}
