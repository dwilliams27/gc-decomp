// Ghidra script: Extract compiler pass markers and xrefs from mwcceppc.exe
// @category Analysis
// @author gc-decomp

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.data.*;
import ghidra.program.util.DefinedDataIterator;
import java.io.*;
import java.util.*;

public class ExtractMWCCPasses extends GhidraScript {

    @Override
    public void run() throws Exception {
        StringBuilder out = new StringBuilder();

        String[] passMarkers = {
            "BEFORE SCHEDULING",
            "AFTER INSTRUCTION SCHEDULING",
            "AFTER PEEPHOLE FORWARD",
            "AFTER REGISTER COLORING",
            "AFTER GENERATING EPILOGUE, PROLOGUE",
            "AFTER MERGING EPILOGUE, PROLOGUE",
            "AFTER PEEPHOLE OPTIMIZATION",
            "After IRO_Optimizer",
            "Dumping function %s after %s",
            "Dumps for pass=%d",
            "Before IRO_FindLoops",
            "After IRO_FindLoops",
            "Before IRO_CopyAndConstantPropagation",
            "After IRO_CopyAndConstantPropagation",
            "Before IRO_LoopUnroller",
            "After IRO_LoopUnroller",
            "after IRO_UseDef",
        };

        String[] sourceFileMarkers = {
            "Coloring.c", "InstrSelection.c", "Peephole.c", "PeepholePPC.c",
            "BackEnd.c", "RegisterInfo.c", "Scheduler.c", "CodeGen.c",
            "CodeGenPPC.c", "IROptimizer.c", "IROLinear.c", "IRODump.c",
            "IROFlowgraph.c", "TOC.c", "Operands.c", "UseDefPPC.c",
        };

        String[] structMarkers = {
            "fCoalesced", "fCoalescedInto", "fSpilled", "fCalleeSaved",
            "dumpir", "lbzu", "LBZU", "stbz", "STBZU",
        };

        // Collect all strings and their addresses
        Map<String, List<Address>> stringMap = new HashMap<>();
        Map<String, String> stringFullText = new HashMap<>();
        List<String> sourceFiles = new ArrayList<>();

        println("Scanning defined strings...");
        for (Data data : DefinedDataIterator.definedStrings(currentProgram)) {
            Object val = data.getValue();
            if (val == null) continue;
            String s = val.toString();

            // Check source file references
            if (s.endsWith(".c") || s.endsWith(".h") || s.endsWith(".cpp")) {
                sourceFiles.add(s + " @ " + data.getAddress());
            }

            // Check all target strings
            String[] allTargets = concat(passMarkers, sourceFileMarkers, structMarkers);
            for (String target : allTargets) {
                if (s.contains(target)) {
                    if (!stringMap.containsKey(target)) {
                        stringMap.put(target, new ArrayList<>());
                    }
                    stringMap.get(target).add(data.getAddress());
                    stringFullText.put(target + "@" + data.getAddress(), s);
                }
            }
        }

        // Also search for register name strings
        String[] regNames = {"gpr0", "gpr1", "gpr2", "gpr3", "gpr31", "fpr0", "fpr31", "cr0", "cr7"};
        for (Data data : DefinedDataIterator.definedStrings(currentProgram)) {
            Object val = data.getValue();
            if (val == null) continue;
            String s = val.toString();
            for (String reg : regNames) {
                if (s.equals(reg)) {
                    if (!stringMap.containsKey(reg)) {
                        stringMap.put(reg, new ArrayList<>());
                    }
                    stringMap.get(reg).add(data.getAddress());
                }
            }
        }

        // Get xrefs for each found string
        out.append("=== MWCC Internal Architecture Analysis ===\n\n");

        ReferenceManager refMgr = currentProgram.getReferenceManager();
        FunctionManager funcMgr = currentProgram.getFunctionManager();

        // Track which functions reference which passes (for pipeline detection)
        Map<String, Set<String>> funcToPassRefs = new HashMap<>();

        out.append("== PASS MARKERS ==\n\n");
        for (String marker : passMarkers) {
            List<Address> addrs = stringMap.get(marker);
            if (addrs == null) {
                out.append("  [NOT FOUND] " + marker + "\n");
                continue;
            }
            for (Address addr : addrs) {
                out.append("  \"" + marker + "\" @ " + addr + "\n");
                Reference[] refs = refMgr.getReferencesTo(addr);
                for (Reference ref : refs) {
                    Address fromAddr = ref.getFromAddress();
                    Function func = funcMgr.getFunctionContaining(fromAddr);
                    String funcName = func != null ? func.getName() : "??";
                    String funcEntry = func != null ? func.getEntryPoint().toString() : "??";
                    out.append("    -> " + fromAddr + " in " + funcName + " (entry: " + funcEntry + ")\n");

                    if (!funcToPassRefs.containsKey(funcName)) {
                        funcToPassRefs.put(funcName, new HashSet<>());
                    }
                    funcToPassRefs.get(funcName).add(marker);
                }
            }
            out.append("\n");
        }

        out.append("\n== SOURCE FILE MARKERS ==\n\n");
        for (String marker : sourceFileMarkers) {
            List<Address> addrs = stringMap.get(marker);
            if (addrs == null) {
                out.append("  [NOT FOUND] " + marker + "\n");
                continue;
            }
            for (Address addr : addrs) {
                out.append("  \"" + marker + "\" @ " + addr + "\n");
                Reference[] refs = refMgr.getReferencesTo(addr);
                Set<String> funcsReffingThis = new HashSet<>();
                for (Reference ref : refs) {
                    Address fromAddr = ref.getFromAddress();
                    Function func = funcMgr.getFunctionContaining(fromAddr);
                    String funcName = func != null ? func.getName() : "??";
                    String funcEntry = func != null ? func.getEntryPoint().toString() : "??";
                    if (!funcsReffingThis.contains(funcName)) {
                        out.append("    -> " + fromAddr + " in " + funcName + " (entry: " + funcEntry + ")\n");
                        funcsReffingThis.add(funcName);
                    }
                }
            }
            out.append("\n");
        }

        out.append("\n== STRUCT/FIELD MARKERS ==\n\n");
        String[] allStructMarkers = concat(structMarkers, regNames);
        for (String marker : allStructMarkers) {
            List<Address> addrs = stringMap.get(marker);
            if (addrs == null) {
                out.append("  [NOT FOUND] " + marker + "\n");
                continue;
            }
            for (Address addr : addrs) {
                out.append("  \"" + marker + "\" @ " + addr + "\n");
                Reference[] refs = refMgr.getReferencesTo(addr);
                for (Reference ref : refs) {
                    Address fromAddr = ref.getFromAddress();
                    Function func = funcMgr.getFunctionContaining(fromAddr);
                    String funcName = func != null ? func.getName() : "??";
                    out.append("    -> " + fromAddr + " in " + funcName + "\n");
                }
            }
            out.append("\n");
        }

        // Pipeline detection
        out.append("\n== PIPELINE CANDIDATES (functions referencing multiple pass markers) ==\n\n");
        List<Map.Entry<String, Set<String>>> sorted = new ArrayList<>(funcToPassRefs.entrySet());
        sorted.sort((a, b) -> b.getValue().size() - a.getValue().size());
        for (Map.Entry<String, Set<String>> entry : sorted) {
            if (entry.getValue().size() >= 2) {
                out.append("  " + entry.getKey() + " (" + entry.getValue().size() + " passes):\n");
                for (String pass : entry.getValue()) {
                    out.append("    - " + pass + "\n");
                }
                out.append("\n");
            }
        }

        // Source files list
        out.append("\n== ALL SOURCE FILE REFERENCES ==\n\n");
        Collections.sort(sourceFiles);
        for (String sf : sourceFiles) {
            out.append("  " + sf + "\n");
        }

        // Write output
        String outpath = "/tmp/ghidra_mwcc_analysis.txt";
        FileWriter writer = new FileWriter(outpath);
        writer.write(out.toString());
        writer.close();

        println("Analysis written to " + outpath);
        println("Found " + stringMap.size() + " string targets, " + sourceFiles.size() + " source files");
    }

    private String[] concat(String[]... arrays) {
        int total = 0;
        for (String[] a : arrays) total += a.length;
        String[] result = new String[total];
        int idx = 0;
        for (String[] a : arrays) {
            System.arraycopy(a, 0, result, idx, a.length);
            idx += a.length;
        }
        return result;
    }
}
