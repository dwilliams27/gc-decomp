FROM melee-build:local AS base

# System deps + ninja + pip
RUN apt-get update && apt-get install -y ninja-build curl python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Node.js for Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Pin Claude Code CLI version for reproducibility
RUN npm install -g @anthropic-ai/claude-code@1

# Wine/wibo symlink (build.ninja calls `wine` which should resolve to wibo)
RUN ln -sf /usr/local/sbin/wibo /usr/local/bin/wine

# Python packages (decomp-agent with MCP extras)
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN pip3 install --break-system-packages '/app[mcp]'

# m2c for decompilation (pure Python, runs natively)
RUN pip3 install --break-system-packages m2c

# NOTE: decomp-permuter is NOT included in initial build.
# See "Deferred: Permuter" in the plan for steps to add it later.

# Container config + MCP config + system prompt
COPY docker/config/container.toml /app/config/container.toml
COPY docker/mcp.json /app/mcp.json
COPY docker/system-prompt.md /app/system-prompt.md

# Non-root user so --dangerously-skip-permissions works
RUN useradd -m -s /bin/bash decomp
USER decomp
