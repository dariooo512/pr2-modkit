// Ghidra headless script: find every function that references any of the named symbols
// (imports such as CreateFileW or socket, or any other named function or label), then write
// a report: the list of those functions with the symbols each uses, followed by the
// decompiled C of each one and of its callers up to N levels.
//
// A reference from outside any function (an import thunk, a function-pointer table) is
// followed one more step to the functions that reference it.
//
// Arguments:  <out file> [depth=N] <symbol> [<symbol> ...]
//   depth=N   how many caller levels to decompile above each user (default 1)
//
// Usage (through the kit's wrapper, which makes the out path absolute):
//   python tools/ghidra/ghidra.py apicalls --out net.txt socket connect sendto recvfrom
//   python tools/ghidra/ghidra.py apicalls --depth 2 --out files.txt CreateFileA WriteFile
// Static only: nothing is executed.
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class ApiCalls extends GhidraScript {
    private int maxDepth = 1;

    public void run() throws Exception {
        String[] a = getScriptArgs();
        if (a.length < 2) {
            println("usage: ApiCalls.java <out file> [depth=N] <symbol> [<symbol> ...]");
            return;
        }
        String out = a[0];
        List<String> names = new ArrayList<>();
        for (int i = 1; i < a.length; i++) {
            if (a[i].startsWith("depth=")) maxDepth = Integer.parseInt(a[i].substring(6));
            else names.add(a[i]);
        }
        PrintWriter w = new PrintWriter(new FileWriter(out));
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        Map<Function, Set<String>> users = new LinkedHashMap<>();
        SymbolTable st = currentProgram.getSymbolTable();
        int symbolsFound = 0;
        for (String n : names) {
            for (Symbol s : st.getSymbols(n)) {
                symbolsFound++;
                for (Reference r : getReferencesTo(s.getAddress())) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    if (f == null) {
                        // thunk or pointer slot: follow the references to that slot
                        for (Reference r2 : getReferencesTo(r.getFromAddress())) {
                            Function f2 = getFunctionContaining(r2.getFromAddress());
                            if (f2 != null) users.computeIfAbsent(f2, k -> new TreeSet<>()).add(n);
                        }
                        continue;
                    }
                    users.computeIfAbsent(f, k -> new TreeSet<>()).add(n);
                }
            }
        }
        w.println("# symbols asked: " + names + "  (" + symbolsFound + " symbol(s) found)");
        w.println("# functions referencing them: " + users.size() + "  (callers decompiled to depth " + maxDepth + ")");
        for (Map.Entry<Function, Set<String>> e : users.entrySet())
            w.println(e.getKey().getEntryPoint() + " " + e.getKey().getName() + " -> " + e.getValue());
        Set<Function> done = new HashSet<>();
        for (Function f : users.keySet()) dump(d, w, f, users.get(f).toString(), 0, done);
        w.close();
        d.dispose();
        println("APICALLS " + users.size() + " function(s) -> " + new File(out).getAbsolutePath());
    }

    void dump(DecompInterface d, PrintWriter w, Function f, String why, int depth, Set<Function> done) {
        if (done.contains(f) || depth > maxDepth) return;
        done.add(f);
        DecompileResults r = d.decompileFunction(f, 120, monitor);
        w.println("\n//==== " + f.getEntryPoint() + " " + f.getName() + "  depth " + depth + "  " + why);
        StringBuilder cs = new StringBuilder();
        for (Function c : f.getCallingFunctions(monitor)) cs.append(c.getEntryPoint()).append(' ');
        w.println("// called from: " + cs);
        w.println(r != null && r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// decompile failed");
        if (depth < maxDepth)
            for (Function c : f.getCallingFunctions(monitor))
                dump(d, w, c, "caller of " + f.getEntryPoint(), depth + 1, done);
    }
}
