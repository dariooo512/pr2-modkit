// Ghidra headless script: walk every reference (calls, jumps AND data/pointer references)
// upward from the given addresses and print the chain as an indented tree:
//   <addr> <- <caller entry> <caller name> (<ref type> at <from address>)
//   <addr> <- DATA <from address> (<ref type>)      (a reference from outside any function,
//                                                     e.g. a vtable or a pointer table)
// Data references are followed too, so a function reached only through a vtable still shows
// who references the table. Each address is visited once.
//
// Arguments:  <out file> [depth=N] <addr> [<addr> ...]
//   depth=N   how many levels to walk up (default 7)
//
// Usage (through the kit's wrapper, which makes the out path absolute):
//   python tools/ghidra/ghidra.py callers --out who_calls.txt 0x41C6F0
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.address.*;
import java.io.*;
import java.util.*;

public class Callers extends GhidraScript {
    private PrintWriter w;
    private int maxDepth = 7;

    public void run() throws Exception {
        String[] a = getScriptArgs();
        if (a.length < 2) {
            println("usage: Callers.java <out file> [depth=N] <addr> [<addr> ...]");
            return;
        }
        List<String> addrs = new ArrayList<>();
        for (int i = 1; i < a.length; i++) {
            if (a[i].startsWith("depth=")) maxDepth = Integer.parseInt(a[i].substring(6));
            else addrs.add(a[i]);
        }
        w = new PrintWriter(new FileWriter(a[0]));
        Set<Address> seen = new HashSet<>();
        for (String s : addrs) {
            String h = (s.startsWith("0x") || s.startsWith("0X")) ? s.substring(2) : s;
            Address ad = toAddr(Long.parseLong(h, 16));
            Function f = getFunctionContaining(ad);
            w.println("# " + ad + (f != null ? " in " + f.getName() + " @ " + f.getEntryPoint() : " (no function)"));
            walk(ad, 0, seen);
            w.println();
        }
        w.close();
        println("CALLERS -> " + new File(a[0]).getAbsolutePath());
    }

    private void walk(Address ad, int depth, Set<Address> seen) {
        if (depth > maxDepth || !seen.add(ad)) return;
        String ind = "  ".repeat(depth);
        for (Reference r : getReferencesTo(ad)) {
            Function c = getFunctionContaining(r.getFromAddress());
            String kind = r.getReferenceType().toString();
            if (c != null) {
                w.println(ind + ad + " <- " + c.getEntryPoint() + " " + c.getName()
                          + " (" + kind + " at " + r.getFromAddress() + ")");
                walk(c.getEntryPoint(), depth + 1, seen);
            } else {
                w.println(ind + ad + " <- DATA " + r.getFromAddress() + " (" + kind + ")");
                walk(r.getFromAddress(), depth + 1, seen);
            }
        }
    }
}
