# Permuter Integration — Working

The decomp-permuter tool (`~/decomp-permuter/permuter.py`) is fully integrated and
working end-to-end via `run_permuter()` in `src/decomp_agent/tools/permuter.py`.

## How It Works

1. **Strip source**: `strip_other_fns.py` keeps only the target function + includes
2. **Preprocess**: `cc -E -P` expands all includes into a self-contained base.c that
   pycparser can parse. GCC extensions (`__attribute__`, `_Static_assert`) are stubbed out.
3. **Assemble target**: DTK asm → GNU AS format → `powerpc-eabi-as` → target.o
4. **compile.sh**: Copies permuted (preprocessed) source into repo's src/ dir, compiles
   with MWCC via wine, moves output .o to permuter's expected location.
5. **Permuter runs**: Generates permutations of base.c, compiles each via compile.sh,
   scores against target.o using `powerpc-eabi-objdump`.

## Key Implementation Details

- **MWCC's `-o` flag takes a DIRECTORY**, not a file path. compile.sh uses a temp dir
  for MWCC output then `mv`s the .o to the permuter's expected location.
- **base.c must be fully preprocessed** — the permuter's C parser (pycparser) needs all
  types defined inline. Raw source with `#include` directives won't parse.
- **settings.toml uses `compiler_type`** (not `compiler`) — controls which randomization
  weights the permuter uses (mwcc vs ido vs base).
- **Include paths extracted from build.ninja** — MWCC uses lowercase `-i` for includes;
  we convert to `-I` for cpp preprocessing.
- The `func_name` field in settings.toml helps the permuter identify the target function
  in the large preprocessed file.

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
