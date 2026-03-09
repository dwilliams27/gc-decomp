"""Exhaustive declaration order search for fn_802590C4.

Tests all 720 permutations of the 6 local variable declarations
and reports the best match percentage.
"""
import itertools
import re
import sys
import time

sys.path.insert(0, "/Users/dwilliams/proj/gc-decomp/src")

from decomp_agent.config import load_config
from decomp_agent.tools.build import check_match
from decomp_agent.tools.source import read_source_file, write_source_file

FUNC_NAME = "fn_802590C4"
SOURCE_FILE = "melee/mn/mngallery.c"

# The 6 variable declarations (each is a complete line)
DECL_LINES = [
    "    void* store;",
    "    void* ud;",
    "    struct fn_802590C4_data* data;",
    "    s32 i;",
    "    s32 zero;",
    "    HSD_JObj* jobj;",
]

# Short names for display
DECL_NAMES = ["store", "ud", "data", "i", "zero", "jobj"]


def main():
    cfg = load_config()
    src_path = cfg.melee.resolve_source_path(SOURCE_FILE)
    original = read_source_file(src_path)

    # Find the declaration block in the function
    original_block = "\n".join(DECL_LINES)
    if original_block not in original:
        print("ERROR: Could not find declaration block in source file.")
        print("Expected:")
        print(original_block)
        sys.exit(1)

    best_pct = 0.0
    best_order = None
    results = []
    total = 720  # 6!

    print(f"Testing all {total} declaration orders for {FUNC_NAME}...")
    print(f"Original order: {DECL_NAMES}")
    print()

    t0 = time.time()
    for idx, perm in enumerate(itertools.permutations(range(6))):
        # Build new declaration block
        new_lines = [DECL_LINES[p] for p in perm]
        new_block = "\n".join(new_lines)
        names = tuple(DECL_NAMES[p] for p in perm)

        # Replace in source
        modified = original.replace(original_block, new_block)
        write_source_file(src_path, modified)

        # Compile and check
        try:
            result = check_match(SOURCE_FILE, cfg)
            func = result.get_function(FUNC_NAME)
            if func:
                pct = func.fuzzy_match_percent
            else:
                pct = 0.0
        except Exception as e:
            pct = 0.0

        results.append((pct, names))

        if pct > best_pct:
            best_pct = pct
            best_order = names
            print(f"  New best: {pct:.1f}% with {names}")

        if (idx + 1) % 60 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed
            remaining = (total - idx - 1) / rate
            print(f"  Tested {idx + 1}/{total}, best so far: {best_pct:.1f}%, "
                  f"~{remaining:.0f}s remaining")

    # Restore original
    write_source_file(src_path, original)

    elapsed = time.time() - t0
    print(f"\n=== RESULT: tested {total} orderings in {elapsed:.0f}s ===")
    print(f"Best: {best_pct:.1f}% with order {best_order}")

    # Show top 10
    results.sort(key=lambda x: -x[0])
    print(f"\nTop 10:")
    for pct, names in results[:10]:
        print(f"  {pct:.1f}% — {names}")

    # Check for regressions on other functions with best order
    if best_order and best_pct > 82.3:
        print(f"\nApplying best order and checking all functions...")
        new_lines = [DECL_LINES[DECL_NAMES.index(n)] for n in best_order]
        new_block = "\n".join(new_lines)
        modified = original.replace(original_block, new_block)
        write_source_file(src_path, modified)
        result = check_match(SOURCE_FILE, cfg)
        for f in result.functions:
            status = "MATCH" if f.is_matched else f"{f.fuzzy_match_percent:.1f}%"
            print(f"  {f.name}: {status}")
        write_source_file(src_path, original)


if __name__ == "__main__":
    main()
