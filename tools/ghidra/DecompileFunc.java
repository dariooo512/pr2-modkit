// Ghidra headless script: print the decompiled pseudo-C of the function(s) containing the
// given virtual addresses. When an address is not inside a known function (for example in a
// project imported without analysis), it disassembles there and creates one.
//
// Usage (through the kit's wrapper):
//   python tools/ghidra/ghidra.py decompile 0x41C6F0 0x4D5AD0
// or directly:
//   analyzeHeadless <projDir> <projName> -process <program> -noanalysis \
//       -scriptPath tools/ghidra -postScript DecompileFunc.java 0x41C6F0
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.address.Address;
import ghidra.util.task.ConsoleTaskMonitor;

public class DecompileFunc extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length == 0) {
            println("usage: DecompileFunc.java <VA> [<VA> ...]   (hex, with or without 0x)");
            return;
        }
        DecompInterface dec = new DecompInterface();
        dec.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        for (String a : args) {
            String h = a.startsWith("0x") || a.startsWith("0X") ? a.substring(2) : a;
            Address addr = toAddr(Long.parseLong(h, 16));
            Function fn = fm.getFunctionContaining(addr);
            if (fn == null) {
                disassemble(addr);
                fn = createFunction(addr, null);
            }
            if (fn == null) { println("// NO FUNCTION at " + a + " (could not create)"); continue; }
            DecompileResults res = dec.decompileFunction(fn, 90, mon);
            println("\n// ===== " + fn.getName() + " @ " + fn.getEntryPoint() + " (req " + a + ") =====");
            if (res != null && res.decompileCompleted())
                println(res.getDecompiledFunction().getC());
            else
                println("// decompile failed: " + (res != null ? res.getErrorMessage() : "null"));
        }
        dec.dispose();
    }
}
