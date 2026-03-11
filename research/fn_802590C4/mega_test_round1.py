#!/usr/bin/env python3
"""
Comprehensive fn_802590C4 test: body variants × pragma combos.
Key hypothesis: single-variable + CSE off → two walkers from same base → destructive add.
"""
import subprocess, re, sys, time

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

# Read original file
with open(MNGALLERY) as f:
    original = f.read()

# Find the pragma push ... pragma pop block for fn_802590C4
BLOCK_START = "#pragma push\n#pragma optimization_level 2\n#pragma opt_strength_reduction on\n#pragma opt_propagation off\nvoid fn_802590C4"
block_start_idx = original.find(BLOCK_START)
if block_start_idx == -1:
    # Try finding just pragma push before fn_802590C4
    alt = "#pragma push\nvoid fn_802590C4"
    block_start_idx = original.find(alt)
    if block_start_idx == -1:
        print("ERROR: Cannot find fn_802590C4 block in source", flush=True)
        sys.exit(1)

# Find the matching #pragma pop after this block
block_end_search = original[block_start_idx:]
pop_idx = block_end_search.find("#pragma pop")
if pop_idx == -1:
    print("ERROR: Cannot find #pragma pop", flush=True)
    sys.exit(1)
block_end_idx = block_start_idx + pop_idx + len("#pragma pop")

BEFORE = original[:block_start_idx]
AFTER = original[block_end_idx:]

# ============================================================
# PRAGMA COMBOS
# ============================================================
PRAGMA_COMBOS = {
    "O2_SR_propoff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_propagation off\n"
    ),
    "O2_SR_propoff_csoff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_propagation off\n"
        "#pragma opt_common_subs off\n"
    ),
    "O2_SR_propoff_csoff_ltoff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_propagation off\n"
        "#pragma opt_common_subs off\n"
        "#pragma opt_lifetimes off\n"
    ),
    "O2_SR_propoff_csoff_dcoff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_propagation off\n"
        "#pragma opt_common_subs off\n"
        "#pragma opt_dead_code off\n"
    ),
    "O2_SR_propoff_csoff_lioff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_propagation off\n"
        "#pragma opt_common_subs off\n"
        "#pragma opt_loop_invariants off\n"
    ),
    "O4_csoff": (
        "#pragma opt_common_subs off\n"
    ),
    "O4_csoff_propoff": (
        "#pragma opt_common_subs off\n"
        "#pragma opt_propagation off\n"
    ),
    "O4_propoff": (
        "#pragma opt_propagation off\n"
    ),
    "O2_SR": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
    ),
    "O2_SR_csoff": (
        "#pragma optimization_level 2\n"
        "#pragma opt_strength_reduction on\n"
        "#pragma opt_common_subs off\n"
    ),
    "O3_csoff_propoff": (
        "#pragma optimization_level 3\n"
        "#pragma opt_common_subs off\n"
        "#pragma opt_propagation off\n"
    ),
    "O3_csoff_propoff_ltoff": (
        "#pragma optimization_level 3\n"
        "#pragma opt_common_subs off\n"
        "#pragma opt_propagation off\n"
        "#pragma opt_lifetimes off\n"
    ),
}

# ============================================================
# BODY VARIANTS (else-branch content + variable declarations)
# ============================================================
# Each variant is (declarations, else_body)
# declarations replace the var block; else_body replaces the else { ... } content

COMMON_STRUCT = """\
    extern f64 mnGallery_804DC368;

    struct fn_802590C4_data {
        u8 pad[0x14];
        u8 state;
        u8 pad2[3];
        s32 frame;
        HSD_GObj* gobjs[2];
    };"""

def make_function(pragmas, decls, else_body):
    """Build complete #pragma push ... #pragma pop block."""
    return (
        "#pragma push\n"
        f"{pragmas}"
        "void fn_802590C4(HSD_GObj* gobj)\n"
        "{\n"
        f"{COMMON_STRUCT}\n"
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
        f"{else_body}"
        "        }\n"
        "    }\n"
        "\n"
        "    HSD_JObjReqAnimAll(jobj, (f32)data->frame);\n"
        "    HSD_JObjAnimAll(jobj);\n"
        "}\n"
        "#pragma pop"
    )

BODY_VARIANTS = {
    # V1: Two-var (current committed baseline)
    "two_var": (
        "    void* store;\n"
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
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
    ),

    # V2: Single-var (one pointer for both read and write)
    "single_var": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V3: Single-var, NULL literal (no zero variable)
    "single_var_null": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = NULL;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V4: Array pointer extraction
    "array_extract": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;\n"
        "    HSD_GObj** arr;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            arr = ((struct fn_802590C4_data*)ud)->gobjs;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(arr[i]);\n"
        "                arr[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V5: Using data pointer directly (no ud variable)
    "data_direct": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;",
        "            data->state = 4;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(data->gobjs[i]);\n"
        "                data->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V6: Inline gobj->user_data (no pre-load, let compiler hoist)
    "inline_ud": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;",
        "            data->state = 4;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)gobj->user_data)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)gobj->user_data)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V7: Two-var with swapped roles (store for read, ud for write)
    "two_var_swapped": (
        "    void* store;\n"
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            store = ud;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V8: Single-var, load AFTER the bl (delayed load)
    "single_var_late_load": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            ud = gobj->user_data;\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V9: Array extract with separate arr for read and write
    "array_two_ptr": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;\n"
        "    HSD_GObj** read_arr;\n"
        "    HSD_GObj** write_arr;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            read_arr = ((struct fn_802590C4_data*)ud)->gobjs;\n"
        "            write_arr = ((struct fn_802590C4_data*)ud)->gobjs;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(read_arr[i]);\n"
        "                write_arr[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V10: Single-var with for loop instead of do-while
    "single_var_for": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            zero = 0;\n"
        "            for (i = 0; i < 2; i++) {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "            }\n"
    ),

    # V11: Two-var, copy AFTER bl (not before)
    "two_var_copy_after_bl": (
        "    void* store;\n"
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            store = ud;\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V12: Single-var typed pointer (not void*)
    "single_typed": (
        "    struct fn_802590C4_data* data;\n"
        "    struct fn_802590C4_data* ud;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(ud->gobjs[i]);\n"
        "                ud->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V13: Reload user_data separately for write (two loads from gobj)
    "two_loads": (
        "    void* store;\n"
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            store = gobj->user_data;\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V14: Single-var with zero = 0 (not zero = i)
    "single_var_zero_lit": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = 0;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V15: Three variables — ud for base, store for write walker, read for read walker
    "three_var": (
        "    void* store;\n"
        "    void* read_ptr;\n"
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            store = ud;\n"
        "            read_ptr = ud;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            i = 0;\n"
        "            zero = i;\n"
        "            do {\n"
        "                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)read_ptr)->gobjs[i]);\n"
        "                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;\n"
        "                i++;\n"
        "            } while (i < 2);\n"
    ),

    # V16: Single-var, manually unrolled loop
    "unrolled": (
        "    struct fn_802590C4_data* data;\n"
        "    s32 zero;\n"
        "    s32 i;\n"
        "    HSD_JObj* jobj;\n"
        "    void* ud;",
        "            data->state = 4;\n"
        "            ud = gobj->user_data;\n"
        "            HSD_GObjPLink_80390228(gobj);\n"
        "            zero = 0;\n"
        "            HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[0]);\n"
        "            ((struct fn_802590C4_data*)ud)->gobjs[0] = (HSD_GObj*)zero;\n"
        "            HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[1]);\n"
        "            ((struct fn_802590C4_data*)ud)->gobjs[1] = (HSD_GObj*)zero;\n"
    ),
}

def compile_and_analyze():
    """Compile and disassemble, return analysis dict."""
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", COMPILE_CMD],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        err = (r.stdout + r.stderr).strip()
        return {"error": err[:200], "insns": 0}

    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", DISASM_CMD],
                       capture_output=True, text=True, timeout=30)
    asm = r.stdout

    hex_list = [h.replace(' ','') for h in re.findall(r'([0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2})', asm)]
    if not hex_list:
        return {"error": "no hex output", "insns": 0}

    # Exact match count (ignoring branch relocations)
    exact = 0
    for idx in range(min(len(hex_list), len(TARGET_HEX))):
        if hex_list[idx] == TARGET_HEX[idx]:
            exact += 1
        elif (int(hex_list[idx],16) >> 26) in (16,18) and (int(hex_list[idx],16) >> 26) == (int(TARGET_HEX[idx],16) >> 26):
            exact += 1

    # Check for destructive add: add rN, rN, rM (opcode 31, XO=266)
    destructive_adds = []
    for line in asm.split('\n'):
        m = re.search(r'add\s+r(\d+),\s*r(\d+),\s*r(\d+)', line)
        if m and m.group(1) == m.group(2) and 'addi' not in line.split('#')[0].split('add')[0]:
            # Make sure it's 'add' not 'addi' - check more carefully
            stripped = line.strip()
            if re.match(r'.*\badd\b\s+r(\d+),\s*r\1,\s*r\d+', stripped):
                destructive_adds.append(stripped)

    # Walker count (addi rN, rN, 4)
    walker_incs = []
    for line in asm.split('\n'):
        if re.search(r'addi\s+r(\d+),\s*r\1,\s*(?:0x)?4\b', line):
            walker_incs.append(line.strip())

    # addi rN, rM, 0 patterns (register copies via addi)
    addi_copies = []
    for line in asm.split('\n'):
        m = re.search(r'addi\s+r(\d+),\s*r(\d+),\s*0\b', line)
        if m and m.group(1) != m.group(2):
            addi_copies.append(line.strip())

    return {
        "insns": len(hex_list),
        "exact": exact,
        "destructive_adds": destructive_adds,
        "walkers": len(walker_incs),
        "walker_detail": walker_incs,
        "addi_copies": addi_copies,
        "asm": asm,
        "hex": hex_list,
    }

# ============================================================
# MAIN: Run all combinations
# ============================================================

# Selective matrix — test key combos, not full cross product
# Priority 1: CSE-off hypothesis with single-var bodies
# Priority 2: All bodies with O2+SR+prop_off+cs_off
# Priority 3: Interesting combos for data_direct, array_extract

TEST_MATRIX = []

# All pragma combos with single_var (the KEY hypothesis)
for pragma_name in PRAGMA_COMBOS:
    TEST_MATRIX.append(("single_var", pragma_name))

# All pragma combos with two_var (baseline comparison)
for pragma_name in ["O2_SR_propoff", "O2_SR_propoff_csoff", "O4_csoff", "O4_csoff_propoff"]:
    TEST_MATRIX.append(("two_var", pragma_name))

# CSE-off with all body variants
for body_name in BODY_VARIANTS:
    if body_name not in ("single_var", "two_var"):  # already added above
        TEST_MATRIX.append((body_name, "O2_SR_propoff_csoff"))

# Also test interesting bodies with O2_SR_propoff (for comparison)
for body_name in ["array_extract", "data_direct", "inline_ud", "single_typed",
                   "two_loads", "three_var", "unrolled", "single_var_late_load"]:
    TEST_MATRIX.append((body_name, "O2_SR_propoff"))

# O4 variants for interesting bodies
for body_name in ["single_var", "array_extract", "data_direct", "single_typed"]:
    for pragma_name in ["O4_csoff", "O4_csoff_propoff", "O4_propoff"]:
        if (body_name, pragma_name) not in TEST_MATRIX:
            TEST_MATRIX.append((body_name, pragma_name))

# Deduplicate
seen = set()
deduped = []
for item in TEST_MATRIX:
    if item not in seen:
        seen.add(item)
        deduped.append(item)
TEST_MATRIX = deduped

print(f"Running {len(TEST_MATRIX)} tests...\n", flush=True)
print(f"{'Body':<25} {'Pragmas':<30} {'Insns':>5} {'Exact':>7} {'Walk':>4} {'Dest':>4} {'Copies':>6} Notes", flush=True)
print("-" * 110, flush=True)

results = []
best_exact = 0
best_tests = []

try:
    for body_name, pragma_name in TEST_MATRIX:
        decls, else_body = BODY_VARIANTS[body_name]
        pragmas = PRAGMA_COMBOS[pragma_name]
        func_block = make_function(pragmas, decls, else_body)
        content = BEFORE + func_block + AFTER

        with open(MNGALLERY, 'w') as f:
            f.write(content)

        result = compile_and_analyze()
        result["body"] = body_name
        result["pragmas"] = pragma_name
        results.append(result)

        if result.get("error"):
            print(f"{body_name:<25} {pragma_name:<30} ERROR: {result['error'][:50]}", flush=True)
            continue

        n = result["insns"]
        e = result["exact"]
        w = result["walkers"]
        d = len(result["destructive_adds"])
        c = len(result["addi_copies"])

        notes = ""
        if d > 0:
            notes += " *** DESTRUCTIVE ADD!"
        if e > best_exact:
            best_exact = e
            best_tests = [(body_name, pragma_name)]
            notes += f" *** NEW BEST {e}/{len(TARGET_HEX)}!"
        elif e == best_exact and e > 0:
            best_tests.append((body_name, pragma_name))
        if n == len(TARGET_HEX):
            notes += " (62 insns)"

        print(f"{body_name:<25} {pragma_name:<30} {n:>5} {e:>5}/62 {w:>4} {d:>4} {c:>6}{notes}", flush=True)

finally:
    # ALWAYS restore original
    with open(MNGALLERY, 'w') as f:
        f.write(original)
    print("\n[Source file restored]", flush=True)

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 110, flush=True)
print(f"Best exact match: {best_exact}/{len(TARGET_HEX)}", flush=True)
print(f"Best tests: {best_tests}", flush=True)

# Print detailed asm for any result with destructive add or exact > 50
print("\n" + "=" * 110, flush=True)
print("DETAILED ASM FOR INTERESTING RESULTS:", flush=True)
for result in results:
    if result.get("insns", 0) == 0:
        continue
    interesting = (
        len(result.get("destructive_adds", [])) > 0 or
        result.get("exact", 0) > 50 or
        (result.get("insns") == 62 and result.get("exact", 0) >= 48)
    )
    if not interesting:
        continue

    print(f"\n--- {result['body']} + {result['pragmas']}: {result['insns']} insns, {result['exact']}/62 exact ---", flush=True)
    if result.get("destructive_adds"):
        print(f"  DESTRUCTIVE ADDS: {result['destructive_adds']}", flush=True)
    if result.get("addi_copies"):
        print(f"  ADDI COPIES: {result['addi_copies']}", flush=True)
    if result.get("walker_detail"):
        print(f"  WALKERS: {result['walker_detail']}", flush=True)

    # Print loop section (from stb state=4 to blt)
    lines = result["asm"].strip().split('\n')
    printing = False
    for line in lines:
        if 'stb' in line and '0x14' in line:
            printing = True
        if printing:
            print(f"  {line.strip()}", flush=True)
        if 'blt' in line and printing:
            printing = False
            break

    # Print hex comparison for the loop section (instructions 30-44)
    if result.get("hex") and len(result["hex"]) >= 44:
        print(f"\n  HEX DIFF (insns 30-44):", flush=True)
        for idx in range(30, min(45, len(result["hex"]))):
            t = TARGET_HEX[idx] if idx < len(TARGET_HEX) else "--------"
            c = result["hex"][idx]
            match = "==" if t == c else "!="
            print(f"    [{idx:2d}] target={t} compiled={c} {match}", flush=True)

print("\nDone.", flush=True)
