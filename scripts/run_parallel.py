"""Run multiple agent attempts in parallel using ThreadPoolExecutor.

Usage:
    python scripts/run_parallel.py [--workers N] [--token-budget N]
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from decomp_agent.agent.loop import run_agent
from decomp_agent.config import load_config


def run_one(name: str, src: str, config) -> dict:
    """Run a single agent attempt and return result dict."""
    t0 = time.monotonic()
    try:
        result = run_agent(name, src, config)
        elapsed = time.monotonic() - t0

        non_cached = result.input_tokens - result.cached_tokens
        cost = (
            non_cached * 1.75
            + result.cached_tokens * 0.175
            + result.output_tokens * 14.00
        ) / 1_000_000

        return {
            "name": name,
            "src": src,
            "matched": result.matched,
            "best_match": result.best_match_percent,
            "reason": result.termination_reason,
            "iterations": result.iterations,
            "elapsed": round(elapsed, 1),
            "total_tokens": result.total_tokens,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cached_tokens": result.cached_tokens,
            "cost": round(cost, 4),
            "error": result.error,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {
            "name": name,
            "src": src,
            "matched": False,
            "best_match": 0.0,
            "reason": "crash",
            "iterations": 0,
            "elapsed": round(elapsed, 1),
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cost": 0.0,
            "error": str(e),
        }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--token-budget", type=int, default=1_000_000)
    args = parser.parse_args()

    config = load_config("config/default.toml")
    config.agent.max_tokens_per_attempt = args.token_budget

    candidates = [
        ("un_80300A88", "melee/if/soundtest.c"),
        ("vi0401_8031D020", "melee/vi/vi0401.c"),
        ("ftKb_SpecialN_800F6388", "melee/ft/chara/ftKirby/ftKb_SpecialN.c"),
        ("fn_801D542C", "melee/gr/grkongo.c"),
        ("it_3F14_Logic5_Spawned", "melee/it/items/ittarucann.c"),
        ("itLinkbomb_UnkMotion3_Anim", "melee/it/items/itlinkbomb.c"),
        ("fn_800DB6C8", "melee/ft/chara/ftCommon/ftCo_Attack100.c"),
        ("ftCo_Damage_OnExitHitlag", "melee/ft/chara/ftCommon/ftCo_Damage.c"),
        ("fn_8024ECCC", "melee/mn/mndatadel.c"),
        ("mpCheckFloor", "melee/mp/mplib.c"),
    ]

    print(
        f"Running {len(candidates)} functions with {args.workers} workers, "
        f"{args.token_budget:,} token budget"
    )
    print()

    results = []
    wall_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_one, name, src, config): (name, src)
            for name, src in candidates
        }
        for future in as_completed(futures):
            name, src = futures[future]
            r = future.result()
            results.append(r)

            status = "MATCHED" if r["matched"] else f'FAILED ({r["reason"]})'
            print(
                f'[{len(results):>2}/{len(candidates)}] {r["name"]:<35} '
                f'{status:<22} best={r["best_match"]:>5.1f}%  '
                f'iters={r["iterations"]:>2}  cost=${r["cost"]:.4f}  '
                f'elapsed={r["elapsed"]:.0f}s'
            )

    wall_elapsed = time.monotonic() - wall_start

    # Summary
    matched = sum(1 for r in results if r["matched"])
    total_cost = sum(r["cost"] for r in results)
    total_tokens = sum(r["total_tokens"] for r in results)
    total_input = sum(r["input_tokens"] for r in results)
    total_output = sum(r["output_tokens"] for r in results)
    total_cached = sum(r["cached_tokens"] for r in results)

    print(f"\n{'='*70}")
    print(f"Matched: {matched}/{len(results)}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Avg cost/attempt: ${total_cost / len(results):.4f}")
    print(f"Total tokens: {total_tokens:,}")
    print(f"Wall time: {wall_elapsed:.0f}s (vs {sum(r['elapsed'] for r in results):.0f}s sequential)")
    print(f"Cache rate: {total_cached / total_input * 100:.1f}%")
    print()

    non_cached = total_input - total_cached
    cost_nc = non_cached * 1.75 / 1_000_000
    cost_c = total_cached * 0.175 / 1_000_000
    cost_o = total_output * 14.00 / 1_000_000
    print(f"Cost breakdown:")
    print(f"  Non-cached input: ${cost_nc:.4f} ({cost_nc/total_cost*100:.1f}%)")
    print(f"  Cached input:     ${cost_c:.4f} ({cost_c/total_cost*100:.1f}%)")
    print(f"  Output:           ${cost_o:.4f} ({cost_o/total_cost*100:.1f}%)")

    # Dump JSON for post-analysis
    print(f"\n{'='*70}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
