#!/usr/bin/env python3
"""Map the Port Royale 2 Lua API.

Sources:
  1. missions.bin  -- compiled Lua 4.1 bytecode; global function names are stored
     as string constants. We pull identifier-like strings out of it.
  2. plain-text scripts (*.lua, *.ini, *.screen) -- we find `Name(` call sites and
     bare identifiers, which reveal engine functions invoked directly.

Output: a categorized list of candidate API symbols with where they were seen, and a
Markdown catalogue grouped by prefix.

    python tools/files/lua_api_map.py [WORKDIR] [-o OUT.md]

WORKDIR is the extracted archives (default PR2_WORKDIR; missions.bin is read from
arcs/lua/data/missions.bin, text scripts from every folder). OUT defaults to
PR2_BUILD/API_MAP.md. The catalogue lists names taken from the game's own files: keep it
for your own use, out of public repositories.
"""
import argparse
import os, re, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

WORK = None

IDENT = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,}")
CALL  = re.compile(r"([A-Za-z_][A-Za-z0-9_]{2,})\s*\(")

# things that are obviously Lua keywords / not engine API
LUA_KW = {
    "and","break","do","else","elseif","end","false","for","function","if","in",
    "local","nil","not","or","repeat","return","then","true","until","while",
    "self","print","type","tostring","tonumber","format","tinsert","tremove",
    "getn","strlen","strsub","strfind","strlower","strupper","gsub","setglobal",
    "getglobal","rawget","rawset","next","pairs","ipairs","assert","error",
}

def strings_from_bin(path):
    with open(path, "rb") as f:
        data = f.read()
    seen = defaultdict(int)
    for m in IDENT.finditer(data):
        s = m.group().decode("latin-1")
        seen[s] += 1
    return seen, len(data)

def calls_from_text(root):
    calls = defaultdict(set)      # name -> set(files)
    files = []
    for dp, _dn, names in os.walk(root):
        for n in names:
            ext = os.path.splitext(n)[1].lower()
            if ext in (".lua", ".ini", ".screen", ".inc"):
                files.append(os.path.join(dp, n))
    for fp in files:
        try:
            txt = open(fp, "r", encoding="latin-1").read()
        except Exception:
            continue
        rel = os.path.relpath(fp, root)
        for m in CALL.finditer(txt):
            calls[m.group(1)].add(rel)
    return calls, len(files)

def classify(name):
    if name in LUA_KW:
        return None
    if name.startswith("lua_") or name.startswith("luaL_"):
        return "lua_c_api"
    if "_" in name and name.split("_")[0][:1].isupper():
        return "engine_namespaced"     # e.g. Global_CreateMission
    if name[0].isupper():
        return "engine_func"           # e.g. SetScrollSpeed, AddTitle
    return "other"

def main(argv=None):
    global WORK
    ap = argparse.ArgumentParser(description="Map the Port Royale 2 Lua API from missions.bin and the text scripts.")
    ap.add_argument("workdir", nargs="?", default=str(kit_paths.WORKDIR))
    ap.add_argument("-o", "--out", type=Path, default=kit_paths.BUILD / "API_MAP.md")
    a = ap.parse_args(argv)
    WORK = a.workdir
    missions = None
    for cand in (os.path.join(WORK, "arcs", "lua", "data", "missions.bin"),):
        if os.path.exists(cand):
            missions = cand
    bin_syms = {}
    if missions:
        bin_syms, blen = strings_from_bin(missions)
        print(f"missions.bin: {blen} bytes, {len(bin_syms)} distinct identifier strings")

    calls, nfiles = calls_from_text(WORK)
    print(f"scanned {nfiles} text scripts; {len(calls)} distinct call names\n")

    # Functions actually CALLED in text scripts are the strongest API signal.
    cat = defaultdict(list)
    for name, where in sorted(calls.items()):
        c = classify(name)
        if not c:
            continue
        in_bin = bin_syms.get(name, 0)
        cat[c].append((name, len(where), in_bin, sorted(where)[:3]))

    order = ["engine_namespaced", "engine_func", "lua_c_api", "other"]
    for c in order:
        if not cat[c]:
            continue
        print(f"===== {c} ({len(cat[c])}) =====")
        for name, nwhere, in_bin, sample in sorted(cat[c], key=lambda t: -t[1]):
            tag = f" [bin:{in_bin}]" if in_bin else ""
            print(f"  {name:<28} files={nwhere}{tag}  e.g. {sample}")
        print()

    # ---- Build a markdown catalog grouped by prefix ----
    # Collect every distinct identifier seen (bin + text), with occurrence count.
    allsyms = dict(bin_syms)
    for n, where in calls.items():
        allsyms.setdefault(n, 0)
        allsyms[n] += 0  # presence

    PREFIXES = [
        ("Global_",  "Global functions (world / mission / timer / RNG)"),
        ("Mission_", "Mission lifecycle"),
        ("Player_",  "Player"),
        ("Town_",    "Town / settlement"),
        ("NPC_",     "NPC"),
        ("Convoy_",  "Convoy / ships"),
        ("Ship_",    "Ship"),
        ("Script_",  "Script registration / timers"),
        ("On",       "Callbacks (mission/NPC/UI event handlers)"),
        ("MSTATE_",  "Mission-state constants"),
        ("DIALOG_",  "Dialog constants"),
        ("ZEIT_",    "Time constants"),
        ("IDPR_",    "String / UI id constants"),
        ("Add",      "Credits/UI builder calls"),
        ("Set",      "Setters"),
        ("Get",      "Getters"),
    ]

    def occ(name):
        return bin_syms.get(name, 0)

    used = set(LUA_KW)
    lines = ["# Port Royale 2 - Lua API map", "",
             "Auto-extracted from `missions.bin` (Lua 4.1 bytecode string table) and",
             "plain-text scripts. `occ` = times the name appears in the bytecode",
             "(rough proxy for how central it is). `[text]` = also called in a",
             "plain-text script we can edit directly.", ""]

    def emit(title, names):
        if not names:
            return
        lines.append(f"## {title}")
        lines.append("")
        for n in sorted(names, key=lambda x: -occ(x)):
            tag = " `[text]`" if n in calls else ""
            lines.append(f"- `{n}` - occ={occ(n)}{tag}")
        lines.append("")

    for pre, title in PREFIXES:
        names = [s for s in allsyms
                 if s not in used and s.startswith(pre)
                 and (s[0].isupper() or pre.endswith("_"))
                 and classify(s) in ("engine_namespaced", "engine_func")]
        # avoid double-listing across overlapping prefixes (e.g. Set/Get vs Script_)
        used.update(names)
        emit(title, names)

    # Anything else capitalized/engine-like not yet bucketed
    rest = [s for s in allsyms
            if s not in used and classify(s) in ("engine_namespaced", "engine_func")]
    emit("Other engine symbols", rest)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {a.out} ({len(lines)} lines)")

if __name__ == "__main__":
    main()
