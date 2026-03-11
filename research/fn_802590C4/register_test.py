#!/usr/bin/env python3
"""
Test `register` keyword effects on fn_802590C4.
Key hypothesis: `register` on i and zero shifts allocation from r26/r27 to r29/r30.
Also test: `register` at O4,p might prevent zero=i fold (no pragma workaround needed).
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

def make_function(pragmas, var_lines, else_body):
    return (
        "#pragma push\n"
        f"{pragmas}"
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
        f"{var_lines}\n"
        "\n"
        "    data = gobj->user_data;\n"
        "    jobj = gobj->hsd_obj;\n"
        "\n"
        "    if (data->state == 3) { return; }\n"
        "    if (data->state == 0) {\n"
        "        if (data->frame < 0x13) { data->frame = data->frame + 1; }\n"
        "        else { data->state = 1; }\n"
        "    } else if (data->state == 2) {\n"
        "        if (data->frame < 0x1D) { data->frame = data->frame + 1; }\n"
        "        else {\n"
        f"{else_body}"
        "        }\n"
        "    }\n"
        "    HSD_JObjReqAnimAll(jobj, (f32)data->frame);\n"
        "    HSD_JObjAnimAll(jobj);\n"
        "}\n"
        "#pragma pop"
    )

TWO_VAR_BODY = (
    "            data->state = 4;\n"
    "            ud = gobj->user_data;\n"
    "            store = ud;\n"
    "            HSD_GObjPLink_80390228(gobj);\n"
    "            i = 0;\n"
    "            zero = i;\n"
    "            do {\n"
    "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);\n"
    "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
    "                i++;\n"
    "            } while (i < 2);\n"
)

DATA_UD_BODY = (
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
)

O2_SR_PROPOFF = (
    "#pragma optimization_level 2\n"
    "#pragma opt_strength_reduction on\n"
    "#pragma opt_propagation off\n"
)

O4_NOPRAGMA = ""  # No pragmas = O4,p from command line

O4_CSOFF = "#pragma opt_common_subs off\n"

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
    # Check for addi rN, rM, 0 (zero = i pattern, preserved copy)
    has_addi_copy = bool(re.search(r'addi\s+r\d+,\s*r\d+,\s*0x0\b', asm))
    # Check for li rN, 0 (folded constant)
    li_zero_count = len(re.findall(r'\bli\s+r\d+,\s*0x?0\b', asm))
    return {
        "insns": len(hex_list), "exact": exact, "destructive": destructive,
        "walkers": walkers, "has_addi_copy": has_addi_copy, "li_zeros": li_zero_count,
        "asm": asm, "hex": hex_list
    }

# ============================================================
# TEST MATRIX
# ============================================================

TESTS = []

# --- PART 1: register at O4,p (THE BIG TEST) ---
# Does register prevent zero=i fold at O4?

# All register combos for two_var at O4
reg_vars = ["store", "zero", "i", "ud"]  # variables we can put register on
for r in range(len(reg_vars) + 1):
    for combo in itertools.combinations(reg_vars, r):
        reg_set = set(combo)
        decls = []
        decls.append(f"    {'register ' if 'store' in reg_set else ''}void* store;")
        decls.append(f"    struct fn_802590C4_data* data;")
        decls.append(f"    {'register ' if 'zero' in reg_set else ''}s32 zero;")
        decls.append(f"    {'register ' if 'i' in reg_set else ''}s32 i;")
        decls.append(f"    HSD_JObj* jobj;")
        decls.append(f"    {'register ' if 'ud' in reg_set else ''}void* ud;")
        var_str = "\n".join(decls)
        name = f"O4_reg_{'_'.join(combo) if combo else 'none'}"
        TESTS.append((name, O4_NOPRAGMA, var_str, TWO_VAR_BODY))

# --- PART 2: register at O2+SR+prop_off (shift the register mapping) ---
for r in range(len(reg_vars) + 1):
    for combo in itertools.combinations(reg_vars, r):
        reg_set = set(combo)
        decls = []
        decls.append(f"    {'register ' if 'store' in reg_set else ''}void* store;")
        decls.append(f"    struct fn_802590C4_data* data;")
        decls.append(f"    {'register ' if 'zero' in reg_set else ''}s32 zero;")
        decls.append(f"    {'register ' if 'i' in reg_set else ''}s32 i;")
        decls.append(f"    HSD_JObj* jobj;")
        decls.append(f"    {'register ' if 'ud' in reg_set else ''}void* ud;")
        var_str = "\n".join(decls)
        name = f"O2SR_reg_{'_'.join(combo) if combo else 'none'}"
        TESTS.append((name, O2_SR_PROPOFF, var_str, TWO_VAR_BODY))

# --- PART 3: register + data_read_ud_write (best destructive add variant) ---
# Key combos for data_ud body
for combo in [(), ("i","zero"), ("ud",), ("i","zero","ud"), ("zero",), ("i",),
              ("ud","zero"), ("ud","i"), ("ud","i","zero")]:
    reg_set = set(combo)
    decls = []
    decls.append(f"    struct fn_802590C4_data* data;")
    decls.append(f"    {'register ' if 'zero' in reg_set else ''}s32 zero;")
    decls.append(f"    {'register ' if 'i' in reg_set else ''}s32 i;")
    decls.append(f"    HSD_JObj* jobj;")
    decls.append(f"    {'register ' if 'ud' in reg_set else ''}void* ud;")
    var_str = "\n".join(decls)
    for pragma_name, pragmas in [("O2SR", O2_SR_PROPOFF), ("O4", O4_NOPRAGMA)]:
        name = f"dataUD_{pragma_name}_reg_{'_'.join(combo) if combo else 'none'}"
        TESTS.append((name, pragmas, var_str, DATA_UD_BODY))

# --- PART 4: Best decl order from round 1 (store,data,zero,i,jobj,ud) with register ---
for combo in [("i","zero"), ("store","ud"), ("i","zero","store","ud"), ("zero",), ("i",)]:
    reg_set = set(combo)
    decls = []
    decls.append(f"    {'register ' if 'store' in reg_set else ''}void* store;")
    decls.append(f"    struct fn_802590C4_data* data;")
    decls.append(f"    {'register ' if 'zero' in reg_set else ''}s32 zero;")
    decls.append(f"    {'register ' if 'i' in reg_set else ''}s32 i;")
    decls.append(f"    HSD_JObj* jobj;")
    decls.append(f"    {'register ' if 'ud' in reg_set else ''}void* ud;")
    var_str = "\n".join(decls)
    name = f"bestorder_O2SR_reg_{'_'.join(combo)}"
    TESTS.append((name, O2_SR_PROPOFF, var_str, TWO_VAR_BODY))

print(f"Running {len(TESTS)} register tests...\n", flush=True)
print(f"{'Test':<45} {'Ins':>3} {'Exact':>7} {'W':>1} {'D':>1} {'Addi':>4} {'Li0':>3} Notes", flush=True)
print("-" * 90, flush=True)

best_exact = 0
best_tests = []

try:
    for idx, (name, pragmas, var_str, body) in enumerate(TESTS):
        func_block = make_function(pragmas, var_str, body)
        content = BEFORE + func_block + AFTER
        with open(MNGALLERY, 'w') as f:
            f.write(content)
        result = compile_and_analyze()

        if result.get("error"):
            print(f"{name:<45} ERROR: {result['error'][:40]}", flush=True)
            continue

        n = result["insns"]
        e = result["exact"]
        w = result["walkers"]
        d = 1 if result["destructive"] else 0
        a = 1 if result["has_addi_copy"] else 0
        l = result["li_zeros"]

        notes = ""
        if d: notes += " DEST"
        if a and "O4" in name: notes += " ADDI_PRESERVED!"
        if e > best_exact:
            best_exact = e
            best_tests = [(name, n, e, w, d, a)]
            notes += f" *** BEST {e}/62"
        elif e == best_exact:
            best_tests.append((name, n, e, w, d, a))

        # Only print interesting results or progress
        if e >= 48 or d or (a and "O4" in name) or idx % 20 == 0:
            print(f"{name:<45} {n:>3} {e:>5}/62 {w:>1} {d:>1} {a:>4} {l:>3}{notes}", flush=True)

        if idx % 30 == 29:
            print(f"  [{idx+1}/{len(TESTS)}] best: {best_exact}/62", flush=True)

finally:
    with open(MNGALLERY, 'w') as f:
        f.write(original)
    print("\n[Source restored]", flush=True)

print(f"\n{'='*90}", flush=True)
print(f"BEST: {best_exact}/62", flush=True)
for t in best_tests[:15]:
    name, n, e, w, d, a = t
    print(f"  {name}: {n}i {e}/62 walk={w} dest={d} addi={a}", flush=True)

# Re-compile best to show hex diff
if best_tests:
    name, n, e, w, d, a = best_tests[0]
    # Find matching test
    for tname, pragmas, var_str, body in TESTS:
        if tname == name:
            func_block = make_function(pragmas, var_str, body)
            break
    content = BEFORE + func_block + AFTER
    with open(MNGALLERY, 'w') as f:
        f.write(content)
    result = compile_and_analyze()
    with open(MNGALLERY, 'w') as f:
        f.write(original)

    if result.get("hex"):
        diffs = 0
        print(f"\nHex diff for best ({name}):", flush=True)
        for idx in range(min(len(result["hex"]), len(TARGET_HEX))):
            t = TARGET_HEX[idx]
            c = result["hex"][idx]
            if t != c:
                diffs += 1
                print(f"  [{idx:2d}] {t} vs {c}", flush=True)
        print(f"Total non-matching: {diffs}", flush=True)

        # Print loop asm
        print(f"\nLoop asm:", flush=True)
        printing = False
        for line in result["asm"].strip().split('\n'):
            if 'stb' in line and '0x14' in line:
                printing = True
            if printing:
                print(f"  {line.strip()}", flush=True)
            if 'blt' in line and printing:
                break

print("\nDone.", flush=True)
