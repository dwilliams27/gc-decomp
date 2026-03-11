"""Helpers for bootstrapping a worker-local Codex home."""

from __future__ import annotations


def render_codex_config(
    *,
    decomp_config_path: str,
    model: str | None = None,
) -> str:
    """Render a minimal Codex config with the decomp MCP server."""
    lines: list[str] = []
    if model:
        lines.append(f'model = "{model}"')
        lines.append("")

    lines.extend([
        '[mcp_servers.decomp-tools]',
        'command = "python"',
        'args = ["-m", "decomp_agent.mcp_server"]',
        "",
        "[mcp_servers.decomp-tools.env]",
        f'DECOMP_CONFIG = "{decomp_config_path}"',
        "",
    ])
    return "\n".join(lines)
