# Permuter Integration — Working

The decomp-permuter tool (`~/decomp-permuter/permuter.py`) is fully integrated and
working end-to-end via `run_permuter()` in `src/decomp_agent/tools/permuter.py`.

## How It Works

1. **Strip source**: `strip_other_fns.py` keeps only the target function + includes
2. **Preprocess**: `cc -E -P` expands all includes into a self-contained base.c that
   pycparser can parse. GCC extensions (`__attribute__`, `_Static_assert`) are stubbed out.
3. **Assemble target**: DTK asm → GNU AS format → `powerpc-eabi-as` → target.o
4. **Splice-based compile.sh**: Extracts the modified function from the permuted
   (preprocessed) file, splices it into the original stripped source (which has proper
   `#include` directives for MWCC), compiles with MWCC via wine.
5. **Permuter runs**: Generates permutations of base.c, compiles each via compile.sh,
   scores against target.o using `powerpc-eabi-objdump`.

## Key Implementation Details

- **MWCC's `-o` flag takes a DIRECTORY**, not a file path. compile.sh uses a temp dir
  for MWCC output then `mv`s the .o to the permuter's expected location.
- **base.c must be fully preprocessed** — the permuter's C parser (pycparser) needs all
  types defined inline. Raw source with `#include` directives won't parse.
- **Compile uses original source, not preprocessed** — base.c is preprocessed for
  pycparser, but compile.sh splices the function back into the original stripped source
  (with `#include` directives) for MWCC. This avoids type redefinition errors.
- **`_splice.py` helper** — standalone script that extracts a function from the permuted
  file and replaces it in the stripped source, bridging preprocessed ↔ original formats.
- **settings.toml uses `compiler_type`** (not `compiler`) — controls which randomization
  weights the permuter uses (mwcc vs ido vs base).
- **Include paths extracted from build.ninja** — MWCC uses lowercase `-i` for includes;
  we convert to `-I` for cpp preprocessing. Source file's directory is added as `-I` for
  sibling header resolution.
- The `func_name` field in settings.toml helps the permuter identify the target function
  in the large preprocessed file.

## Performance

- Each permuter iteration takes ~0.7s (wine/MWCC overhead dominates)
- ~90 iterations per 60 seconds
- Register-only diffs (score ~35) may need hundreds of iterations to find score=0
- Default timeout: 300s (~450 iterations)

## Verified Working

- **Command_00** (lbcommand.c): score=0 on iteration 1 (base already matches)
- **Command_01** (lbcommand.c): score=0 on iteration 1
- **lbSnap_8001DF20** (lbsnap.c): pipeline verified end-to-end, base score=35
  (register-only diffs), iterates correctly at ~1.5 iter/s

## Test Command

```bash
# Run through the agent's tool registry:
python3 -c "
import sys, json; sys.path.insert(0, 'src')
from decomp_agent.config import load_config
from decomp_agent.tools.registry import build_registry
config = load_config()
registry = build_registry(config)
print(registry.dispatch('run_permuter', json.dumps({
    'function_name': 'Command_00',
    'source_file': 'melee/lb/lbcommand.c'
})))
"

# Or directly:
python3 -c "
import sys; sys.path.insert(0, 'src')
from decomp_agent.config import load_config
from decomp_agent.tools.permuter import run_permuter
config = load_config()
r = run_permuter('Command_00', 'melee/lb/lbcommand.c', config, timeout=120)
print(f'score={r.best_score} success={r.success} iters={r.iterations}')
"
```
