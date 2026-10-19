/* Adapted from Sovereign Nations (dll/log.c in
 * https://github.com/dariooo512/pr2-sovereign-nations): the only change
 * is that the file name comes from MOD_NAME (see mod.h) instead of being
 * hard-coded as PR2Nation.log.
 *
 * <MOD_NAME>.log next to the game exe, one flushed line per call.
 *
 * Plain Win32 (WriteFile on a handle opened once) under the DLL's own lock rather
 * than a CRT FILE: a second thread (for example a hang watchdog) may log too, and
 * at process exit Windows kills that thread wherever it is. A CRT stream lock held
 * by a killed thread would block the CRT's own exit-time flush; this handle has none. */
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include "log.h"
#include "mod.h"

static HANDLE g_log = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION g_lock;

void log_open(void)
{
    char path[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    while (n && path[n - 1] != '\\') n--;
    lstrcpynA(path + n, MOD_NAME ".log", MAX_PATH - (int)n);
    InitializeCriticalSection(&g_lock);
    g_log = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
}

void log_line(const char *fmt, ...)
{
    if (g_log == INVALID_HANDLE_VALUE) return;
    char buf[4096];
    SYSTEMTIME t;
    GetLocalTime(&t);
    int n = snprintf(buf, sizeof buf, "%02d:%02d:%02d ", t.wHour, t.wMinute, t.wSecond);
    va_list ap;
    va_start(ap, fmt);
    int m = vsnprintf(buf + n, sizeof buf - n - 2, fmt, ap);
    va_end(ap);
    if (m < 0) m = 0;
    n += m < (int)sizeof buf - n - 2 ? m : (int)sizeof buf - n - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';
    DWORD w;
    EnterCriticalSection(&g_lock);
    WriteFile(g_log, buf, (DWORD)n, &w, NULL);
    LeaveCriticalSection(&g_lock);
}
