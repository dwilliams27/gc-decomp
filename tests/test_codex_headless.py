from __future__ import annotations

from decomp_agent.agent.loop import AgentResult
from decomp_agent.orchestrator.codex_headless import (
    _parse_codex_result,
    _parse_jsonl_events,
)


def test_parse_jsonl_events_skips_non_json_lines():
    output = "\n".join([
        "thread 'main' panicked at somewhere",
        '{"type":"thread.started","thread_id":"abc123"}',
        '{"type":"turn.started"}',
        "plain text noise",
        '{"type":"turn.failed","error":{"message":"network down"}}',
    ])

    events = _parse_jsonl_events(output)

    assert [event["type"] for event in events] == [
        "thread.started",
        "turn.started",
        "turn.failed",
    ]


def test_parse_codex_result_extracts_session_and_failure():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"thread-1"}',
        '{"type":"turn.started"}',
        '{"type":"turn.failed","error":{"message":"stream disconnected"}}',
    ])
    result = AgentResult(model="codex-code-headless")

    reason, detail = _parse_codex_result(stdout, "", result)

    assert result.session_id == "thread-1"
    assert result.iterations == 1
    assert reason == "api_error"
    assert detail == "stream disconnected"


def test_parse_codex_result_detects_rate_limit():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"thread-2"}',
        '{"type":"turn.started"}',
        '{"type":"error","message":"429 rate limit reached"}',
    ])
    result = AgentResult(model="codex-code-headless")

    reason, detail = _parse_codex_result(stdout, "", result)

    assert reason == "rate_limited"
    assert "429 rate limit" in detail
