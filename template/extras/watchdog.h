#ifndef MOD_WATCHDOG_H
#define MOD_WATCHDOG_H
/* Logs where the main thread waits when frames stop for 20 s (watchdog.c). */
void watchdog_start(void);   /* main thread, first frame */
void watchdog_frame(void);   /* main thread, every frame (counts frames) */
#endif
