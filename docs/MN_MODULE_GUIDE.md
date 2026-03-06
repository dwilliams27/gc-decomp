# mn/ Module Expert Decomp Guide

Research from deep-reading mndeflicker.c, mnhyaku.c, mnlanguage.c, and all mn/ headers.

---

## mn/ Module Patterns

### GObj Lifecycle (Every Menu File Follows This)

```
Entry Point (e.g., mnHyaku_8024CD64)
  ├─ Load archive sections (joint, animjoint, matanim_joint, shapeanim_joint)
  ├─ Audio setup (lbAudioAx_80026F2C, lbAudioAx_8002702C)
  └─ Call setup function
        ↓
Setup Function (e.g., mnHyaku_8024CB94)
  ├─ GObj_Create(HSD_GOBJ_CLASS_ITEM, 7, 0x80)
  ├─ HSD_JObjLoadJoint(archive_joint_ptr)
  ├─ HSD_GObjObject_80390A70(gobj, HSD_GObj_804D7849, jobj)
  ├─ GObj_SetupGXLink(gobj, HSD_GObj_JObjCallback, 4, 0x80)
  ├─ HSD_JObjAddAnimAll(jobj, animjoint, matanim_joint, shapeanim_joint)
  ├─ HSD_JObjReqAnimAll(jobj, start_frame) + HSD_JObjAnimAll(jobj)
  ├─ Allocate user_data (Menu struct or custom)
  ├─ Register init callback: HSD_GObjProc_8038FD54(gobj, init_cb, 0)
  ├─ Set proc->flags_3 = HSD_GObj_804D783C
  └─ Store gobj in static global
```

### Callback Chain Pattern

Three-phase callback chain used by all mn/ menus:

1. **Init callback** — Checks `mn_804A04F0.cur_menu != MENU_ID`, registers animation callback
2. **Animation callback** — Waits for intro animation to complete, then registers input callback
3. **Input callback** — Handles user input every frame (the main think loop)

Transition between callbacks:
```c
// Remove old callback, register new one
HSD_GObjProc_8038FE24(gobj);  // remove current
HSD_GObjProc_8038FD54(gobj, new_callback, 0);  // register new
gobj->proc->flags_3 = HSD_GObj_804D783C;
```

### Input Handling Pattern

```c
void mnXXXX_InputHandler(HSD_GObj* gobj) {
    Menu* menu = GET_MENU(gobj);

    // Throttle check
    if (mn_804D6BC8.cooldown != 0) {
        Menu_DecrementAnimTimer();
        return;
    }

    // Get inputs
    u64 events = Menu_GetAllInputs();

    // Handle back button
    if (events & MenuInput_Back) {
        sfxBack();
        mn_80229894(prev_menu_id, cur_menu_id, 0);
        return;
    }

    // Handle navigation (left/right/up/down)
    if (events & MenuInput_Left) {
        sfxMove();
        // Update cursor position
        // Update animation frame
        // Update text display
    }

    // Handle confirm
    if (events & MenuInput_AButton) {
        sfxForward();
        mn_80229860(target_mode);  // or mn_80229894()
    }
}
```

### Animation Control

```c
// Static animation data (always 3 floats)
static AnimLoopSettings intro_anim = { 0.0f, 19.0f, -0.1f };  // no loop
static AnimLoopSettings outro_anim = { 20.0f, 29.0f, -0.1f }; // no loop

// Set animation frame
HSD_JObjReqAnimAll(jobj, frame);

// Configure playback
mn_8022F3D8(jobj, loop_flag, type_mask);
// type_mask: 0x80 = MOBJ, 0x400 = JOBJ, 0xFF = all

// Advance animation
HSD_JObjAnimAll(jobj);

// Apply loop settings
mn_8022ED6C(jobj, &anim_settings);
```

### Text Management

```c
// Always free old text first
if (menu->text != NULL) {
    HSD_SisLib_803A5CC4(menu->text);
}

// Use inline helper
Menu_InitCenterText(menu, text_id);

// Or explicit (when inline breaks float ordering):
menu->text = HSD_SisLib_803A5ACC(0, 1, -9.5f, 9.1f, 17.0f, 364.68332f, 38.38772f);
menu->text->font_size.x = menu->text->font_size.y = 0.0521f;
HSD_SisLib_803A6368(menu->text, text_id);
```

### Archive Loading

```c
// All menus share archive: mn_804D6BB8
// Load 4 resources per menu element
lbArchive_LoadSections(mn_804D6BB8, (void**)&data,
    "MenMainConXX_Top_joint",
    "MenMainConXX_Top_animjoint",
    "MenMainConXX_Top_matanim_joint",
    "MenMainConXX_Top_shapeanim_joint",
    NULL);
// XX = 2-letter code (Df=deflicker, Hy=hyaku, La=language)
```

### Static Globals Pattern

Every mn/ file has:
```c
static HSD_GObj* mnXXXX_804DXXXX;   // Current GObj
static u8 mnXXXX_804DXXXX;          // State flag (0=disabled, 1=enabled)
static struct { ... } mnXXXX_804AXXXX;  // Archive data (4 pointers)
```

### Menu State (Global)

```c
// MenuFlow - shared across all menus
extern MenuFlow mn_804A04F0;
// .cur_menu   — current menu ID (MenuKind enum)
// .prev_menu  — previous menu for back navigation
// .hovered_selection — cursor position
// .entering_menu — entry flag

// MenuInputState - input throttle
extern MenuInputState mn_804D6BC8;
// .cooldown — frames to wait before accepting input
// .x2, .x4 — cleared on input reset
```

---

## Available Types & Structs

### Menu (0x8 bytes) — Base user_data for simple menus
```c
typedef struct Menu {
    u8 cursor;      // x0: current selection index
    u8 unk1;        // x1: state flag
    u8 unk2;        // x2: input enable flag
    u8 unk3;        // x3: padding
    HSD_Text* text; // x4: centered text display
} Menu;
```

### AnimLoopSettings (0xC bytes)
```c
typedef struct AnimLoopSettings {
    f32 start_frame;
    f32 end_frame;
    f32 loop_frame;  // -0.1f = no loop
} AnimLoopSettings;
```

### MenuFlow — Global navigation state
```c
typedef struct MenuFlow {
    MenuKind8 cur_menu;
    MenuKind8 prev_menu;
    u16 hovered_selection;
    u8 confirmed_selection;
    u8 pad_5[3];
    u64 buttons;
    u8 x10;
    u8 entering_menu;
    u8 light_lerp_frames;
    GXColor* light_color;
} MenuFlow;
```

### Key Enums

```c
// MenuKind — Menu IDs (from forward.h)
MenuKind_MAIN = 0, MenuKind_1P = 1, MenuKind_VS = 2,
MenuKind_SETTINGS = 4, MenuKind_DATA = 5,
MenuKind_EVENT = 7, MenuKind_STADIUM = 8,
// ... up to 34 values

// MenuState
MenuState_IDLE = 0, MenuState_ENTER_TO = 1,
MenuState_EXIT_FROM = 2, MenuState_EXIT_TO = 3,
MenuState_ENTER_FROM = 4

// MenuInput (from inlines.h)
MenuInput_Up, MenuInput_Down, MenuInput_Left, MenuInput_Right,
MenuInput_Confirm, MenuInput_Back,
MenuInput_LTrigger, MenuInput_RTrigger, MenuInput_StartButton,
MenuInput_AButton, MenuInput_XButton, MenuInput_YButton
```

### Available Macros & Inlines (from mn/inlines.h)

| Macro/Inline | Purpose |
|---|---|
| `GET_MENU(gobj)` | Cast user_data to Menu* |
| `Menu_DecrementAnimTimer()` | Decrement cooldown timer |
| `Menu_GetAllInputs()` | Get combined input from all ports |
| `Menu_GetInputsForPort(i)` | Get input for specific port |
| `sfxBack()` | Play back SFX |
| `sfxForward()` | Play forward/confirm SFX |
| `sfxMove()` | Play cursor move SFX |
| `Menu_InitCenterText(menu, val)` | Create centered text with standard params |

### Common Include Set

```c
#include <platform.h>
#include <baselib/gobj.h>
#include <baselib/jobj.h>
#include <dolphin/os.h>
#include "mn/inlines.h"
#include "mn/mnmain.h"
#include "mn/types.h"
```

---

## Quality Checklist

Every function must pass all of these before PR submission:

### Merge Blockers
- [ ] No raw pointer arithmetic (`(u8*)ptr + 0xNN`) — use struct fields or M2C_FIELD
- [ ] Correct union member selected (not m2c default)
- [ ] No regressions in other functions (collateral damage check)
- [ ] Not a fake/hacked match

### Review Comment Generators
- [ ] No magic numbers — use enums (`MenuKind_EVENT` not `7`)
- [ ] No unnecessary casts (Claude over-generates these)
- [ ] No `var_*` variable names — use meaningful names (`i`/`j` for indices)
- [ ] Uses macros: `GET_MENU(gobj)` not `gobj->user_data`
- [ ] `true`/`false` for bool returns, not `1`/`0`
- [ ] Single struct assignment, not field-by-field copy
- [ ] Chained zero assignments: `x = y = z = 0.0F`
- [ ] No match % comments
- [ ] TODOs use `/// @todo` format
- [ ] Helper functions used where available (e.g., `sfxBack()` not raw audio call)

---

## m2c Flag Reference for Menu Code

### Baseline Flags (always use)
```
--knr --pointer left --target ppc-mwcc-c
```

### Recommended Additional Flags
```
--globals=none     # Cleaner output, fewer wrong global references
--no-casts         # Reduce unnecessary casts (major reviewer complaint)
--stack-structs    # Infer Vec3/struct stack copies → single assignments
```

### When Needed
```
--union-field StructName:member_name    # When m2c picks wrong union variant
--void-field-type Struct.field:TypeName  # When void* needs specific cast
```

---

## Common Mismatch Patterns & Fixes

| Assembly Diff | Cause | Fix |
|---|---|---|
| Stack size differs | Missing padding or variable | Add `PAD_STACK(n)` or reuse variable |
| `lfs/stfs` vs `lwz/stw` | Field-by-field copy vs struct assign | Use single struct assignment `*dst = *src` |
| Extra `mr` instructions | Unnecessary temp variables | Inline the expression |
| Wrong branch targets | Control flow structure wrong | Restructure if/else or switch |
| Missing `frsp` | Float precision | Use `f32` cast or `F` suffix on literals |
| Register allocation differs | Variable declaration order | Reorder declarations to match |

---

## Target File Notes

### mnevent.c (14 functions, empty)
- Event match menu — selects event match mode
- Has 13 function declarations in mnevent.h (mostly UNK_RET)
- Archive prefix likely: "MenMainConEv" or similar
- Menu ID: MenuKind_EVENT (7)

### mnitemsw.c (10 functions, empty)
- Item switch menu — toggles items on/off
- No header content (mnitemsw.h is empty)
- Archive prefix: "MenMainConIs" or similar
- Likely uses toggle/checkbox UI pattern (different from cursor selection)

### mngallery.c (2 remaining functions)
- Trophy gallery viewer
- Already has: memory allocator (2.44MB), cleanup callback
- Large memory footprint suggests complex visual content
- 9 functions declared in mngallery.h
- Has its own data struct: mnGallery_804A0B90_t (0x96000 bytes!)

---

## Reference: Matched mn/ Files

| File | Functions | Lines | Key Pattern |
|---|---|---|---|
| mndeflicker.c | 6 | 204 | Simple up/down toggle, basic text display |
| mnhyaku.c | 9 | 235 | Horizontal 6-item selection, audio setup |
| mnlanguage.c | 7 | 223 | Binary toggle, custom user_data struct |

Use these as templates when implementing target files. The patterns are nearly identical — only menu IDs, asset names, input mappings, and state machines differ.
