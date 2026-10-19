// Ghidra headless script: export every function of the current program as a TSV row
//   addr<TAB>size<TAB>ncalls<TAB>hits<TAB>name
// where `hits` lists every instruction operand whose scalar value equals one of the constants
// given as script arguments, as <hexvalue>@<address>, comma-separated ("-" when none).
//
// Use it to find the code that uses a magic number: a structure size or stride, a grid
// dimension, a cap, a resource id. Constants are hex with 0x, or decimal; a negative decimal
// matches the operand's signed value (so -1 finds 0xFFFFFFFF immediates too).
//
// Writes <PR2_OUTDIR>/<program name>.funcs.tsv and prints the functions with hits plus a total.
// Without PR2_OUTDIR, every row goes to stdout.
//
// Usage:
//   python tools/ghidra/ghidra.py constants 0x1F4 1000 0x5C
//   python tools/ghidra/ghidra.py --program SomeEngine.dll constants 0x5C
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.scalar.Scalar;
import java.io.PrintWriter;
import java.io.File;
import java.util.HashSet;

public class ExportFuncs extends GhidraScript {
    private static long parse(String s) {
        String t = s.trim();
        boolean neg = t.startsWith("-");
        if (neg) t = t.substring(1);
        long v = (t.startsWith("0x") || t.startsWith("0X")) ? Long.parseLong(t.substring(2), 16)
                                                            : Long.parseLong(t);
        return neg ? -v : v;
    }

    public void run() throws Exception {
        HashSet<Long> wanted = new HashSet<Long>();
        for (String a : getScriptArgs()) wanted.add(parse(a));
        if (wanted.isEmpty())
            println("note: no constants given; exporting the function list with empty hits");

        String outDir = System.getenv("PR2_OUTDIR");
        String pname = currentProgram.getName();
        PrintWriter w = null;
        File outFile = null;
        if (outDir != null) {
            new File(outDir).mkdirs();
            outFile = new File(outDir, pname + ".funcs.tsv");
            w = new PrintWriter(outFile);
            w.println("addr\tsize\tncalls\thits\tname");
        }
        Listing lst = currentProgram.getListing();
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        int n = 0, withHits = 0;
        while (it.hasNext()) {
            Function f = it.next();
            Address ep = f.getEntryPoint();
            long size = f.getBody().getNumAddresses();
            int ncalls = 0;
            StringBuilder hits = new StringBuilder();
            InstructionIterator ins = lst.getInstructions(f.getBody(), true);
            while (ins.hasNext()) {
                Instruction in = ins.next();
                if (in.getFlowType().isCall()) ncalls++;
                if (wanted.isEmpty()) continue;
                int nops = in.getNumOperands();
                for (int oi = 0; oi < nops; oi++) {
                    for (Object o : in.getOpObjects(oi)) {
                        if (!(o instanceof Scalar)) continue;
                        Scalar sc = (Scalar) o;
                        long u = sc.getUnsignedValue(), sgn = sc.getSignedValue();
                        if (wanted.contains(u) || wanted.contains(sgn)) {
                            if (hits.length() > 0) hits.append(",");
                            hits.append(Long.toHexString(u)).append("@").append(in.getAddress());
                        }
                    }
                }
            }
            String line = ep + "\t0x" + Long.toHexString(size) + "\t" + ncalls + "\t"
                    + (hits.length() == 0 ? "-" : hits.toString()) + "\t" + f.getName();
            if (hits.length() > 0) withHits++;
            if (w != null) {
                w.println(line);
                if (hits.length() > 0) println(line);
            } else {
                println(line);
            }
            n++;
        }
        if (w != null) w.close();
        println("TOTAL functions: " + n + ", with hits: " + withHits
                + (outFile != null ? ", written to " + outFile.getAbsolutePath() : ""));
    }
}
