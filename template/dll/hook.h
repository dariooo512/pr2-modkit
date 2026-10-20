/* Vendored unchanged from Sovereign Nations (dll/hook.h in https://github.com/dariooo512/pr2-sovereign-nations). */
/* Runtime detours for PR2Nation.dll.
 *
 * A hook replaces whole instructions at `at` (len >= 5, verified against
 * `expect` before patching) with a jmp to a trampoline that
 *   saves all registers + flags, calls handler(Regs*), restores them
 *   (so the handler may change registers), re-runs the displaced
 *   instructions (must be position-independent) and jumps back.
 * If another mod's hook is already there (a 5-byte jmp where `expect`
 * starts), the hook chains behind it: its handler runs, then that jmp.
 */
#ifndef PR2N_HOOK_H
#define PR2N_HOOK_H
#include <stdint.h>

typedef struct Regs {           /* layout of PUSHFD over PUSHAD */
    uint32_t eflags;
    uint32_t edi, esi, ebp, esp, ebx, edx, ecx, eax;
} Regs;

typedef void (*HookFn)(Regs *r);

/* Returns 1 on success (also chained), 0 if the bytes at `at` differ from `expect`. */
int hook_install(const char *name, uint32_t at, const uint8_t *expect, int len, HookFn fn);

/* Like hook_install, for a hook at a function entry: when fn returns nonzero
 * the trampoline restores the registers (with the eax fn left in r->eax) and
 * returns to the caller with `ret ret_pop` instead of running the function. */
typedef int (*HookRetFn)(Regs *r);
int hook_install_ret(const char *name, uint32_t at, const uint8_t *expect, int len, HookRetFn fn,
                     uint16_t ret_pop);

/* Like hook_install, but fn may also send execution elsewhere: when fn returns
 * an address, the registers are restored (with fn's changes) and execution
 * jumps there instead of running the displaced instructions. Returning 0 runs
 * them and continues at at+len as usual. */
typedef uint32_t (*HookJmpFn)(Regs *r);
int hook_install_jmp(const char *name, uint32_t at, const uint8_t *expect, int len, HookJmpFn fn);

/* Patch bytes in place after verifying the old ones. */
int patch_bytes(const char *name, uint32_t at, const uint8_t *expect, const uint8_t *repl, int len);

/* Stack argument i (0 = return address) at the hook point. */
static inline uint32_t stack_arg(const Regs *r, int i) { return ((uint32_t *)(uintptr_t)r->esp)[i]; }

#endif
