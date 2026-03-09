#!/usr/bin/env python3
"""Launch two parallel overnight permuter runs for the mngallery.c stuck functions.

Each function gets its own work directory under /tmp/permuter_overnight_<funcname>/
with base.c, target.o, compile.sh, and settings.toml. Then the permuter is launched
as a background process with nohup, logging to permuter.log in each work dir.

Usage:
    python3 scripts/overnight_permuter.py [--workers 8] [--hours 10]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Add the project to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from decomp_agent.config import Config, load_config
from decomp_agent.tools.permuter import (
    _assemble_target,
    _build_compile_sh,
    _extract_mwcc_command,
    _find_permuter,
    _find_strip_other_fns,
    _get_binutils,
    _preprocess_source,
    _SPLICE_HELPER,
)
from decomp_agent.tools.source import read_source_file

FUNCTIONS = [
    ("fn_802590C4", "melee/mn/mngallery.c"),
    ("mnGallery_80259868", "melee/mn/mngallery.c"),
]


def setup_work_dir(
    func_name: str, source_file: str, config: Config, binutils: Path
) -> Path:
    """Create a persistent permuter work directory for one function."""
    work_dir = Path(f"/tmp/permuter_overnight_{func_name}")
    if work_dir.exists():
        print(f"  Cleaning existing {work_dir}")
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    src_path = config.melee.resolve_source_path(source_file)
    source = read_source_file(src_path)

    strip_fns = _find_strip_other_fns()
    assert strip_fns is not None, "strip_other_fns.py not found"

    # 1. Create stripped source (headers + only target function)
    stripped_path = work_dir / f"_stripped_{Path(source_file).stem}.c"
    shutil.copy2(src_path, stripped_path)
    result = subprocess.run(
        ["python3", str(strip_fns), str(stripped_path), func_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"strip_other_fns.py failed: {result.stderr}")

    # 2. Preprocess stripped source into base.c
    preprocessed = _preprocess_source(stripped_path, config, source_file)
    (work_dir / "base.c").write_text(preprocessed, encoding="utf-8")

    # 3. Assemble target asm -> target.o
    from decomp_agent.tools.m2c_tool import get_target_assembly

    target_asm = get_target_assembly(func_name, source_file, config)
    if target_asm is None:
        raise RuntimeError(f"Could not get target assembly for {func_name}")

    target_o = work_dir / "target.o"
    if not _assemble_target(target_asm, func_name, target_o, binutils):
        raise RuntimeError(f"Failed to assemble target for {func_name}")

    # 4. Write splice helper
    splice_helper = work_dir / "_splice.py"
    splice_helper.write_text(_SPLICE_HELPER, encoding="utf-8")

    # 5. Write compile.sh
    compile_script = _build_compile_sh(
        func_name, source_file, config, splice_helper, stripped_path
    )
    compile_sh = work_dir / "compile.sh"
    compile_sh.write_text(compile_script, encoding="utf-8")
    compile_sh.chmod(0o755)

    # 6. Write settings.toml
    (work_dir / "settings.toml").write_text(
        f'func_name = "{func_name}"\ncompiler_type = "mwcc"\n',
        encoding="utf-8",
    )

    return work_dir


def launch_permuter(
    work_dir: Path, permuter_path: Path, binutils: Path, workers: int, hours: float
) -> int:
    """Launch a permuter process in the background with nohup. Returns PID."""
    timeout_secs = int(hours * 3600)
    log_file = work_dir / "permuter.log"

    env = os.environ.copy()
    env["PATH"] = str(binutils) + ":" + env.get("PATH", "")

    # Use gtimeout (GNU coreutils on macOS) to enforce the time limit,
    # nohup to survive terminal close
    timeout_bin = shutil.which("gtimeout") or shutil.which("timeout")
    if timeout_bin is None:
        raise RuntimeError(
            "Neither 'gtimeout' nor 'timeout' found. "
            "Install coreutils: brew install coreutils"
        )
    cmd = (
        f"nohup {timeout_bin} {timeout_secs} "
        f"python3 {permuter_path} {work_dir} "
        f"--stop-on-zero --best-only -j {workers} "
        f"> {log_file} 2>&1 &"
    )

    proc = subprocess.Popen(
        ["bash", "-c", cmd],
        cwd=str(permuter_path.parent),
        env=env,
    )
    proc.wait()  # The bash -c returns immediately since we backgrounded with &

    # Read the PID from the nohup process — find the actual python3 permuter PID
    # by looking for the most recent python3 permuter.py process
    import time
    time.sleep(1)
    ps_result = subprocess.run(
        ["pgrep", "-f", f"permuter.py.*{work_dir.name}"],
        capture_output=True,
        text=True,
    )
    pids = ps_result.stdout.strip().split("\n")
    pid = int(pids[0]) if pids and pids[0] else -1
    return pid


def main():
    parser = argparse.ArgumentParser(description="Overnight permuter for mngallery.c")
    parser.add_argument("--workers", type=int, default=8, help="Workers per permuter")
    parser.add_argument(
        "--hours", type=float, default=10, help="Hours to run (default: 10)"
    )
    args = parser.parse_args()

    print("=== Overnight Permuter Setup ===")
    print(f"Workers per function: {args.workers}")
    print(f"Runtime: {args.hours} hours")
    print()

    # Load config
    config = load_config()

    # Validate tools
    permuter_path = _find_permuter()
    assert permuter_path is not None, "decomp-permuter not found"
    binutils = _get_binutils(config)
    assert binutils is not None, "binutils not found"
    strip_fns = _find_strip_other_fns()
    assert strip_fns is not None, "strip_other_fns.py not found"

    print(f"Permuter: {permuter_path}")
    print(f"Binutils: {binutils}")
    print()

    # Set up work directories
    work_dirs = {}
    for func_name, source_file in FUNCTIONS:
        print(f"Setting up {func_name}...")
        work_dir = setup_work_dir(func_name, source_file, config, binutils)
        work_dirs[func_name] = work_dir
        print(f"  -> {work_dir}")

        # Quick sanity: verify compile.sh works
        print(f"  Sanity check: compiling base.c...")
        test_out = work_dir / "_test.o"
        result = subprocess.run(
            ["bash", str(work_dir / "compile.sh"), str(work_dir / "base.c"), "-o", str(test_out)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(permuter_path.parent),
            env={**os.environ, "PATH": str(binutils) + ":" + os.environ.get("PATH", "")},
        )
        if result.returncode != 0:
            print(f"  FAILED: {result.stderr[:500]}")
            print(f"  stdout: {result.stdout[:500]}")
            sys.exit(1)
        test_out.unlink(missing_ok=True)
        print(f"  OK")

    print()

    # Launch permuters
    pids = {}
    for func_name, work_dir in work_dirs.items():
        print(f"Launching permuter for {func_name}...")
        pid = launch_permuter(
            work_dir, permuter_path, binutils, args.workers, args.hours
        )
        pids[func_name] = pid
        print(f"  PID: {pid}")
        print(f"  Log: {work_dir}/permuter.log")

    print()
    print("=== Both permuters running ===")
    print()
    print("Monitor with:")
    for func_name, work_dir in work_dirs.items():
        print(f"  tail -f {work_dir}/permuter.log")
    print()
    print("Check for improvements:")
    for func_name, work_dir in work_dirs.items():
        print(f"  ls {work_dir}/output-*/source.c 2>/dev/null")
    print()
    print("Kill all:")
    pid_str = " ".join(str(p) for p in pids.values() if p > 0)
    print(f"  kill {pid_str}")
    print()
    print(f"Will auto-stop after {args.hours} hours.")


if __name__ == "__main__":
    main()
