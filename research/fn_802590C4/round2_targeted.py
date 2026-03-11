#!/usr/bin/env python3
"""
Round 2 targeted tests for fn_802590C4.
Focus: register pressure tricks, hybrid approaches, and edge cases from round 1.
"""
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

# Find block boundaries
BLOCK_START = "#pragma push\n#pragma optimization_level 2\n#pragma opt_strength_reduction on\n#pragma opt_propagation off\nvoid fn_802590C4"
block_start_idx = original.find(BLOCK_START)
if block_start_idx == -1:
    alt = "#pragma push\nvoid fn_802590C4"
    block_start_idx = original.find(alt)
    if block_start_idx == -1:
        print("ERROR: Cannot find fn_802590C4 block", flush=True)
        sys.exit(1)

block_end_search = original[block_start_idx:]
pop_idx = block_end_search.find("#pragma pop")
block_end_idx = block_start_idx + pop_idx + len("#pragma pop")

BEFORE = original[:block_start_idx]
AFTER = original[block_end_idx:]

# ============================================================
# COMPLETE FUNCTION TEMPLATES
# Each is a full #pragma push ... #pragma pop block
# ============================================================

TESTS = {}

# --- Register pressure: add dummy variables that eat registers ---
# Hypothesis: extra variables change the interference graph, shifting coloring

TESTS["regpressure_1extra"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    void* store;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;
    void* extra;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            store = ud;
            extra = ud;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Array copy with one base, separate read/write array ptrs ---
# Different from array_two_ptr: use ONE base load, then one is base and other is base copy
TESTS["arr_copy_from_base"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    HSD_GObj** read_arr;
    HSD_GObj** write_arr;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            read_arr = ((struct fn_802590C4_data*)gobj->user_data)->gobjs;
            write_arr = read_arr;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(read_arr[i]);
                write_arr[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Different decl orders for array_two_ptr (was 63/30 in round 1) ---
TESTS["arr_two_ptr_reorder1"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    HSD_GObj** write_arr;
    HSD_GObj** read_arr;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            read_arr = ((struct fn_802590C4_data*)ud)->gobjs;
            write_arr = ((struct fn_802590C4_data*)ud)->gobjs;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(read_arr[i]);
                write_arr[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Use data pointer for read, ud for write (not previously tested with O2+SR+prop_off) ---
TESTS["data_read_ud_write"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(data->gobjs[i]);
                ((struct fn_802590C4_data*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Use ud for read, data for write ---
TESTS["ud_read_data_write"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                data->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Interesting: what if store = (void*)data instead of store = ud? ---
# data and ud are the same value but different types. Does the compiler treat them differently?
TESTS["store_from_data"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    void* store;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            store = (void*)data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- store = data (using data pointer directly as the second walker source) ---
# data is already in a register (r28). Using it as one walker base while ud is the other
# might create a different interference graph since data is live across the whole function
TESTS["ud_and_data_walkers"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = (void*)data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)ud)->gobjs[i]);
                data->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- What if we use a DIFFERENT struct member access for read vs write? ---
# Access gobjs via the struct for read, but use an offset-based access for write
# This might create different IR
TESTS["struct_vs_arr_access"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    struct fn_802590C4_data* ud;
    HSD_GObj** arr;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            arr = ud->gobjs;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(ud->gobjs[i]);
                arr[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Reverse: arr for read, struct for write ---
TESTS["arr_read_struct_write"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    struct fn_802590C4_data* ud;
    HSD_GObj** arr;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            arr = ud->gobjs;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(arr[i]);
                ud->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Use JUST the data pointer (already in r28) for read, load new for write ---
# data is r28 (loaded at function entry), ud is loaded fresh before the loop
# The walker bases are different IR-level variables despite same runtime value
TESTS["data_read_freshload_write"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    void* store;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            store = gobj->user_data;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(data->gobjs[i]);
                ((struct fn_802590C4_data*)store)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- Decl order experiments for two_var (best was 50/62) ---
# Try putting ud FIRST, which might change coloring
TESTS["two_var_ud_first"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    void* ud;
    void* store;
    s32 i;
    s32 zero;
    struct fn_802590C4_data* data;
    HSD_JObj* jobj;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
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
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- What if we use O2+SR+prop_off+cs_off for array_two_ptr ---
# AND swap the assignment order?
TESTS["arr_two_ptr_csoff_swap"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
#pragma opt_common_subs off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    HSD_GObj** write_arr;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;
    HSD_GObj** read_arr;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            write_arr = ((struct fn_802590C4_data*)ud)->gobjs;
            read_arr = ((struct fn_802590C4_data*)ud)->gobjs;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(read_arr[i]);
                write_arr[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- What about register keyword? Does MWCC respect it? ---
TESTS["register_vars"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    register void* store;
    struct fn_802590C4_data* data;
    register s32 zero;
    register s32 i;
    HSD_JObj* jobj;
    register void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
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
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

# --- What about using the struct access gobjs field offset via a nested struct? ---
# If gobjs is at a different offset in a differently-padded struct,
# the walker stride or offset changes. Not useful directly but tests the IR path.
# Actually let's try: what if data and ud have DIFFERENT struct types for the cast?
TESTS["different_struct_types"] = """\
#pragma push
#pragma optimization_level 2
#pragma opt_strength_reduction on
#pragma opt_propagation off
void fn_802590C4(HSD_GObj* gobj)
{
    extern f64 mnGallery_804DC368;
    struct fn_802590C4_data {
        u8 pad[0x14]; u8 state; u8 pad2[3]; s32 frame; HSD_GObj* gobjs[2];
    };
    struct fn_802590C4_alt {
        u8 pad[0x1c]; HSD_GObj* gobjs[2];
    };
    void* store;
    struct fn_802590C4_data* data;
    s32 zero;
    s32 i;
    HSD_JObj* jobj;
    void* ud;

    data = gobj->user_data;
    jobj = gobj->hsd_obj;
    if (data->state == 3) { return; }
    if (data->state == 0) {
        if (data->frame < 0x13) { data->frame = data->frame + 1; }
        else { data->state = 1; }
    } else if (data->state == 2) {
        if (data->frame < 0x1D) { data->frame = data->frame + 1; }
        else {
            data->state = 4;
            ud = gobj->user_data;
            store = ud;
            HSD_GObjPLink_80390228(gobj);
            i = 0;
            zero = i;
            do {
                HSD_GObjPLink_80390228(((struct fn_802590C4_data*)store)->gobjs[i]);
                ((struct fn_802590C4_alt*)ud)->gobjs[i] = (HSD_GObj*)zero;
                i++;
            } while (i < 2);
        }
    }
    HSD_JObjReqAnimAll(jobj, (f32)data->frame);
    HSD_JObjAnimAll(jobj);
}
#pragma pop"""

def compile_and_analyze():
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", COMPILE_CMD],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": (r.stdout + r.stderr).strip()[:200], "insns": 0}
    r = subprocess.run(["docker","exec","docker-worker-1","bash","-c", DISASM_CMD],
                       capture_output=True, text=True, timeout=30)
    asm = r.stdout
    hex_list = [h.replace(' ','') for h in re.findall(r'([0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2} [0-9A-F]{2})', asm)]
    if not hex_list:
        return {"error": "no hex output", "insns": 0}
    exact = 0
    for idx in range(min(len(hex_list), len(TARGET_HEX))):
        if hex_list[idx] == TARGET_HEX[idx]:
            exact += 1
        elif (int(hex_list[idx],16) >> 26) in (16,18) and (int(hex_list[idx],16) >> 26) == (int(TARGET_HEX[idx],16) >> 26):
            exact += 1
    destructive = bool(re.search(r'\badd\s+r(\d+),\s*r\1,\s*r\d+', asm))
    walkers = len(re.findall(r'addi\s+r(\d+),\s*r\1,\s*(?:0x)?4\b', asm))
    return {"insns": len(hex_list), "exact": exact, "destructive": destructive, "walkers": walkers, "asm": asm, "hex": hex_list}

print(f"Running {len(TESTS)} round 2 tests...\n", flush=True)
print(f"{'Test':<30} {'Insns':>5} {'Exact':>7} {'Walk':>4} {'Dest':>4} Notes", flush=True)
print("-" * 90, flush=True)

best_exact = 0
try:
    for name, func_block in TESTS.items():
        content = BEFORE + func_block + AFTER
        with open(MNGALLERY, 'w') as f:
            f.write(content)
        result = compile_and_analyze()
        if result.get("error"):
            print(f"{name:<30} ERROR: {result['error'][:60]}", flush=True)
            continue
        n = result["insns"]
        e = result["exact"]
        w = result["walkers"]
        d = "YES" if result["destructive"] else "no"
        notes = ""
        if result["destructive"]:
            notes += " DEST_ADD"
        if e > best_exact:
            best_exact = e
            notes += f" *** BEST {e}/62"
        if n == 62:
            notes += " (62i)"
        if e >= 50 and n == 62:
            # Print hex diff for loop section
            notes += " DETAIL_BELOW"
        print(f"{name:<30} {n:>5} {e:>5}/62 {w:>4} {d:>4}{notes}", flush=True)

        # Print detailed diff for anything >= 50 exact
        if e >= 50 and len(result.get("hex",[])) >= 44:
            print(f"  Loop hex (30-44):", flush=True)
            for idx in range(30, min(45, len(result["hex"]))):
                t = TARGET_HEX[idx] if idx < len(TARGET_HEX) else "--------"
                c = result["hex"][idx]
                match = "==" if t == c else "!="
                print(f"    [{idx:2d}] {t} vs {c} {match}", flush=True)
finally:
    with open(MNGALLERY, 'w') as f:
        f.write(original)
    print("\n[Source restored]", flush=True)

print(f"\nBest exact: {best_exact}/62", flush=True)
print("Done.", flush=True)
