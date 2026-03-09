# Ghidra headless script: Extract compiler pass markers and xrefs from mwcceppc.exe
# Run with: analyzeHeadless <project_dir> <project_name> -process mwcceppc.exe -postScript ghidra_extract_passes.py
# @category Analysis

from ghidra.program.model.listing import CodeUnit
from ghidra.program.util import DefinedDataIterator
import json

results = {}

# Strings we want to find xrefs for
PASS_MARKERS = [
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
]

SOURCE_FILE_MARKERS = [
    "Coloring.c",
    "InstrSelection.c",
    "Peephole.c",
    "PeepholePPC.c",
    "BackEnd.c",
    "RegisterInfo.c",
    "Scheduler.c",
    "CodeGen.c",
    "IROptimizer.c",
    "IROLinear.c",
    "IRODump.c",
]

STRUCT_FIELD_MARKERS = [
    "fCoalesced",
    "fCoalescedInto",
    "fSpilled",
    "fCalleeSaved",
    "gpr0",
    "gpr31",
    "lbzu",
    "LBZU",
    "dumpir",
]

ALL_TARGETS = PASS_MARKERS + SOURCE_FILE_MARKERS + STRUCT_FIELD_MARKERS

program = currentProgram
listing = program.getListing()
memory = program.getMemory()
refMgr = program.getReferenceManager()
funcMgr = program.getFunctionManager()

def find_string_addr(target):
    """Find address of a defined string in the binary."""
    found = []
    for data in DefinedDataIterator.definedStrings(program):
        val = data.getValue()
        if val is not None and target in str(val):
            found.append((data.getAddress(), str(val)))
    return found

def get_xrefs_to(addr):
    """Get all references to an address."""
    refs = refMgr.getReferencesTo(addr)
    xrefs = []
    for ref in refs:
        from_addr = ref.getFromAddress()
        func = funcMgr.getFunctionContaining(from_addr)
        func_name = func.getName() if func else "unknown"
        func_addr = str(func.getEntryPoint()) if func else "unknown"
        xrefs.append({
            "from_addr": str(from_addr),
            "func_name": func_name,
            "func_entry": func_addr,
            "ref_type": str(ref.getReferenceType()),
        })
    return xrefs

output = {}

for target in ALL_TARGETS:
    addrs = find_string_addr(target)
    if addrs:
        for addr, full_str in addrs:
            xrefs = get_xrefs_to(addr)
            key = target if len(target) < 60 else target[:57] + "..."
            output[key] = {
                "string_addr": str(addr),
                "full_string": full_str,
                "xref_count": len(xrefs),
                "xrefs": xrefs
            }

# Also find ALL source file references (*.c, *.h patterns)
source_files = {}
for data in DefinedDataIterator.definedStrings(program):
    val = str(data.getValue()) if data.getValue() else ""
    if val.endswith(".c") or val.endswith(".h"):
        if "\\" in val or "/" in val or val[0].isupper():
            source_files[val] = str(data.getAddress())

output["__source_files__"] = source_files

# Find the main compiler pipeline by looking at which function references multiple pass markers
pipeline_candidates = {}
for target in PASS_MARKERS:
    addrs = find_string_addr(target)
    for addr, _ in addrs:
        xrefs = get_xrefs_to(addr)
        for xref in xrefs:
            fn = xref["func_name"]
            if fn not in pipeline_candidates:
                pipeline_candidates[fn] = {"entry": xref["func_entry"], "passes": []}
            pipeline_candidates[fn]["passes"].append(target)

# Sort by number of pass markers referenced
pipeline_ranked = sorted(pipeline_candidates.items(), key=lambda x: len(x[1]["passes"]), reverse=True)
output["__pipeline_candidates__"] = {k: v for k, v in pipeline_ranked[:10]}

# Write output
import java.io
outpath = "/tmp/ghidra_mwcc_analysis.json"
writer = java.io.FileWriter(outpath)
writer.write(json.dumps(output, indent=2))
writer.close()
println("Analysis written to " + outpath)
println("Found %d string targets, %d source files" % (len(output) - 2, len(source_files)))
