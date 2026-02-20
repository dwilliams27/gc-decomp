"""Run decomp-permuter to find matching code permutations for near-matches."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

from decomp_agent.config import Config
from decomp_agent.tools.source import get_function_source, read_source_file


@dataclass
class PermuterResult:
    """Result of running decomp-permuter on a function."""

    function_name: str
    best_score: int | None = None
    best_code: str | None = None
    iterations: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.best_score is not None and self.best_score == 0

    @property
    def improved(self) -> bool:
        return self.best_code is not None


def _find_permuter() -> Path | None:
    """Find the decomp-permuter installation."""
    permuter = shutil.which("permuter.py")
    if permuter:
        return Path(permuter)

    common_paths = [
        Path.home() / "decomp-permuter" / "permuter.py",
        Path.home() / "tools" / "decomp-permuter" / "permuter.py",
    ]
    for p in common_paths:
        if p.exists():
            return p
    return None


def _find_strip_other_fns() -> Path | None:
    """Find strip_other_fns.py from the permuter installation."""
    permuter = _find_permuter()
    if permuter is None:
        return None
    strip = permuter.parent / "strip_other_fns.py"
    return strip if strip.exists() else None


def _get_binutils(config: Config) -> Path | None:
    """Get the path to the project's binutils directory."""
    binutils = config.melee.repo_path / "build" / "binutils"
    return binutils if binutils.is_dir() else None


def _convert_dtk_asm(dtk_asm: str, func_name: str) -> str:
    """Convert DTK assembly output to GNU AS format.

    DTK format:
        .fn lbSnap_8001DF20, global
        /* 8001DF20 0001AB00  7C 08 02 A6 */  mflr r0
        .endfn lbSnap_8001DF20

    GNU AS format:
        .section .text
        .global lbSnap_8001DF20
        lbSnap_8001DF20:
            mflr r0
    """
    lines = [".section .text"]
    for line in dtk_asm.splitlines():
        # .fn name, global -> .global name \n name:
        m = re.match(r"\.fn (\w+),\s*global", line)
        if m:
            name = m.group(1)
            lines.append(f".global {name}")
            lines.append(f"{name}:")
            continue
        # .endfn -> skip
        if line.startswith(".endfn"):
            continue
        # /* addr offset HEX */ instruction -> instruction
        m = re.match(r"/\*.*\*/\s+(.*)", line)
        if m:
            insn = m.group(1).strip()
            if insn:
                lines.append(f"    {insn}")
            continue
        # Keep other non-empty lines
        if line.strip():
            lines.append(line)
    return "\n".join(lines) + "\n"


def _assemble_target(
    dtk_asm: str, func_name: str, output_path: Path, binutils: Path
) -> bool:
    """Assemble DTK assembly into a single-function .o file."""
    gnu_asm = _convert_dtk_asm(dtk_asm, func_name)

    asm_file = output_path.with_suffix(".s")
    asm_file.write_text(gnu_asm, encoding="utf-8")

    assembler = binutils / "powerpc-eabi-as"
    result = subprocess.run(
        [str(assembler), "-mregnames", str(asm_file), "-o", str(output_path)],
        capture_output=True,
        text=True,
    )
    asm_file.unlink(missing_ok=True)
    return result.returncode == 0


def _extract_mwcc_command(source_file: str, config: Config) -> str | None:
    """Extract the MWCC compile command from build.ninja for a source file.

    Parses the ninja build file to find the compiler flags for the
    given source file, then constructs the standalone MWCC command.
    """
    ninja_path = config.melee.repo_path / "build.ninja"
    if not ninja_path.exists():
        return None

    ninja_content = ninja_path.read_text(encoding="utf-8")

    # Resolve $ line continuations: "foo $\n    bar" -> "foo bar"
    ninja_content = re.sub(r"\$\n\s+", " ", ninja_content)

    # Find the build rule for this source file
    target_pattern = source_file.replace(".c", ".o")
    in_target_block = False
    mw_version = None
    cflags = None

    for line in ninja_content.splitlines():
        if f"src/{target_pattern}" in line and line.startswith("build "):
            in_target_block = True
            continue
        if in_target_block:
            stripped = line.strip()
            if line.startswith("  mw_version = "):
                mw_version = line.split("=", 1)[1].strip()
            elif line.startswith("  cflags = "):
                cflags = line.split("=", 1)[1].strip()
            elif stripped == "" or (not line.startswith("  ") and stripped):
                break  # End of build block

    if mw_version is None or cflags is None:
        return None

    repo = config.melee.repo_path
    # Return the raw command string (not split) to preserve quoted args
    # like -pragma "cats off"
    sjiswrap = repo / "build" / "tools" / "sjiswrap.exe"
    compiler = repo / "build" / "compilers" / mw_version / "mwcceppc.exe"
    return f'"{sjiswrap}" "{compiler}" {cflags} -c'


def _extract_cpp_flags(mwcc_cmd: str) -> list[str]:
    """Extract include paths and defines from MWCC command for cpp.

    MWCC uses -i for includes, -D for defines. We convert -i to -I for cpp.
    """
    parts = shlex.split(mwcc_cmd)
    cpp_flags: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.startswith("-D"):
            cpp_flags.append(p)
        elif p == "-i":  # Exactly -i, not -inline etc.
            if i + 1 < len(parts):
                cpp_flags.extend(["-I", parts[i + 1]])
                i += 1
        i += 1
    return cpp_flags


def _preprocess_source(
    stripped_path: Path, config: Config, source_file: str
) -> str:
    """Preprocess stripped source into a self-contained C file for the permuter.

    The permuter's C parser (pycparser) needs all types to be defined.
    We run the C preprocessor to expand all #include directives, producing
    a file with all types inline. We also stub out GCC extensions that
    pycparser can't handle (__attribute__, _Static_assert, etc.).
    """
    mwcc_cmd = _extract_mwcc_command(source_file, config)
    if mwcc_cmd is None:
        raise RuntimeError(
            f"Could not extract MWCC command for {source_file} from build.ninja"
        )

    cpp_flags = _extract_cpp_flags(mwcc_cmd)

    # MWCC's -cwd source resolves quoted includes relative to the source
    # file's directory. Since we're preprocessing a temp copy, we need to
    # explicitly add the original source directory as an include path.
    src_dir = str(Path("src") / Path(source_file).parent)
    cpp_flags = ["-I", src_dir] + cpp_flags

    cpp_cmd = [
        "cc", "-E", "-P", "-nostdinc", "-undef",
        "-DPERMUTER",
        # Stub out GCC extensions that pycparser can't handle
        "-D_Static_assert(x, y)=",
        "-D__attribute__(x)=",
        "-DGLOBAL_ASM(...)=",
        "-D__asm__(...)=",
    ] + cpp_flags + [str(stripped_path)]

    result = subprocess.run(
        cpp_cmd,
        capture_output=True,
        text=True,
        cwd=str(config.melee.repo_path),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to preprocess {stripped_path}: {result.stderr[:500]}"
        )
    return result.stdout


def _build_compile_sh(
    function_name: str,
    source_file: str,
    config: Config,
    splice_helper: Path,
    stripped_src: Path,
) -> str:
    """Generate compile.sh for the permuter.

    The permuter invokes: ./compile.sh <permuted.c> -o <output.o>

    The permuted file is a preprocessed C file (base.c with permutations).
    We extract just the target function from it, splice it into the
    original stripped source (which has proper #includes), and compile
    with MWCC.
    """
    repo = config.melee.repo_path
    src_dir = Path(source_file).parent  # e.g. "melee/lb"
    temp_src_stem = f"_permuter_{Path(source_file).stem}"
    temp_src = f"src/{src_dir}/{temp_src_stem}.c"

    # Extract MWCC command from build.ninja (returns a raw shell string)
    mwcc_line = _extract_mwcc_command(source_file, config)
    if mwcc_line is None:
        raise RuntimeError(
            f"Could not extract MWCC command for {source_file} from build.ninja"
        )

    if config.docker.enabled:
        container = shlex.quote(config.docker.container_name)
        outdir_rel = "build/_permuter_out"
        return f"""#!/bin/bash
set -e

INPUT="$1"
shift; shift
OUTPUT="$1"

REPO="{repo}"
TEMP_SRC="$REPO/{temp_src}"
OUTDIR="$REPO/{outdir_rel}"

# Clean up on exit
trap 'rm -f "$TEMP_SRC"; rm -rf "$OUTDIR"' EXIT

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

# Copy original stripped source (with proper #includes) into repo
cp "{stripped_src}" "$TEMP_SRC"

# Extract modified function from permuted file and splice into the copy
python3 "{splice_helper}" "$INPUT" "$TEMP_SRC" "{function_name}"

# Compile with MWCC via Docker (wibo inside container is ~5x faster than wine)
docker exec -w "$REPO" {container} wibo {mwcc_line} "{temp_src}" -o "{outdir_rel}"
mv "$OUTDIR/{temp_src_stem}.o" "$OUTPUT"
"""
    else:
        return f"""#!/bin/bash
set -e

INPUT="$1"
shift; shift
OUTPUT="$1"

REPO="{repo}"
TEMP_SRC="$REPO/{temp_src}"

# Clean up temp source on exit (even on failure)
trap 'rm -f "$TEMP_SRC"' EXIT

# Copy original stripped source (with proper #includes) into repo
cp "{stripped_src}" "$TEMP_SRC"

# Extract modified function from permuted file and splice into the copy
python3 "{splice_helper}" "$INPUT" "$TEMP_SRC" "{function_name}"

# MWCC's -o flag takes a DIRECTORY, not a file path.
# It outputs <input_stem>.o into that directory.
OUTDIR=$(mktemp -d)
cd "$REPO" && wine {mwcc_line} "{temp_src}" -o "$OUTDIR"
mv "$OUTDIR/{temp_src_stem}.o" "$OUTPUT"
rm -rf "$OUTDIR"
"""


# Standalone splice helper written to work_dir/_splice.py
# Extracts a function from the permuted (preprocessed) file and
# replaces that function in the original stripped source.
_SPLICE_HELPER = r'''"""Extract function from permuted file, splice into stripped source."""
import re
import sys


def find_function(source, func_name):
    """Find and extract a function definition (signature + body) from source."""
    pattern = (
        r"(?:^|\n)"
        + r"([a-zA-Z_][\w\s*]*?"
        + re.escape(func_name)
        + r"\s*\([^)]*\)\s*)\{"
    )
    m = re.search(pattern, source)
    if not m:
        return None

    start = m.start()
    if source[start] == "\n":
        start += 1

    brace_start = source.index("{", m.end() - 1)
    depth = 0
    pos = brace_start
    while pos < len(source):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    end = pos + 1

    return source[start:end]


def replace_function(source, func_name, new_code):
    """Replace a function definition in source with new_code."""
    pattern = (
        r"(?:^|\n)"
        + r"([a-zA-Z_][\w\s*]*?"
        + re.escape(func_name)
        + r"\s*\([^)]*\)\s*)\{"
    )
    m = re.search(pattern, source)
    if not m:
        return None

    start = m.start()
    if source[start] == "\n":
        start += 1

    brace_start = source.index("{", m.end() - 1)
    depth = 0
    pos = brace_start
    while pos < len(source):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    end = pos + 1

    return source[:start] + new_code + source[end:]


if __name__ == "__main__":
    input_c, src_path, func_name = sys.argv[1], sys.argv[2], sys.argv[3]

    # Extract function from the permuted (preprocessed) file
    with open(input_c) as f:
        permuted = f.read()
    new_func = find_function(permuted, func_name)
    if new_func is None:
        print(f"Error: {func_name} not found in input", file=sys.stderr)
        sys.exit(1)

    # Replace function in the stripped source
    with open(src_path) as f:
        source = f.read()
    updated = replace_function(source, func_name, new_func)
    if updated is None:
        print(f"Error: {func_name} not found in {src_path}", file=sys.stderr)
        sys.exit(1)

    with open(src_path, "w") as f:
        f.write(updated)
'''


def run_permuter(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    timeout: int = 300,
) -> PermuterResult:
    """Run decomp-permuter on a function to find matching permutations.

    Sets up a permuter scratch directory with:
    - base.c: preprocessed source (all types + target function)
    - target.o: assembled from DTK asm
    - compile.sh: copies permuted source into repo, compiles with MWCC
    - settings.toml: permuter config

    Then runs the permuter with --stop-on-zero.
    """
    permuter_path = _find_permuter()
    if permuter_path is None:
        return PermuterResult(
            function_name=function_name,
            error="decomp-permuter not found. Install from "
            "https://github.com/simonlindholm/decomp-permuter",
        )

    strip_fns = _find_strip_other_fns()
    if strip_fns is None:
        return PermuterResult(
            function_name=function_name,
            error="strip_other_fns.py not found in permuter installation",
        )

    binutils = _get_binutils(config)
    if binutils is None:
        return PermuterResult(
            function_name=function_name,
            error="binutils not found. Run: ninja build/binutils",
        )

    # Get current function source (validates the function exists)
    src_path = config.melee.resolve_source_path(source_file)
    if not src_path.exists():
        return PermuterResult(
            function_name=function_name,
            error=f"Source file not found: {src_path}",
        )

    source = read_source_file(src_path)
    func_code = get_function_source(source, function_name)
    if func_code is None:
        return PermuterResult(
            function_name=function_name,
            error=f"Function {function_name} not found in {source_file}",
        )

    # Get target assembly for the function
    from decomp_agent.tools.m2c_tool import get_target_assembly

    target_asm = get_target_assembly(function_name, source_file, config)
    if target_asm is None:
        return PermuterResult(
            function_name=function_name,
            error="Could not get target assembly for function",
        )

    with tempfile.TemporaryDirectory(prefix="permuter_") as tmpdir:
        work_dir = Path(tmpdir)

        # 1. Create stripped source (headers + only target function)
        stripped_path = work_dir / f"_stripped_{Path(source_file).stem}.c"
        shutil.copy2(src_path, stripped_path)
        result = subprocess.run(
            ["python3", str(strip_fns), str(stripped_path), function_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return PermuterResult(
                function_name=function_name,
                error=f"strip_other_fns.py failed: {result.stderr}",
            )

        # 2. Preprocess stripped source into base.c
        #    The permuter's C parser (pycparser) needs all types defined.
        try:
            preprocessed = _preprocess_source(
                stripped_path, config, source_file
            )
        except RuntimeError as e:
            return PermuterResult(
                function_name=function_name, error=str(e)
            )
        (work_dir / "base.c").write_text(preprocessed, encoding="utf-8")

        # 3. Assemble target asm -> single-function target.o
        target_o = work_dir / "target.o"
        if not _assemble_target(target_asm, function_name, target_o, binutils):
            return PermuterResult(
                function_name=function_name,
                error="Failed to assemble target assembly into .o file",
            )

        # 4. Write splice helper
        splice_helper = work_dir / "_splice.py"
        splice_helper.write_text(_SPLICE_HELPER, encoding="utf-8")

        # 5. Write compile.sh
        try:
            compile_script = _build_compile_sh(
                function_name, source_file, config,
                splice_helper, stripped_path,
            )
        except RuntimeError as e:
            return PermuterResult(
                function_name=function_name, error=str(e)
            )
        compile_sh = work_dir / "compile.sh"
        compile_sh.write_text(compile_script, encoding="utf-8")
        compile_sh.chmod(0o755)

        # 6. Write settings.toml (permuter reads compiler_type, not compiler)
        (work_dir / "settings.toml").write_text(
            f'func_name = "{function_name}"\n'
            f'compiler_type = "mwcc"\n',
            encoding="utf-8",
        )

        # 7. Add binutils to PATH and run permuter
        env = os.environ.copy()
        env["PATH"] = str(binutils) + ":" + env.get("PATH", "")

        try:
            proc = subprocess.run(
                [
                    "python3",
                    str(permuter_path),
                    str(work_dir),
                    "--stop-on-zero",
                    "--best-only",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(permuter_path.parent),
                env=env,
            )
        except subprocess.TimeoutExpired:
            # Check for best output even on timeout
            best_code = _read_best_output(work_dir)
            return PermuterResult(
                function_name=function_name,
                best_code=best_code,
                error=f"Permuter timed out after {timeout}s"
                + (" (found improved code)" if best_code else ""),
            )

        output = proc.stdout + proc.stderr
        best_score, iterations = _parse_permuter_output(output)
        best_code = _read_best_output(work_dir)

        if proc.returncode != 0 and best_code is None:
            return PermuterResult(
                function_name=function_name,
                best_score=best_score,
                iterations=iterations,
                error=output[:2000] if output else "Permuter failed",
            )

        return PermuterResult(
            function_name=function_name,
            best_score=best_score,
            best_code=best_code,
            iterations=iterations,
        )


def _parse_permuter_output(output: str) -> tuple[int | None, int]:
    """Parse permuter stdout/stderr for score and iteration count."""
    best_score = None
    iterations = 0
    for line in output.splitlines():
        if "score" in line.lower():
            try:
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    if part.lower().startswith("iteration"):
                        iterations = int(part.split()[-1])
                    elif "score" in part.lower():
                        score = int(part.split()[-1])
                        if best_score is None or score < best_score:
                            best_score = score
            except (ValueError, IndexError):
                pass
        # Also check "base score = N" from debug mode
        m = re.search(r"base score\s*=\s*(\d+)", line, re.IGNORECASE)
        if m:
            score = int(m.group(1))
            if best_score is None or score < best_score:
                best_score = score
    return best_score, iterations


def _read_best_output(work_dir: Path) -> str | None:
    """Read the best permutation output if it exists."""
    output_dir = work_dir / "output"
    if not output_dir.is_dir():
        return None
    best_files = sorted(output_dir.glob("*.c"))
    if not best_files:
        return None
    return best_files[-1].read_text(encoding="utf-8")
