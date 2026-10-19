// Ghidra headless script: for each VA argument, report whether the function containing it is
// referenced at any address OTHER than its entry point (jumped or called into mid-body).
//
// Such functions are unsafe to replace in place with a body of different length: an inbound
// branch to a mid-body address would land on changed bytes or padding and crash. Functions
// referenced only at their entry point are safe to replace.
//
// Prints one line per VA:  MIDREF <va> mid=<n> entry=<n> safe|UNSAFE   (or NOFUNC)
// Usage: python tools/ghidra/ghidra.py midbody 0x41C6F0 0x4D5AD0
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;

public class MidbodyRefs extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length == 0) {
            println("usage: MidbodyRefs.java <VA> [<VA> ...]   (hex, with or without 0x)");
            return;
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        for (String a : args) {
            String h = (a.startsWith("0x") || a.startsWith("0X")) ? a.substring(2) : a;
            Address addr = toAddr(Long.parseLong(h, 16));
            Function fn = fm.getFunctionContaining(addr);
            if (fn == null) { println("MIDREF " + a + " NOFUNC"); continue; }
            AddressSetView body = fn.getBody();
            int midRefs = 0, entryRefs = 0;
            AddressIterator it = rm.getReferenceDestinationIterator(body, true);
            while (it.hasNext()) {
                Address dst = it.next();
                for (Reference r : rm.getReferencesTo(dst)) {
                    boolean srcInside = body.contains(r.getFromAddress());
                    if (srcInside) continue;                 // internal branches do not count
                    if (dst.equals(fn.getEntryPoint())) entryRefs++;
                    else midRefs++;                          // inbound ref to a non-entry address
                }
            }
            println("MIDREF " + a + " mid=" + midRefs + " entry=" + entryRefs
                    + (midRefs > 0 ? " UNSAFE" : " safe"));
        }
    }
}
