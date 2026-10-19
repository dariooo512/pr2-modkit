// Ghidra headless script: summarise the function inventory of the analysed program.
// Prints the number of functions, how many are thunks or external, how many are leaves
// (call no other function), a size histogram and the total code bytes.
//
// Usage: python tools/ghidra/ghidra.py count   (needs an analysed project)
//@category PR2Kit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.FunctionIterator;
import java.util.Set;

public class CountFuncs extends GhidraScript {
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        int total = 0, thunks = 0, external = 0, leaf = 0;
        int le32 = 0, le128 = 0, le512 = 0, gt512 = 0;
        long totalBytes = 0;

        FunctionIterator it = fm.getFunctions(true);
        while (it.hasNext()) {
            Function f = it.next();
            total++;
            if (f.isThunk()) thunks++;
            if (f.isExternal()) external++;
            long sz = f.getBody().getNumAddresses();
            totalBytes += sz;
            if (sz <= 32) le32++;
            else if (sz <= 128) le128++;
            else if (sz <= 512) le512++;
            else gt512++;

            Set<Function> callees = f.getCalledFunctions(monitor);
            if (callees == null || callees.isEmpty()) leaf++;
        }

        println("COUNT total=" + total + " thunks=" + thunks + " external=" + external
                + " leaf=" + leaf);
        println("SIZE  <=32B=" + le32 + "  33-128B=" + le128 + "  129-512B=" + le512
                + "  >512B=" + gt512);
        println("BYTES totalCodeBytes=" + totalBytes);
    }
}
