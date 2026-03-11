#!/usr/bin/env python3
"""
Declaration order sweep for data_read_ud_write variant.
This variant gives 2 walkers + destructive add + 62 insns.
Sweep all permutations of the 5 local variables to find the best register assignment.
"""
import subprocess, re, sys, itertools

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

BLOCK_START = "#pragma push\n#pragma optimization_level 2\n#pragma opt_strength_reduction on\n#pragma opt_propagation off\nvoid fn_802590C4"
block_start_idx = original.find(BLOCK_START)
if block_start_idx == -1:
    alt = "#pragma push\nvoid fn_802590C4"
    block_start_idx = original.find(alt)
    if block_start_idx == -1:
        print("ERROR: Cannot find block", flush=True)
        sys.exit(1)

block_end_search = original[block_start_idx:]
pop_idx = block_end_search.find("#pragma pop")
block_end_idx = block_start_idx + pop_idx + len("#pragma pop")
BEFORE = original[:block_start_idx]
AFTER = original[block_end_idx:]

# Variables to permute (these are the locals that the register allocator assigns)
VAR_DECLS = {
    "data": "    struct fn_802590C4_data* data;",
    "zero": "    s32 zero;",
    "i":    "    s32 i;",
    "jobj": "    HSD_JObj* jobj;",
    "ud":   "    void* ud;",
}

# Also test with different body variants using data+ud
BODY_VARIANTS = {
    "data_read_ud_write": (
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(data->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),
    "ud_read_data_write": (
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                data->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),
    "data_read_freshload_write": (
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(data->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),
}

PRAGMAS = (
    "#pragma optimization_level 2\n"
    "#pragma opt_strength_reduction on\n"
    "#pragma opt_propagation off\n"
)

def make_function(decl_order, body):
    decls = "\n".join(VAR_DECLS[v] for v in decl_order)
    return (
        "#pragma push\n"
        f"{PRAGMAS}"
        "void fn_802590C4(HSD_GObj* gobj)\n"
        "{\n"
        "    extern f64 mnGallery_804DC368;\n"
        "\n"
        "    struct fn_802590C4_data {\n"
        "        u8 pad[0x14];\n"
        "        u8 state;\n"
        "        u8 pad2[3];\n"
        "        s32 frame;\n"
        "        HSD_GObj* gobjs[2];\n"
        "    };\n"
        f"{decls}\n"
        "\n"
        "    data = gobj->user_data;\n"
        "    jobj = gobj->hsd_obj;\n"
        "\n"
        "    if (data->state == 3) {\n"
        "        return;\n"
        "    }\n"
        "\n"
        "    if (data->state == 0) {\n"
        "        if (data->frame < 0x13) {\n"
        "            data->frame = data->frame + 1;\n"
        "        } else {\n"
        "            data->state = 1;\n"
        "        }\n"
        "    } else if (data->state == 2) {\n"
        "        if (data->frame < 0x1D) {\n"
        "            data->frame = data->frame + 1;\n"
        "        } else {\n"
        f"{body}"
        "        }\n"
        "    }\n"
        "\n"
        "    HSD_JObjReqAnimAll(jobj, (f32)data->frame);\n"
        "    HSD_JObjAnimAll(jobj);\n"
        "}\n"
        "#pragma pop"
    )

def compile_and_analyze():
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", COMPILE_CMD],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": (r.stdout + r.stderr)[:200], "insns": 0}
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", DISASM_CMD],
                       capture_output=True, text=True, timeout=30)
    asm = r.stdout
    hex_list = [h.replace(' ','') for h in re.findall(r'([0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2})', asm)]
    if not hex_list:
        return {"error": "no hex", "insns": 0}
    exact = 0
    for idx in range(min(len(hex_list), len(TARGET_HEX))):
        if hex_list[idx] == TARGET_HEX[idx]:
            exact += 1
        elif (int(hex_list[idx],16) >> 26) in (16,18) and (int(hex_list[idx],16) >> 26) == (int(TARGET_HEX[idx],16) >> 26):
            exact += 1
    destructive = bool(re.search(r'\badd\s+r(\d+),\s*r\1,\s*r\d+', asm))
    walkers = len(re.findall(r'addi\s+r(\d+),\s*r\1,\s*(?:0x)?4\b', asm))
    return {"insns": len(hex_list), "exact": exact, "destructive": destructive, "walkers": walkers, "asm": asm, "hex": hex_list}

# All 120 permutations of 5 variables × 3 body variants = 360 tests
var_names = list(VAR_DECLS.keys())
all_perms = list(itertools.permutations(var_names))
total = len(all_perms) * len(BODY_VARIANTS)
print(f"Sweeping {len(all_perms)} declaration orders × {len(BODY_VARIANTS)} body variants = {total} tests", flush=True)
print(f"{'Body':<28} {'Decl Order':<30} {'Insns':>5} {'Exact':>7} {'W':>2} {'D':>2}", flush=True)
print("-" * 85, flush=True)

best_exact = 0
best_configs = []
all_results = []

try:
    test_num = 0
    for body_name, body_text in BODY_VARIANTS.items():
        for perm in all_perms:
            test_num += 1
            func_block = make_function(perm, body_text)
            content = BEFORE + func_block + AFTER
            with open(MNGALLERY, 'w') as f:
                f.write(content)
            result = compile_and_analyze()
            if result.get("error"):
                continue
            n = result["insns"]
            e = result["exact"]
            w = result["walkers"]
            d = 1 if result["destructive"] else 0

            all_results.append((body_name, perm, n, e, w, d, result))

            if e > best_exact:
                best_exact = e
                best_configs = [(body_name, perm, n, e, w, d)]
                order_str = ",".join(perm)
                print(f"{body_name:<28} {order_str:<30} {n:>5} {e:>5}/62 {w:>2} {d:>2} *** NEW BEST!", flush=True)
            elif e == best_exact:
                best_configs.append((body_name, perm, n, e, w, d))

            # Print progress every 30 tests
            if test_num % 30 == 0:
                print(f"  [{test_num}/{total}] best so far: {best_exact}/62", flush=True)

finally:
    with open(MNGALLERY, 'w') as f:
        f.write(original)
    print("\n[Source restored]", flush=True)

print(f"\n{'='*85}", flush=True)
print(f"BEST EXACT: {best_exact}/62", flush=True)
print(f"Configs achieving best:", flush=True)
for cfg in best_configs[:20]:
    body_name, perm, n, e, w, d = cfg
    print(f"  {body_name} [{','.join(perm)}] {n}i {e}/62 walk={w} dest={d}", flush=True)

# Show distribution
from collections import Counter
exact_dist = Counter(r[3] for r in all_results)
print(f"\nExact match distribution:", flush=True)
for score in sorted(exact_dist.keys(), reverse=True):
    print(f"  {score}/62: {exact_dist[score]} tests", flush=True)

# Show all results with destructive add + 62 insns
print(f"\nAll results with 62 insns + destructive add:", flush=True)
for body_name, perm, n, e, w, d, result in all_results:
    if n == 62 and d == 1:
        order_str = ",".join(perm)
        print(f"  {body_name} [{order_str}] {e}/62 walk={w}", flush=True)

# Print hex diff for the absolute best result
if best_configs:
    body_name, perm, n, e, w, d = best_configs[0]
    # Re-compile to get the asm
    func_block = make_function(perm, BODY_VARIANTS[body_name])
    content = BEFORE + func_block + AFTER
    with open(MNGALLERY, 'w') as f:
        f.write(content)
    result = compile_and_analyze()
    with open(MNGALLERY, 'w') as f:
        f.write(original)

    if result.get("hex"):
        print(f"\nBest result hex diff (full):", flush=True)
        for idx in range(min(len(result["hex"]), len(TARGET_HEX))):
            t = TARGET_HEX[idx]
            c = result["hex"][idx]
            match = "==" if t == c else "!="
            if match == "!=":
                print(f"  [{idx:2d}] {t} vs {c} {match}", flush=True)

        # Print the loop asm section
        print(f"\nBest result loop asm:", flush=True)
        printing = False
        for line in result["asm"].strip().split('\n'):
            if 'stb' in line and '0x14' in line:
                printing = True
            if printing:
                print(f"  {line.strip()}", flush=True)
            if 'blt' in line and printing:
                break

print("\nDone.", flush=True)
