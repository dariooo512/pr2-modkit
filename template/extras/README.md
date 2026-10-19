# Optional DLL parts

Two files from Sovereign Nations that most mods want. They are not built by default: copy the
ones you want into `dll/` (the build compiles every `dll/*.c`) and add the calls below.

| Files | What | Wire it in |
|---|---|---|
| `clock.c`, `clock.h` | **The uptime fix.** Once Windows has been up for 24.86 days, the game hangs with a black window on entering the world (a float timer in `WalExtPR2.dll` stops advancing; vanilla has the bug too). This redirects `GetTickCount` and `timeGetTime` to milliseconds since the DLL loaded. Process-wide. | `#include "clock.h"` in `main.c`; call `clock_install();` in `DllMain` right after `log_open();` (before the game reads a clock) |
| `watchdog.c`, `watchdog.h` | **Hang watchdog.** When no frame has run for 20 s it suspends the main thread for a moment, copies its context and stack, and logs the main thread's `eip` and likely return addresses as `module+offset` (`WATCHDOG: ...` lines in your log). A long load or save can trigger it once, harmlessly. | `#include "watchdog.h"` in `main.c`; in `on_frame()`: `watchdog_frame();` every frame, and `watchdog_start();` once (it ignores repeated calls) |

Example `on_frame()` with the watchdog:

```c
static void on_frame(Regs *r)
{
    (void)r;
    watchdog_start();          /* first call starts it; later calls return at once */
    watchdog_frame();
    mod_frame();
}
```

Checked: the template builds with both files copied into `dll/` and wired in as above (GCC 16.1,
i686). Neither was run in game as part of the template; both run in Sovereign Nations (the clock
fix since the uptime bug was found, the watchdog in every build).
