from __future__ import annotations

from decomp_agent.orchestrator.codex_bootstrap import render_codex_config


def test_render_codex_config_with_model():
    rendered = render_codex_config(
        decomp_config_path="/app/config/container.toml",
        model="gpt-5.4",
    )

    assert 'model = "gpt-5.4"' in rendered
    assert '[mcp_servers.decomp-tools]' in rendered
    assert 'command = "python"' in rendered
    assert 'args = ["-m", "decomp_agent.mcp_server"]' in rendered
    assert 'DECOMP_CONFIG = "/app/config/container.toml"' in rendered


def test_render_codex_config_without_model():
    rendered = render_codex_config(
        decomp_config_path="/tmp/custom.toml",
        model=None,
    )

    assert 'model =' not in rendered
    assert 'DECOMP_CONFIG = "/tmp/custom.toml"' in rendered
