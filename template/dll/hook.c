/* Vendored unchanged from Sovereign Nations (dll/hook.c in https://github.com/dariooo512/pr2-sovereign-nations). */
#include <windows.h>
#include <string.h>
#include "hook.h"
#include "log.h"

static uint8_t *alloc_exec(int n)
{
    static uint8_t *pool, *cur;
    if (!pool || cur + n > pool + 0x10000) {
        pool = cur = VirtualAlloc(NULL, 0x10000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!pool) return NULL;
    }
    uint8_t *p = cur;
    cur += (n + 15) & ~15;
    return p;
}

static void write_code(uint32_t at, const void *src, int len)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)at, len, PAGE_EXECUTE_READWRITE, &old);
    memcpy((void *)(uintptr_t)at, src, len);
    VirtualProtect((void *)(uintptr_t)at, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void *)(uintptr_t)at, len);
}

static void put_rel32(uint8_t *p, uint8_t op, uint32_t from, uint32_t to)
{
    p[0] = op;
    *(int32_t *)(p + 1) = (int32_t)(to - (from + 5));
}

int patch_bytes(const char *name, uint32_t at, const uint8_t *expect, const uint8_t *repl, int len)
{
    if (memcmp((void *)(uintptr_t)at, expect, len) != 0) {
        log_line("PATCH %s @%08X: original bytes differ, NOT patched", name, at);
        return 0;
    }
    write_code(at, repl, len);
    log_line("PATCH %s @%08X ok", name, at);
    return 1;
}

int hook_install(const char *name, uint32_t at, const uint8_t *expect, int len, HookFn fn)
{
    if (len < 5 || memcmp((void *)(uintptr_t)at, expect, len) != 0) {
        log_line("HOOK %s @%08X: original bytes differ, NOT installed", name, at);
        return 0;
    }
    uint8_t *t = alloc_exec(64 + len);
    if (!t) return 0;
    uint8_t *p = t;
    *p++ = 0x60;                                   /* pushad              */
    *p++ = 0x9C;                                   /* pushfd              */
    *p++ = 0x54;                                   /* push esp  (Regs*)   */
    put_rel32(p, 0xE8, (uint32_t)(uintptr_t)p, (uint32_t)(uintptr_t)fn); p += 5; /* call fn */
    *p++ = 0x83; *p++ = 0xC4; *p++ = 0x04;         /* add esp, 4          */
    *p++ = 0x9D;                                   /* popfd               */
    *p++ = 0x61;                                   /* popad               */
    memcpy(p, expect, len); p += len;              /* displaced code      */
    put_rel32(p, 0xE9, (uint32_t)(uintptr_t)p, at + len); p += 5;

    uint8_t jmp[16];
    memset(jmp, 0x90, sizeof jmp);
    put_rel32(jmp, 0xE9, at, (uint32_t)(uintptr_t)t);
    write_code(at, jmp, len);
    log_line("HOOK %s @%08X ok (trampoline %p)", name, at, t);
    return 1;
}

int hook_install_ret(const char *name, uint32_t at, const uint8_t *expect, int len, HookRetFn fn,
                     uint16_t ret_pop)
{
    if (len < 5 || len > 32 || memcmp((void *)(uintptr_t)at, expect, len) != 0) {
        log_line("HOOK %s @%08X: original bytes differ, NOT installed", name, at);
        return 0;
    }
    uint8_t *t = alloc_exec(64 + len);
    if (!t) return 0;
    uint8_t *p = t;
    *p++ = 0x60;                                   /* pushad              */
    *p++ = 0x9C;                                   /* pushfd              */
    *p++ = 0x54;                                   /* push esp  (Regs*)   */
    put_rel32(p, 0xE8, (uint32_t)(uintptr_t)p, (uint32_t)(uintptr_t)fn); p += 5; /* call fn */
    *p++ = 0x83; *p++ = 0xC4; *p++ = 0x04;         /* add esp, 4          */
    *p++ = 0x85; *p++ = 0xC0;                      /* test eax, eax       */
    *p++ = 0x75; *p++ = (uint8_t)(2 + len + 5);    /* jnz handled         */
    *p++ = 0x9D;                                   /* popfd               */
    *p++ = 0x61;                                   /* popad               */
    memcpy(p, expect, len); p += len;              /* displaced code      */
    put_rel32(p, 0xE9, (uint32_t)(uintptr_t)p, at + len); p += 5;
    *p++ = 0x9D;                                   /* handled: popfd      */
    *p++ = 0x61;                                   /* popad (r->eax)      */
    *p++ = 0xC2; *p++ = (uint8_t)ret_pop; *p++ = (uint8_t)(ret_pop >> 8);   /* ret n */

    uint8_t jmp[32];
    memset(jmp, 0x90, sizeof jmp);
    put_rel32(jmp, 0xE9, at, (uint32_t)(uintptr_t)t);
    write_code(at, jmp, len);
    log_line("HOOK %s @%08X ok (trampoline %p, may return)", name, at, t);
    return 1;
}

int hook_install_jmp(const char *name, uint32_t at, const uint8_t *expect, int len, HookJmpFn fn)
{
    if (len < 5 || len > 32 || memcmp((void *)(uintptr_t)at, expect, len) != 0) {
        log_line("HOOK %s @%08X: original bytes differ, NOT installed", name, at);
        return 0;
    }
    uint8_t *t = alloc_exec(80 + len);
    if (!t) return 0;
    uint8_t *cell = t;                             /* the jump target fn returned */
    uint8_t *p = t + 8;
    uint8_t *entry = p;
    *p++ = 0x60;                                   /* pushad              */
    *p++ = 0x9C;                                   /* pushfd              */
    *p++ = 0x54;                                   /* push esp  (Regs*)   */
    put_rel32(p, 0xE8, (uint32_t)(uintptr_t)p, (uint32_t)(uintptr_t)fn); p += 5; /* call fn */
    *p++ = 0x83; *p++ = 0xC4; *p++ = 0x04;         /* add esp, 4          */
    *p++ = 0x85; *p++ = 0xC0;                      /* test eax, eax       */
    *p++ = 0x75; *p++ = (uint8_t)(2 + len + 5);    /* jnz redirect        */
    *p++ = 0x9D;                                   /* popfd               */
    *p++ = 0x61;                                   /* popad               */
    memcpy(p, expect, len); p += len;              /* displaced code      */
    put_rel32(p, 0xE9, (uint32_t)(uintptr_t)p, at + len); p += 5;
    *p++ = 0xA3; *(uint32_t *)p = (uint32_t)(uintptr_t)cell; p += 4;        /* redirect: mov [cell], eax */
    *p++ = 0x9D;                                   /* popfd               */
    *p++ = 0x61;                                   /* popad               */
    *p++ = 0xFF; *p++ = 0x25; *(uint32_t *)p = (uint32_t)(uintptr_t)cell; p += 4;  /* jmp [cell] */

    uint8_t jmp[32];
    memset(jmp, 0x90, sizeof jmp);
    put_rel32(jmp, 0xE9, at, (uint32_t)(uintptr_t)entry);
    write_code(at, jmp, len);
    log_line("HOOK %s @%08X ok (trampoline %p, may redirect)", name, at, entry);
    return 1;
}
