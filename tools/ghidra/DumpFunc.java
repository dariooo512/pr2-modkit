// Ghidra headless script: dump decompiled C, disassembly and raw bytes for the function(s)
// containing the given virtual addresses.
//
// For each requested VA it emits a self-contained block:
//   - the decompiled pseudo-C (Ghidra decompiler)
//   - the instruction listing (address: mnemonic operands)
//   - the function's raw bytes as hex, range by range (for byte diffs and replay oracles)
//
// Output goes to stdout. When the env var PR2_OUTDIR (or the JVM property pr2.outdir) is set,
// each function is also written to <outdir>/0x<VA8>.decomp.txt, where <VA8> is the requested
// address as 8 upper-case hex digits (the name gather_decomp.py looks for).
//
// Usage (through the kit's wrapper, which sets PR2_OUTDIR):
//   python tools/ghidra/ghidra.py dump 0x41C6F0 0x5AB2C0
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.PrintWriter;
import java.io.File;

public class DumpFunc extends GhidraScript {
    private PrintWriter fileOut = null;

    private void emit(String s) {
        println(s);
        if (fileOut != null) fileOut.println(s);
    }

    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length == 0) {
            println("usage: DumpFunc.java <VA> [<VA> ...]   (hex, with or without 0x)");
            return;
        }
        String outDir = System.getProperty("pr2.outdir");
        if (outDir == null) outDir = System.getenv("PR2_OUTDIR");
        if (outDir != null) new File(outDir).mkdirs();

        DecompInterface dec = new DecompInterface();
        DecompileOptions opts = new DecompileOptions();
        dec.setOptions(opts);
        dec.toggleCCode(true);
        dec.toggleSyntaxTree(true);
        dec.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();

        for (String a : args) {
            String h = (a.startsWith("0x") || a.startsWith("0X")) ? a.substring(2) : a;
            long va = Long.parseLong(h, 16);
            if (fileOut != null) { fileOut.close(); fileOut = null; }
            if (outDir != null)
                fileOut = new PrintWriter(new File(outDir, String.format("0x%08X.decomp.txt", va)));

            Address addr = toAddr(va);
            Function fn = fm.getFunctionContaining(addr);
            if (fn == null) {
                disassemble(addr);
                fn = createFunction(addr, null);
            }
            if (fn == null) {
                emit("\n// ##### NO FUNCTION at " + a + " (could not create) #####");
                continue;
            }
            Address entry = fn.getEntryPoint();
            AddressSetView body = fn.getBody();
            long size = body.getNumAddresses();

            emit("// ######################################################################");
            emit("// FUNC " + fn.getName() + " @ " + entry + "  (requested " + a + ")");
            emit("// size=" + size + " bytes in " + body.getNumAddressRanges()
                 + " range(s)  signature: " + fn.getSignature());
            emit("// ######################################################################");

            // 1) Decompiled pseudo-C
            emit("\n// ---- DECOMPILED C ----");
            DecompileResults res = dec.decompileFunction(fn, 120, mon);
            if (res != null && res.decompileCompleted())
                emit(res.getDecompiledFunction().getC());
            else
                emit("// decompile failed: " + (res != null ? res.getErrorMessage() : "null"));

            // 2) Instruction listing
            emit("// ---- DISASSEMBLY ----");
            InstructionIterator it = currentProgram.getListing().getInstructions(body, true);
            while (it.hasNext()) {
                Instruction ins = it.next();
                emit("//   " + ins.getAddress() + ": " + ins.toString());
            }

            // 3) Raw bytes, one line per address range of the body. A function's body need not
            //    be contiguous (shared tails, chunks placed elsewhere), so the bytes are read
            //    range by range, not as `size` bytes from the entry point.
            emit("// ---- RAW BYTES (hex) ----");
            for (AddressRange r : body.getAddressRanges()) {
                StringBuilder sb = new StringBuilder("// ");
                if (body.getNumAddressRanges() > 1)
                    sb.append("[").append(r.getMinAddress()).append("..").append(r.getMaxAddress()).append("] ");
                Address cur = r.getMinAddress();
                try {
                    for (long i = 0; i < r.getLength(); i++) {
                        byte b = getByte(cur);
                        sb.append(String.format("%02x", b & 0xff));
                        cur = cur.add(1);
                    }
                } catch (MemoryAccessException mae) {
                    sb.append("  <memory access error>");
                }
                emit(sb.toString());
            }
            emit("// ---- END " + fn.getName() + " ----\n");
        }
        if (fileOut != null) fileOut.close();
        dec.dispose();
    }
}
