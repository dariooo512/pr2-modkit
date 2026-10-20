/* The PR2 mod loader: version.dll in the game folder, one loader for every mod (README.md).
 *
 * The game imports VERSION.dll (GetFileVersionInfoA, GetFileVersionInfoSizeA, VerQueryValueA),
 * which is not a KnownDLL, so Windows loads this file from the game folder first. Every
 * export forwards to the real version.dll from the system folder (loaded on first use).
 *
 * PR2.exe stays untouched on disk, the Steam DRM runs as always. In DllMain the loader reads
 * every `*.patches` file next to the exe (patch data, one per mod, made by the kit's
 * tools/release/export_patches.py), in file-name order, and accepts each one that
 *   - is valid patch data (format 2, or Sovereign Nations' format 1) with an intact checksum,
 *   - is not switched off (<STEM>_OFF in the environment, e.g. HELLOMOD_OFF=1),
 *   - uses the same trigger as the files accepted before it (the same game build), and
 *   - changes no byte that an accepted file changes: patch sites and patch sections must not
 *     overlap. A file that overlaps is refused, the log says with which file, and the game
 *     starts with the others.
 * Then it hooks one import slot, MSVCR71!__set_app_type (the `hook` record). The C runtime
 * calls it right after the program's entry point, when the game code is in memory in its final
 * form. At that call (identified by its return address) the loader puts the slot back and,
 * for each accepted file in order:
 *   1. leaves it alone if the exe already carries its patches (a patched test exe, the file
 *      route): that exe loads the mod's DLL itself;
 *   2. checks the original bytes of EVERY patch site of the file; on any mismatch patches
 *      nothing of that file and does not load its DLL (not the game build it was made for);
 *   3. else puts the file's section at its address if that memory is free, otherwise anywhere
 *      (applying the file's relocations and telling the DLL where in <STEM>_SECTION), writes
 *      the patches and loads the DLL the file names (the `dll` record).
 * Finally it calls the real __set_app_type. PR2LOADER_OFF=1 starts the game without any mod.
 * Log: PR2Loader.log next to the exe, one line per mod and decision.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "sha256.h"

#define LOADER_VERSION 2
/* The setup recognises the loader (and its version) by this string: keep its form. */
__attribute__((used)) static const char k_ident[] = "PR2-LOADER 2 (one loader for every mod: *.patches next to the exe)";

/* ---- the real version.dll ---------------------------------------------------------- */
static const char *const k_names[17] = {
    "GetFileVersionInfoA", "GetFileVersionInfoByHandle", "GetFileVersionInfoExA", "GetFileVersionInfoExW",
    "GetFileVersionInfoSizeA", "GetFileVersionInfoSizeExA", "GetFileVersionInfoSizeExW", "GetFileVersionInfoSizeW",
    "GetFileVersionInfoW", "VerFindFileA", "VerFindFileW", "VerInstallFileA", "VerInstallFileW",
    "VerLanguageNameA", "VerLanguageNameW", "VerQueryValueA", "VerQueryValueW"};
FARPROC g_real[17];

void ensure_real(void)
{
    static HMODULE h;
    if (h) return;
    char path[MAX_PATH];
    UINT n = GetSystemDirectoryA(path, MAX_PATH);           /* SysWOW64 for this 32-bit process */
    lstrcpynA(path + n, "\\version.dll", MAX_PATH - (int)n);
    h = LoadLibraryA(path);
    for (int i = 0; i < 17; i++) g_real[i] = h ? GetProcAddress(h, k_names[i]) : NULL;
}

/* each export: make sure the real DLL is there, then jump to its function (arguments untouched) */
#define STUB(i, name) __asm__(".globl _" #name "_proxy\n_" #name "_proxy:\n\tcall _ensure_real\n" \
                              "\tjmp *_g_real+" #i "*4\n");
STUB(0, GetFileVersionInfoA) STUB(1, GetFileVersionInfoByHandle) STUB(2, GetFileVersionInfoExA)
STUB(3, GetFileVersionInfoExW) STUB(4, GetFileVersionInfoSizeA) STUB(5, GetFileVersionInfoSizeExA)
STUB(6, GetFileVersionInfoSizeExW) STUB(7, GetFileVersionInfoSizeW) STUB(8, GetFileVersionInfoW)
STUB(9, VerFindFileA) STUB(10, VerFindFileW) STUB(11, VerInstallFileA) STUB(12, VerInstallFileW)
STUB(13, VerLanguageNameA) STUB(14, VerLanguageNameW) STUB(15, VerQueryValueA) STUB(16, VerQueryValueW)

/* ---- log ----------------------------------------------------------------------------- */
static char g_dir[MAX_PATH];

static void lg(const char *fmt, ...)
{
    char path[MAX_PATH + 32], line[700];
    snprintf(path, sizeof path, "%sPR2Loader.log", g_dir);
    SYSTEMTIME t;
    GetLocalTime(&t);
    int n = snprintf(line, sizeof line, "%02d:%02d:%02d ", t.wHour, t.wMinute, t.wSecond);
    va_list ap;
    va_start(ap, fmt);
    n += vsnprintf(line + n, sizeof line - n, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof line - 3) n = (int)sizeof line - 3;
    line[n++] = '\r';
    line[n++] = '\n';
    HANDLE f = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(f, line, (DWORD)n, &w, NULL);
    CloseHandle(f);
}

/* ---- the patch data (format: README.md, tools/release/export_patches.py) ---------------- */
#define MAX_MODS    16
#define MAX_PATCHES 512
#define MAX_RELOCS  1024
#define MAX_BYTES   32
#define MAX_TEXT    (1024 * 1024)
#define MAX_SECTION 0x100000
typedef struct { uint32_t va; uint8_t old[MAX_BYTES], neu[MAX_BYTES]; int n; } Patch;
typedef struct { int patch; uint32_t off; int sign; } Reloc;          /* patch -1: in the section */
typedef struct {
    char file[64], stem[64], name[96], dll[64];
    int format;
    uint32_t sec_va, sec_size, hook_slot, hook_ret;
    uint8_t *sec;
    Patch p[MAX_PATCHES];
    int np;
    Reloc r[MAX_RELOCS];
    int nr;
} Mod;

static Mod *g_mods[MAX_MODS];
static int g_nmods;
static uint32_t g_hook_slot, g_hook_ret;

static int hexbytes(const char *h, uint8_t *out, int cap)
{
    int n = 0;
    while (h[0] && h[1] && n < cap) {
        unsigned v;
        if (sscanf(h, "%2x", &v) != 1) return -1;
        out[n++] = (uint8_t)v;
        h += 2;
    }
    return *h ? -1 : n;
}

static void free_mod(Mod *m)
{
    if (!m) return;
    free(m->sec);
    free(m);
}

/* Parses one patch file into m; returns NULL on success, else why the file is refused. */
static const char *parse(Mod *m, char *text, size_t len)
{
    size_t w = 0;                                   /* normalise line ends */
    for (size_t i = 0; i < len; i++) if (text[i] != '\r') text[w++] = text[i];
    text[w] = 0;
    while (w && text[w - 1] == '\n') text[--w] = 0;
    char *end = strrchr(text, '\n');
    if (!strncmp(text, "PR2-PATCHDATA 2\n", 16)) m->format = 2;
    else if (!strncmp(text, "PR2NATION-PATCHDATA 1\n", 22)) m->format = 1;
    else return "not patch data of format 1 or 2";
    if (!end || strncmp(end + 1, "end ", 4)) return "truncated (no end line)";
    char hex[65];
    sha256_hex(text, (size_t)(end - text) + 1, hex);
    if (strncmp(end + 5, hex, 64)) return "damaged (checksum)";
    *end = 0;
    for (char *ln = strtok(text, "\n"); ln; ln = strtok(NULL, "\n")) {
        if (!strncmp(ln, "name ", 5)) {               /* the rest of the line, spaces included */
            lstrcpynA(m->name, ln + 5, sizeof m->name);
            continue;
        }
        char *semi = strstr(ln, " ;");
        if (semi) *semi = 0;
        char k[16] = "", a[80] = "", b[80] = "", c[80] = "", d[16] = "";
        int nf = sscanf(ln, "%15s %79s %79s %79s %15s", k, a, b, c, d);
        if (nf < 1 || k[0] == '#') continue;
        if (!strcmp(k, "section") && nf >= 3) {
            if (m->sec) return "two section records";
            m->sec_va = strtoul(a, NULL, 16);
            m->sec_size = strtoul(b, NULL, 16);
            if (!m->sec_size || m->sec_size > MAX_SECTION) return "bad section size";
            if (!(m->sec = calloc(1, m->sec_size))) return "no memory";
        } else if (!strcmp(k, "data") && nf >= 3) {
            uint8_t buf[64];
            uint32_t off = strtoul(a, NULL, 16);
            int n = hexbytes(b, buf, sizeof buf);
            if (!m->sec || n < 0 || off + (uint32_t)n > m->sec_size) return "bad data record";
            memcpy(m->sec + off, buf, (size_t)n);
        } else if (!strcmp(k, "patch") && nf >= 4) {
            if (m->np >= MAX_PATCHES) return "too many patches";
            Patch *p = &m->p[m->np++];
            p->va = strtoul(a, NULL, 16);
            p->n = hexbytes(b, p->old, MAX_BYTES);
            if (p->n <= 0 || hexbytes(c, p->neu, MAX_BYTES) != p->n) return "bad patch record";
        } else if (!strcmp(k, "reloc") && nf >= 4) {
            if (m->nr >= MAX_RELOCS) return "too many relocations";
            Reloc *r = &m->r[m->nr++];
            if (a[0] == 'S') {
                r->patch = -1;
                r->off = strtoul(b, NULL, 16);
                r->sign = c[0] == '-' ? -1 : 1;
            } else if (a[0] == 'P' && nf >= 5) {
                r->patch = atoi(b);
                r->off = strtoul(c, NULL, 16);
                r->sign = d[0] == '-' ? -1 : 1;
            } else return "bad reloc record";
        } else if (!strcmp(k, "hook") && nf >= 3) {
            m->hook_slot = strtoul(a, NULL, 16);
            m->hook_ret = strtoul(b, NULL, 16);
        } else if (!strcmp(k, "dll") && nf >= 2) {
            lstrcpynA(m->dll, a, sizeof m->dll);
        }
    }
    if (!m->hook_slot || !m->dll[0]) return "incomplete (no hook or dll record)";
    if (strpbrk(m->dll, "\\/:")) return "the dll record must be a file name next to the exe";
    if (m->format == 1 && (!m->sec || !m->np)) return "incomplete (format 1 needs a section and patches)";
    for (int i = 0; i < m->nr; i++) {
        const Reloc *r = &m->r[i];
        if (r->patch < 0 ? (!m->sec || r->off + 4 > m->sec_size)
                         : (r->patch >= m->np || r->off + 4 > (uint32_t)m->p[r->patch].n))
            return "a relocation is out of range";
    }
    return NULL;
}

static int overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn)
{
    return an && bn && a < b + bn && b < a + an;
}

/* The first byte range of m that one of o's ranges overlaps, or 0. */
static uint32_t conflict(const Mod *m, const Mod *o)
{
    for (int i = -1; i < m->np; i++) {
        uint32_t va = i < 0 ? m->sec_va : m->p[i].va, n = i < 0 ? (m->sec ? m->sec_size : 0) : (uint32_t)m->p[i].n;
        for (int j = -1; j < o->np; j++) {
            uint32_t vb = j < 0 ? o->sec_va : o->p[j].va, nb = j < 0 ? (o->sec ? o->sec_size : 0) : (uint32_t)o->p[j].n;
            if (overlap(va, n, vb, nb)) return va > vb ? va : vb;
        }
    }
    return 0;
}

static void env_name(char *out, size_t cap, const char *stem, const char *suffix)
{
    size_t i = 0;
    for (; stem[i] && i + strlen(suffix) + 1 < cap; i++) {
        char ch = stem[i];
        out[i] = (ch >= 'a' && ch <= 'z') ? (char)(ch - 32) : ((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')) ? ch : '_';
    }
    lstrcpynA(out + i, suffix, (int)(cap - i));
}

static void read_mod(const char *file)
{
    Mod *m = calloc(1, sizeof *m);
    if (!m) return;
    lstrcpynA(m->file, file, sizeof m->file);
    lstrcpynA(m->stem, file, sizeof m->stem);
    char *dot = strrchr(m->stem, '.');
    if (dot) *dot = 0;
    lstrcpynA(m->name, m->stem, sizeof m->name);
    char off[96];
    env_name(off, sizeof off, m->stem, "_OFF");
    if (GetEnvironmentVariableA(off, NULL, 0)) {
        lg("%s: %s set, not loaded", file, off);
        free_mod(m);
        return;
    }
    char path[MAX_PATH + 80];
    snprintf(path, sizeof path, "%s%s", g_dir, m->file);
    FILE *f = fopen(path, "rb");
    char *text = malloc(MAX_TEXT + 1);
    size_t len = f && text ? fread(text, 1, MAX_TEXT, f) : 0;
    if (f) fclose(f);
    const char *why = !text ? "no memory" : !len ? "cannot be read" : len >= MAX_TEXT ? "too large" : NULL;
    if (!why) {
        text[len] = 0;
        why = parse(m, text, len);
    }
    free(text);
    if (!why && g_nmods && (m->hook_slot != g_hook_slot || m->hook_ret != g_hook_ret))
        why = "made for another game build (its trigger differs from the other mods')";
    for (int i = 0; !why && i < g_nmods; i++) {
        uint32_t at = conflict(m, g_mods[i]);
        if (at) {
            static char msg[400];
            snprintf(msg, sizeof msg, "conflicts with %s (%s): both change the bytes at %08X. Only one of the "
                     "two mods can run; remove one of them", g_mods[i]->file, g_mods[i]->name, at);
            why = msg;
        }
    }
    if (why) {
        lg("%s (%s): %s; this mod is not loaded, the game starts without it", file, m->name, why);
        free_mod(m);
        return;
    }
    if (!g_nmods) {
        g_hook_slot = m->hook_slot;
        g_hook_ret = m->hook_ret;
    }
    lg("%s (%s): format %d, %d patches, %s; loads %s", file, m->name, m->format, m->np,
       m->sec ? "a section" : "no section", m->dll);
    g_mods[g_nmods++] = m;
}

static int by_name(const void *a, const void *b)
{
    return lstrcmpiA((const char *)a, (const char *)b);
}

static int read_mods(void)
{
    static char names[MAX_MODS * 2][64];
    int n = 0;
    char pat[MAX_PATH + 16];
    snprintf(pat, sizeof pat, "%s*.patches", g_dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            size_t l = strlen(fd.cFileName);
            /* *.patches only (FindFirstFile also matches *.patches_old etc. through 8.3 names) */
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY || l <= 8 || lstrcmpiA(fd.cFileName + l - 8, ".patches"))
                continue;
            if (l >= 60) lg("%s: file name too long (59 characters at most); not loaded", fd.cFileName);
            else if (n < MAX_MODS * 2) lstrcpynA(names[n++], fd.cFileName, 64);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    if (!n) {
        lg("loader %d: no *.patches next to the game: the game starts without mods", LOADER_VERSION);
        return 0;
    }
    qsort(names, (size_t)n, sizeof names[0], by_name);
    lg("loader %d: %d patch file(s) next to the game", LOADER_VERSION, n);
    for (int i = 0; i < n; i++) {
        if (g_nmods >= MAX_MODS) {
            lg("%s: more than %d mods; not loaded", names[i], MAX_MODS);
            continue;
        }
        read_mod(names[i]);
    }
    return g_nmods;
}

/* ---- patching ------------------------------------------------------------------------- */
static int readable(uint32_t va, int n)
{
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery((void *)(uintptr_t)va, &mi, sizeof mi) || mi.State != MEM_COMMIT) return 0;
    return va + (uint32_t)n <= (uint32_t)(uintptr_t)mi.BaseAddress + (uint32_t)mi.RegionSize;
}

static void write_mem(uint32_t va, const void *src, size_t n)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)va, n, PAGE_EXECUTE_READWRITE, &old);
    memcpy((void *)(uintptr_t)va, src, n);
    VirtualProtect((void *)(uintptr_t)va, n, old, &old);
}

static void fix(uint8_t *at, int sign, uint32_t delta)
{
    uint32_t v;
    memcpy(&v, at, 4);
    v += sign > 0 ? delta : (uint32_t)-delta;
    memcpy(at, &v, 4);
}

static void apply(Mod *m)
{
    /* already patched on disk (a patched test exe, the file route): that exe loads the DLL itself */
    for (int i = 0; i < m->np; i++)
        if (readable(m->p[i].va, m->p[i].n) && !memcmp((void *)(uintptr_t)m->p[i].va, m->p[i].neu, m->p[i].n) &&
            (!m->sec || readable(m->sec_va, 16))) {
            lg("%s (%s): this exe already carries the patches (file route): loader idle for this mod", m->file, m->name);
            return;
        }
    for (int i = 0; i < m->np; i++) {
        const Patch *p = &m->p[i];
        if (!readable(p->va, p->n) || memcmp((void *)(uintptr_t)p->va, p->old, p->n)) {
            lg("%s (%s): original bytes differ at %08X: not the game build the mod was made for; "
               "nothing patched, the game starts without this mod", m->file, m->name, p->va);
            return;
        }
    }
    uint8_t *at = NULL;
    if (m->sec) {
        at = VirtualAlloc((void *)(uintptr_t)m->sec_va, m->sec_size, MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
        if (!at) at = VirtualAlloc(NULL, m->sec_size, MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
        if (!at) {
            lg("%s (%s): no memory for the patch section: the game starts without this mod", m->file, m->name);
            return;
        }
        uint32_t delta = (uint32_t)(uintptr_t)at - m->sec_va;
        memcpy(at, m->sec, m->sec_size);
        for (int i = 0; i < m->nr; i++)
            fix(m->r[i].patch < 0 ? at + m->r[i].off : m->p[m->r[i].patch].neu + m->r[i].off, m->r[i].sign, delta);
        if (delta) {
            char v[16], var[96];
            snprintf(v, sizeof v, "%08X", (unsigned)(uintptr_t)at);
            env_name(var, sizeof var, m->stem, "_SECTION");
            SetEnvironmentVariableA(var, v);
            lg("%s (%s): section moved to %08X (its address was taken), %s=%s", m->file, m->name,
               (unsigned)(uintptr_t)at, var, v);
        }
    }
    for (int i = 0; i < m->np; i++) write_mem(m->p[i].va, m->p[i].neu, (size_t)m->p[i].n);
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    char dll[MAX_PATH + 80];
    snprintf(dll, sizeof dll, "%s%s", g_dir, m->dll);
    HMODULE h = LoadLibraryA(dll);
    if (m->sec)
        lg("%s (%s): %d patches applied in memory, section at %08X; %s %s", m->file, m->name, m->np,
           (unsigned)(uintptr_t)at, m->dll, h ? "loaded" : "did NOT load");
    else
        lg("%s (%s): %d patches applied in memory, no section; %s %s", m->file, m->name, m->np, m->dll,
           h ? "loaded" : "did NOT load");
}

/* ---- the trigger: MSVCR71!__set_app_type, called by the CRT right after the entry point ---- */
typedef void (__cdecl *SetAppType_t)(int);
static SetAppType_t g_set_app_type;

static void __cdecl on_set_app_type(int type)
{
    uint32_t ret = (uint32_t)(uintptr_t)__builtin_return_address(0);
    SetAppType_t real = g_set_app_type;
    if (ret == g_hook_ret) {
        write_mem(g_hook_slot, &real, 4);                        /* the import slot as it was */
        for (int i = 0; i < g_nmods; i++) apply(g_mods[i]);
    }
    real(type);
}

static void install(void)
{
    if (GetEnvironmentVariableA("PR2LOADER_OFF", NULL, 0)) { lg("PR2LOADER_OFF set: the game starts without mods"); return; }
    if (!read_mods()) return;
    if (!readable(g_hook_slot, 4)) { lg("the trigger slot %08X is not in this exe: the game starts without mods", g_hook_slot); return; }
    g_set_app_type = *(SetAppType_t *)(uintptr_t)g_hook_slot;
    SetAppType_t mine = on_set_app_type;
    write_mem(g_hook_slot, &mine, 4);
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID res)
{
    (void)res;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        DWORD n = GetModuleFileNameA(NULL, g_dir, MAX_PATH);
        while (n && g_dir[n - 1] != '\\') n--;
        g_dir[n] = 0;
        install();
    }
    return TRUE;
}
