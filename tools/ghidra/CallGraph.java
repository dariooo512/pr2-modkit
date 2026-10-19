// Ghidra headless script: export the program's call graph as a TSV function list.
//
// One row per function:
//   addr  name  size  depth  nCallees  nCallers  isThunk  hasImportName  nameClass
//
//   addr       entry point, 0x%08X
//   size       bytes in the function body (all ranges)
//   depth      longest chain of in-binary callees (a leaf is 0). Cycle-safe: back edges in
//              recursion contribute 0. Gives a leaves-to-roots order for reconstruction work.
//   nCallees   distinct in-binary functions this one calls
//   nCallers   distinct in-binary functions that call this one (fan-in)
//   nameClass  crt_stl | import | rtti | game: a best-effort tag from the name only. A
//              stripped binary names almost everything FUN_..., so most rows are "game";
//              refine it with your own structural rules if you need a real split.
//
// The file is the function list the Reko drivers (tools/reko/) read: they need addr and size,
// and filter on nameClass.
//
// Output: $PR2_OUTDIR/worklist.tsv (the current directory when PR2_OUTDIR is not set).
// Usage: python tools/ghidra/ghidra.py callgraph   (needs an analysed project)
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.FunctionIterator;
import java.io.PrintWriter;
import java.io.File;
import java.util.*;

public class CallGraph extends GhidraScript {
    private HashMap<Long, int[]> depthMemo = new HashMap<Long, int[]>(); // addr -> {depth}
    private HashMap<Long, ArrayList<Long>> calleeMap = new HashMap<Long, ArrayList<Long>>();

    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();

        // 1) index all functions and their in-binary callees (skip self calls and externals)
        FunctionIterator it = fm.getFunctions(true);
        ArrayList<Function> all = new ArrayList<Function>();
        while (it.hasNext()) all.add(it.next());
        for (Function f : all) {
            long a = f.getEntryPoint().getOffset();
            ArrayList<Long> cs = new ArrayList<Long>();
            Set<Function> callees = f.getCalledFunctions(monitor);
            if (callees != null) {
                for (Function c : callees) {
                    if (c.isExternal()) continue;
                    long ca = c.getEntryPoint().getOffset();
                    if (ca != a) cs.add(ca);
                }
            }
            calleeMap.put(a, cs);
        }

        // 2) longest-callee-chain depth, cycle-safe
        HashSet<Long> onStack = new HashSet<Long>();
        for (Function f : all) computeDepth(f.getEntryPoint().getOffset(), onStack);

        // 3) fan-in counts
        HashMap<Long, Integer> fanIn = new HashMap<Long, Integer>();
        for (Function f : all) fanIn.put(f.getEntryPoint().getOffset(), 0);
        for (Map.Entry<Long, ArrayList<Long>> e : calleeMap.entrySet())
            for (Long c : e.getValue())
                if (fanIn.containsKey(c)) fanIn.put(c, fanIn.get(c) + 1);

        // 4) emit TSV
        String outDir = System.getenv("PR2_OUTDIR");
        File dir = new File(outDir != null ? outDir : ".");
        dir.mkdirs();
        File out = new File(dir, "worklist.tsv");
        PrintWriter pw = new PrintWriter(out);
        pw.println("addr\tname\tsize\tdepth\tnCallees\tnCallers\tisThunk\thasImportName\tnameClass");
        for (Function f : all) {
            long a = f.getEntryPoint().getOffset();
            String name = f.getName();
            long size = f.getBody().getNumAddresses();
            int depth = depthMemo.get(a)[0];
            int nc = calleeMap.get(a).size();
            int fi = fanIn.get(a);
            boolean thunk = f.isThunk();
            boolean importName = isImportName(name);
            String cls = classify(name, thunk, importName);
            pw.println(String.format("0x%08X\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%s",
                a, name, size, depth, nc, fi, thunk ? 1 : 0, importName ? 1 : 0, cls));
        }
        pw.close();
        println("CALLGRAPH wrote " + out.getAbsolutePath() + " (" + all.size() + " functions)");
    }

    private int computeDepth(long a, HashSet<Long> onStack) {
        int[] m = depthMemo.get(a);
        if (m != null) return m[0];
        if (onStack.contains(a)) return 0;          // back edge in a cycle
        onStack.add(a);
        int best = 0;
        ArrayList<Long> cs = calleeMap.get(a);
        if (cs != null) {
            for (Long c : cs) {
                if (!calleeMap.containsKey(c)) continue;
                int d = computeDepth(c, onStack);
                if (d + 1 > best) best = d + 1;
            }
        }
        onStack.remove(a);
        depthMemo.put(a, new int[]{best});
        return best;
    }

    // Names that come from imports or library signatures rather than from the game's own code.
    // PR2 imports lua_* from lua.dll and arc_* from its archive DLL; extend the list for
    // other binaries.
    private boolean isImportName(String n) {
        return n.startsWith("lua_") || n.startsWith("arc_") || n.startsWith("FID_conflict:")
            || n.contains("@")
            || n.equals("timeGetTime");
    }

    private String classify(String n, boolean thunk, boolean importName) {
        String low = n.toLowerCase();
        if (low.contains("basic_string") || low.contains("char_traits")
            || low.contains("allocator") || low.contains("stl::") || low.contains("std::")
            || low.contains("type_info") || low.contains("exception")
            || low.contains("operator") || low.contains("_crt") || low.startsWith("~"))
            return "crt_stl";
        if (importName) return "import";
        if (n.contains("RTTI") || n.contains("`")) return "rtti";
        return "game";
    }
}
