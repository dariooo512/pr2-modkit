# Ghidra headless script (Python: Jython or PyGhidra): print the decompiled pseudo-C of the
# function(s) containing the given virtual addresses. Same job as DecompileFunc.java, which
# is the one the kit's wrapper uses; this one is for setups that prefer Python scripts.
#
# Usage:
#   analyzeHeadless <projDir> <projName> -process <program> -noanalysis \
#       -scriptPath tools/ghidra -postScript DecompileFunc.py 0x41C6F0 0x4D5AD0
#@category PR2Kit
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
fm = currentProgram.getFunctionManager()
dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()

if not args:
    print("usage: DecompileFunc.py <VA> [<VA> ...]   (hex, with or without 0x)")

for a in args:
    h = a[2:] if a.lower().startswith("0x") else a
    addr = toAddr(int(h, 16))          # works in Jython (Python 2) and PyGhidra (Python 3)
    fn = fm.getFunctionContaining(addr)
    if fn is None:
        disassemble(addr)
        fn = createFunction(addr, None)
    if fn is None:
        print("// NO FUNCTION at %s (could not create)" % a)
        continue
    res = dec.decompileFunction(fn, 90, mon)
    print("\n// ===== %s @ %s (req %s) =====" % (fn.getName(), fn.getEntryPoint(), a))
    if res and res.decompileCompleted():
        print(res.getDecompiledFunction().getC())
    else:
        print("// decompile failed: %s" % (res.getErrorMessage() if res else "null"))

dec.dispose()
