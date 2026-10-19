#!/usr/bin/env python3
"""Run the kit's Ghidra scripts headless, against a Ghidra project the kit keeps in _build.

First import (and analyse) the exe once, then run scripts against the saved project; each
run then takes seconds instead of the minutes analysis takes.

    python tools/ghidra/ghidra.py import                     # PR2_BASE_EXE, full analysis
    python tools/ghidra/ghidra.py import --no-analysis       # fast; scripts create functions on demand
    python tools/ghidra/ghidra.py import --exe <path to some.dll>   # another binary, same project
    python tools/ghidra/ghidra.py dump 0x41C6F0              # C + disassembly + bytes -> out/0x0041C6F0.decomp.txt
    python tools/ghidra/ghidra.py decompile 0x41C6F0 0x4D5AD0
    python tools/ghidra/ghidra.py callgraph                  # -> out/worklist.tsv (needs analysis)
    python tools/ghidra/ghidra.py count
    python tools/ghidra/ghidra.py midbody 0x41C6F0
    python tools/ghidra/ghidra.py constants 0x5C 1000        # -> out/<program>.funcs.tsv
    python tools/ghidra/ghidra.py apicalls --out net.txt socket connect
    python tools/ghidra/ghidra.py callers --out who.txt 0x41C6F0
    python tools/ghidra/ghidra.py run MyScript.java arg1 arg2   # any script in tools/ghidra or --script-path

Settings (tools/kit_paths.py): GHIDRA_HOME (the Ghidra folder that holds
support/analyzeHeadless.bat), PR2_BASE_EXE (the binary `import` takes by default), PR2_BUILD
(the project lives in <PR2_BUILD>/ghidra/project, script output in <PR2_BUILD>/ghidra/out).

The output of these scripts is decompiled game code: keep it in _build (git-ignored), never
commit or publish it.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_paths  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_PROJECT_DIR = kit_paths.BUILD / "ghidra" / "project"
DEFAULT_OUT = kit_paths.BUILD / "ghidra" / "out"
DEFAULT_PROJECT_NAME = "PR2"


def headless() -> Path:
    home = kit_paths.need(kit_paths.GHIDRA_HOME, "Ghidra install", "GHIDRA_HOME")
    name = "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"
    return kit_paths.need(home / "support" / name, "analyzeHeadless", "GHIDRA_HOME")


def bat_quote(a: str) -> str:
    """Quote for analyzeHeadless.bat: cmd splits unquoted batch arguments at = ; , and spaces,
    so `depth=2` would arrive as two arguments and a;b script paths as several."""
    if a and not any(c in a for c in ' \t=;,&()^"'):
        return a
    return '"' + a.replace('"', '""') + '"'


def run_headless(args, extra, out_dir: Path) -> int:
    cmd = [str(headless()), str(args.project_dir), args.project_name] + extra
    env = dict(os.environ)
    env["PR2_OUTDIR"] = str(out_dir)          # read by DumpFunc, CallGraph, ExportFuncs
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        line = " ".join(bat_quote(a) for a in cmd)
        if args.verbose:
            print("+ " + line, file=sys.stderr)
        return subprocess.run(line, env=env).returncode
    if args.verbose:
        print("+ " + " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, env=env).returncode


def program_name(args) -> str:
    return args.program or kit_paths.BASE_EXE.name


def script_cmd(args, script: str, script_args) -> list:
    paths = [str(HERE)] + [str(Path(p).resolve()) for p in args.script_path]
    cmd = ["-process", program_name(args), "-noanalysis",
           "-scriptPath", ";".join(paths), "-postScript", script] + list(script_args)
    if args.read_only:
        cmd.append("-readOnly")
    return cmd


def out_file(args, default_name: str) -> str:
    p = Path(args.out_file or default_name)
    if not p.is_absolute():
        p = Path(args.out) / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the kit's Ghidra scripts headless (see the module docstring and tools/ghidra/README.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 2)[1] if __doc__ else None)
    ap.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR,
                    help="Ghidra project folder (default: <PR2_BUILD>/ghidra/project)")
    ap.add_argument("--project-name", default=DEFAULT_PROJECT_NAME,
                    help="Ghidra project name (default: %(default)s)")
    ap.add_argument("--program",
                    help="the imported program to run scripts on, by file name "
                         "(default: the file name of PR2_BASE_EXE)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="output folder, passed to the scripts as PR2_OUTDIR (default: <PR2_BUILD>/ghidra/out)")
    ap.add_argument("--script-path", action="append", default=[],
                    help="extra folder with Ghidra scripts (repeatable); tools/ghidra is always on the path")
    ap.add_argument("--read-only", action="store_true",
                    help="do not save changes to the project (functions the scripts create are not kept)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the analyzeHeadless command line")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="import a binary into the project (once)")
    p.add_argument("--exe", type=Path, help="binary to import (default: PR2_BASE_EXE)")
    p.add_argument("--no-analysis", action="store_true",
                   help="skip auto-analysis (fast; callgraph/count/constants then see few functions)")
    p.add_argument("--overwrite", action="store_true", help="replace a program of the same name")

    for name, hlp in (("dump", "decompiled C + disassembly + raw bytes per function (DumpFunc.java)"),
                      ("decompile", "decompiled C only, to stdout (DecompileFunc.java)"),
                      ("midbody", "is the function referenced mid-body? (MidbodyRefs.java)")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("vas", nargs="+", help="virtual addresses, hex (0x41C6F0)")

    sub.add_parser("callgraph", help="call graph TSV -> <out>/worklist.tsv (CallGraph.java)")
    sub.add_parser("count", help="function inventory summary (CountFuncs.java)")

    p = sub.add_parser("constants", help="functions using given constants -> <out>/<program>.funcs.tsv (ExportFuncs.java)")
    p.add_argument("values", nargs="*", help="constants, 0x hex or decimal")

    p = sub.add_parser("apicalls", help="functions that use the named symbols, decompiled with their callers (ApiCalls.java)")
    p.add_argument("--out-file", default=None, help="report file (relative = under --out; default apicalls.txt)")
    p.add_argument("--depth", type=int, default=1, help="caller levels to decompile (default 1)")
    p.add_argument("symbols", nargs="+", help="symbol names, e.g. CreateFileW socket")

    p = sub.add_parser("callers", help="reference tree upward from addresses (Callers.java)")
    p.add_argument("--out-file", default=None, help="report file (relative = under --out; default callers.txt)")
    p.add_argument("--depth", type=int, default=7, help="levels to walk up (default 7)")
    p.add_argument("vas", nargs="+", help="virtual addresses, hex")

    p = sub.add_parser("run", help="run any Ghidra script on the program")
    p.add_argument("script", help="script file name (in tools/ghidra or a --script-path folder)")
    p.add_argument("script_args", nargs=argparse.REMAINDER, help="arguments for the script")

    args = ap.parse_args()
    args.project_dir = Path(args.project_dir).resolve()
    args.out = Path(args.out).resolve()

    if args.cmd == "import":
        exe = kit_paths.need(args.exe or kit_paths.BASE_EXE, "binary to import",
                             "PR2_BASE_EXE" if args.exe is None else "--exe")
        args.project_dir.mkdir(parents=True, exist_ok=True)
        extra = ["-import", str(Path(exe).resolve())]
        if args.no_analysis:
            extra.append("-noanalysis")
        if args.overwrite:
            extra.append("-overwrite")
        return run_headless(args, extra, args.out)

    if not args.project_dir.exists():
        raise SystemExit(f"no Ghidra project at {args.project_dir}; run `ghidra.py import` first")

    if args.cmd == "dump":
        extra = script_cmd(args, "DumpFunc.java", args.vas)
    elif args.cmd == "decompile":
        extra = script_cmd(args, "DecompileFunc.java", args.vas)
    elif args.cmd == "midbody":
        extra = script_cmd(args, "MidbodyRefs.java", args.vas)
    elif args.cmd == "callgraph":
        extra = script_cmd(args, "CallGraph.java", [])
    elif args.cmd == "count":
        extra = script_cmd(args, "CountFuncs.java", [])
    elif args.cmd == "constants":
        extra = script_cmd(args, "ExportFuncs.java", args.values)
    elif args.cmd == "apicalls":
        extra = script_cmd(args, "ApiCalls.java",
                           [out_file(args, "apicalls.txt"), f"depth={args.depth}"] + args.symbols)
    elif args.cmd == "callers":
        extra = script_cmd(args, "Callers.java",
                           [out_file(args, "callers.txt"), f"depth={args.depth}"] + args.vas)
    else:  # run
        extra = script_cmd(args, args.script, args.script_args)
    return run_headless(args, extra, args.out)


if __name__ == "__main__":
    sys.exit(main())
