# Docker Setup for Faster Compilation

## Why Docker?

On macOS, MWCC compilation requires Wine to run `.exe` compilers. Standard Wine takes ~2500ms per invocation (dominated by startup overhead). The melee Docker image ships **wibo v0.6.11**, a lightweight Wine replacement that runs MWCC in ~160-200ms — a **~12x speedup**.

Docker Desktop's Rosetta 2 runs x86_64 Linux containers on Apple Silicon with minimal overhead.

## Setup

### 1. Build the image locally

The GHCR package requires org-level auth, so we build from the repo's Dockerfile:

```bash
cd /path/to/melee
docker build --platform linux/amd64 \
  -t melee-build:local \
  -f .github/packages/build-linux/Dockerfile \
  --target build-linux .
```

### 2. Start a persistent container

The repo must be mounted at the **same absolute path** as on the host, because `build.ninja` and tool paths are absolute. Override the default entrypoint (which expects a `/input` volume):

```bash
docker run -d --name melee-build \
  --platform linux/amd64 \
  --entrypoint sleep \
  -v /path/to/melee:/path/to/melee \
  melee-build:local infinity
```

### 3. Set up the container

```bash
# Create wine -> wibo symlink (build.ninja calls `wine` directly)
docker exec melee-build ln -s /usr/local/sbin/wibo /usr/local/bin/wine

# Install ninja (the image only has make; our fork uses ninja)
docker exec melee-build apt-get update
docker exec melee-build apt-get install -y ninja-build

# Symlink host Python path so build.ninja's python variable resolves
# (replace with your actual python path from build.ninja line 4)
docker exec melee-build mkdir -p /path/to/.pyenv/versions/3.10.0/bin
docker exec melee-build ln -s /usr/bin/python3 /path/to/.pyenv/versions/3.10.0/bin/python
```

### 4. Enable Docker in config

In `config/default.toml`:

```toml
[docker]
enabled = true
```

## How It Works

- **`run_in_repo()`** (`tools/run.py`): All tools that use `run_in_repo()` (build, disasm, m2c, etc.) automatically route commands through `docker exec` when Docker is enabled. No per-tool changes needed.

- **Permuter** (`tools/permuter.py`): The permuter bypasses `run_in_repo()` because it constructs its own compile.sh. When Docker is enabled, compile.sh wraps the MWCC invocation with `docker exec` and uses `wibo` directly, with the output directory inside the repo (so the container can access it via the bind mount).

- **Wine resolution**: The `wine` -> `wibo` symlink in the container means `build.ninja` rules that call `wine` transparently use wibo instead.

## Measured Performance

| Approach | Per-MWCC compile | vs baseline |
|---|---|---|
| wine (macOS, warm) | ~2500ms | 1x |
| Docker + wibo | ~200ms | ~12x |

## Alternative: wineserver -p (No Docker)

If you don't want to use Docker, you can keep `docker.enabled = false` and instead run a persistent Wine server:

```bash
cd /path/to/melee && wineserver -p
```

This keeps the Wine server resident in memory, avoiding cold-start overhead on each invocation. Gives ~2-3x speedup over baseline. The server shuts down automatically after a period of inactivity, or you can stop it with `wineserver -k`.

## Troubleshooting

- **Container not running**: `docker start melee-build`
- **Container doesn't exist**: Re-run the `docker run` command from step 2
- **"wine: not found" inside container**: `docker exec melee-build ln -s /usr/local/sbin/wibo /usr/local/bin/wine`
- **"python: not found" in ninja**: Check `build.ninja` line 4 for the python path, then create the symlink per step 3
- **Stale container after image update**: `docker rm -f melee-build` and re-create from step 2
