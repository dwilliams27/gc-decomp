# Permuter Integration — Status & TODO

The decomp-permuter tool (`~/decomp-permuter/permuter.py`) is partially integrated but
not yet working end-to-end. The individual pieces all work; the remaining work is
wiring them together in `compile.sh`.

## What Works

1. **Target assembly → single-function target.o**: Convert DTK asm to GNU AS format,
   assemble with `powerpc-eabi-as` from `build/binutils/`. Produces a clean .o with
   only the target function. (`_convert_dtk_asm` + `_assemble_target` in permuter.py)

2. **MWCC command extraction**: Parse `build.ninja` to get the exact compiler flags for
   any source file, including `$` line continuation handling. Returns the full
   `wine sjiswrap.exe mwcceppc.exe <flags> -c` command string.
   (`_extract_mwcc_command` in permuter.py)

3. **Single-function source stripping**: Use permuter's `strip_other_fns.py` on a copy
   of the source file to keep only includes + declarations + the target function body.
   This compiles into a .o with only one function (1KB vs 8KB for full TU).

4. **Standalone MWCC compilation**: The stripped source compiles successfully with the
   extracted MWCC command when run from the repo root (needed for `-cwd source` and
   `-i` include paths).

5. **Binutils on PATH**: `build/binutils/powerpc-eabi-objdump` is required by the
   permuter's scorer. We add it to PATH via `env` in the subprocess call.

6. **permuter_settings.toml**: Created in the melee repo with `build_system = "ninja"`
   and `compiler_type = "mwcc"`.

## What's Left

### compile.sh Integration

The compile.sh script needs to:
1. Take `input.c` (modified function) and `-o output.o` args from the permuter
2. Copy the pre-stripped source into the repo's `src/` directory (so MWCC include
   paths resolve — it uses `-cwd source` relative includes)
3. Splice the modified function code from `input.c` into the stripped source copy
   (using `_splice.py` helper)
4. Compile with the extracted MWCC command
5. Clean up the temp source file

The `_splice.py` helper (function splicing via regex) works. The issue is getting the
full pipeline through compile.sh to work reliably — escaping, paths, and error handling.

### Remaining Issues

- **compile.sh quoting**: The MWCC cflags contain `-pragma "cats off"` which needs
  careful quoting in the shell script. Current approach passes the raw command string
  which should work but hasn't been tested end-to-end.
- **Temp file cleanup**: compile.sh creates `_permuter_<name>.c` in the repo src dir.
  Must be cleaned up even on failure. The main `run_permuter()` function also needs
  to restore the original source if anything goes wrong.
- **Timeout**: Default 300s is too long for interactive use but may be needed for
  actual permutation runs. Consider making it configurable or using shorter timeouts
  for agent use (60-120s).
- **Score parsing**: The permuter's output format for PPC may differ from MIPS.
  Need to verify the regex in `_parse_permuter_output` against actual output.

### Test Command

To test manually once compile.sh is fixed:
```bash
export PATH="$HOME/proj/melee-fork/melee/build/binutils:$PATH"
python3 ~/decomp-permuter/permuter.py /tmp/permuter_dir --stop-on-zero --best-only --debug
```

The `--debug` flag compiles once and prints the score without iterating — useful for
verifying the setup works before running the full permuter.
