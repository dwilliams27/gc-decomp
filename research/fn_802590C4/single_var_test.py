#!/usr/bin/env python3
"""Test single-variable variants with O2+SR+prop_off to see if two walkers emerge."""
import subprocess, re, sys

MNGALLERY = "/Users/dwilliams/proj/melee-fork/melee/src/melee/mn/mngallery.c"

TARGET_HEX = [
    "7C0802A6","90010004","9421FFD0","BF410018","8383002C","83E30028","881C0014","28000003",
    "418200C4","28000000","40820028","807C0018","2C030013","40800010","38030001","901C0018",
    "48000074","38000001","981C0014","48000068","28000002","40820060","809C0018","2C04001D",
    "40800010","38040001","901C0018","48000048","38000004","981C0014","8363002C","481370E9",
    "3BA00000","57A0103A","3B5B0000","3BDD0000","7F7B0214","807B001C","481370CD","3BBD0001",
    "93DA001C","2C1D0002","3B7B0004","3B5A0004","4180FFE4","809C0018","3C004330","C822C988",
    "387F0000","6C848000","90810014","90010010","C8010010","EC200828","48116721","7FE3FB78",
    "48117785","BB410018","80010034","38210030","7C0803A6","4E800020",
]

COMPILE_CMD = (
    "cd /Users/dwilliams/proj/melee-fork/melee && "
    "build/tools/wibo build/tools/sjiswrap.exe "
    "build/compilers/GC/1.2.5n/mwcceppc.exe "
    "-nowraplines -cwd source -Cpp_exceptions off -proc gekko -fp hardware "
    "-align powerpc -nosyspath -fp_contract on -O4,p -multibyte -enum int "
    "-nodefaults -inline auto "
    '-pragma "cats off" -pragma "warn_notinlined off" '
    "-RTTI off -str reuse -DBUILD_VERSION=0 -DVERSION_GALE01 "
    "-maxerrors 1 -msgstyle std -warn off -warn iserror "
    "-i src -i src/MSL -i src/Runtime -i extern/dolphin/include "
    "-i src/melee -i src/melee/ft/chara -i src/sysdolphin -lang=c "
    "-c src/melee/mn/mngallery.c -o /tmp/_test.o 2>&1"
)

DISASM_CMD = (
    "/usr/local/bin/dtk elf disasm /tmp/_test.o /tmp/_test.s 2>&1 && "
    "sed -n '/.fn fn_802590C4/,/.endfn fn_802590C4/p' /tmp/_test.s"
)

with open(MNGALLERY) as f:
    original = f.read()

# All variants modify the else branch. Find it.
ELSE_START = "            data->state = 4;\n"
ELSE_END = "            } while (i < 2);\n"

# The function body with pragmas + various loop structures
PRAGMAS = "#pragma optimization_level 2\n#pragma opt_strength_reduction on\n#pragma opt_propagation off\n"

# Target marker for pragma insertion
TARGET_MARKER = "#pragma push\nvoid fn_802590C4"
pragma_insert = original.find(TARGET_MARKER) + len("#pragma push\n")

VARIANTS = {
    # V1: Single variable 'ud' for both read and write
    "single_ud": """            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V2: Single variable 'store' for both
    "single_store": """            data->state = 4;
            store = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V3: Two variables but ud loaded AFTER bl (copy-after-bl)
    "ud_after_bl": """            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            store = ud;
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V4: Cast through data pointer (reuse data for both)
    "via_data": """            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                data->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V5: Use data for both read and write
    "both_data": """            data->state = 4;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(data->gobjs[i]);
                data->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V6: ud=load, separate temp for read access
    "temp_read": """            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                store = ((struct fn_802590C4_data*)ud)->gobjs[i];
                HSD_GObjPLink_80390228((HSD_GObj*)store);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V7: Original two-var (reference, should give 50/62)
    "two_var_ref": """            data->state = 4;
            ud = gobj->user_data;
            store = ud;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V8: Assign zero = 0 instead of zero = i (what if the original just used 0?)
    "zero_literal": """            data->state = 4;
            ud = gobj->user_data;
            store = ud;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = 0;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
""",
    # V9: Use NULL directly (no zero variable)
    "null_direct": """            data->state = 4;
            ud = gobj->user_data;
            store = ud;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = NULL;
                i++;
            } while (i < 2);
""",
}

def make_content(variant_body):
    # Insert pragmas
    content = original[:pragma_insert] + PRAGMAS + original[pragma_insert:]
    # Replace else branch body
    start = content.find(ELSE_START)
    end = content.find(ELSE_END)
    if start == -1 or end == -1:
        return None
    end += len(ELSE_END)
    return content[:start] + variant_body + content[end:]

def compile_and_analyze():
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", COMPILE_CMD],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": (r.stdout + r.stderr)[:300], "insns": 0}
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", DISASM_CMD],
                       capture_output=True, text=True, timeout=30)
    hex_list = [h.replace(' ','') for h in re.findall(r'([0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2})', r.stdout)]
    if not hex_list:
        return {"error": "no hex output", "insns": 0}

    exact = 0
    for i in range(min(len(hex_list), len(TARGET_HEX))):
        if hex_list[i] == TARGET_HEX[i]:
            exact += 1
        elif (int(hex_list[i],16) >> 26) in (16,18) and (int(hex_list[i],16) >> 26) == (int(TARGET_HEX[i],16) >> 26):
            exact += 1

    walker_incs = sum(1 for l in r.stdout.split('\n') if re.search(r'addi r\d+, r\d+, 0x4', l))
    has_destructive_add = bool(re.search(r'add r27, r27, r0', r.stdout))

    return {
        "insns": len(hex_list),
        "exact": exact,
        "walkers": walker_incs,
        "destructive_add": has_destructive_add,
        "asm": r.stdout,
    }

print(f"{'Variant':<20} {'Insns':>5} {'Exact':>7} {'Walk':>4} {'Dest':>4}", flush=True)
print("-" * 50, flush=True)

results = {}
for name, body in VARIANTS.items():
    content = make_content(body)
    if content is None:
        print(f"{name:<20} REPLACE FAILED", flush=True)
        continue

    with open(MNGALLERY, 'w') as f:
        f.write(content)

    result = compile_and_analyze()
    results[name] = result

    if result.get("error"):
        print(f"{name:<20} ERROR: {result['error'][:60]}", flush=True)
    else:
        n = result["insns"]
        e = result["exact"]
        w = result["walkers"]
        d = "YES!" if result["destructive_add"] else "no"
        star = " *** DEST ADD!" if result["destructive_add"] else ""
        if e > 55:
            star = f" *** {e}/62!"
        print(f"{name:<20} {n:>5} {e:>5}/62 {w:>4} {d:>4}{star}", flush=True)

# Restore
with open(MNGALLERY, 'w') as f:
    f.write(original)

# Print loop setup for interesting results
print("\n" + "=" * 60, flush=True)
for name, result in results.items():
    if result.get("insns", 0) > 0 and (result.get("destructive_add") or result.get("exact", 0) > 50 or result.get("insns") == 62):
        print(f"\n--- {name}: {result['insns']} insns, {result['exact']}/62 exact, {result['walkers']} walkers ---", flush=True)
        lines = result["asm"].strip().split('\n')
        # Print from stb 0x14 to blt
        printing = False
        for line in lines:
            if 'stb r0' in line and '0x14' in line:
                printing = True
            if printing:
                print(f"  {line.strip()}", flush=True)
            if 'blt' in line and printing:
                printing = False
                break

print("\nDone.", flush=True)
