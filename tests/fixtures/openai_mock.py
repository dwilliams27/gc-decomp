"""Scripted OpenAI client mock for deterministic E2E tests.

Returns pre-programmed tool-call sequences so the agent loop runs
real iteration logic without hitting the OpenAI API.
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, field


@dataclass
class ScriptedToolCall:
    """One tool call the mock model should request."""

    name: str
    arguments: dict


@dataclass
class ScriptedResponse:
    """One scripted model response.

    If tool_calls is set, the model is requesting tool use.
    If only content is set, the model is stopping (no tool calls).
    """

    tool_calls: list[ScriptedToolCall] | None = None
    content: str | None = None
    tokens: int = 500


def _build_tool_call(tc: ScriptedToolCall, call_id: str) -> object:
    """Build a namespace object matching OpenAI SDK ChatCompletionMessageToolCall."""
    return types.SimpleNamespace(
        id=call_id,
        type="function",
        function=types.SimpleNamespace(
            name=tc.name,
            arguments=json.dumps(tc.arguments),
        ),
    )


def _build_response(scripted: ScriptedResponse, call_index: int) -> object:
    """Build a namespace object matching an OpenAI ChatCompletion response."""
    tool_calls = None
    if scripted.tool_calls:
        tool_calls = [
            _build_tool_call(tc, f"call_{call_index}_{i}")
            for i, tc in enumerate(scripted.tool_calls)
        ]

    message = types.SimpleNamespace(
        role="assistant",
        content=scripted.content,
        tool_calls=tool_calls,
    )

    choice = types.SimpleNamespace(
        message=message,
        finish_reason="tool_calls" if tool_calls else "stop",
    )

    usage = types.SimpleNamespace(
        prompt_tokens=scripted.tokens // 2,
        completion_tokens=scripted.tokens // 2,
        total_tokens=scripted.tokens,
    )

    return types.SimpleNamespace(
        choices=[choice],
        usage=usage,
    )


def _build_stop_response() -> object:
    """Build a terminal response with no tool calls (model stops)."""
    message = types.SimpleNamespace(
        role="assistant",
        content="I've completed my analysis.",
        tool_calls=None,
    )
    choice = types.SimpleNamespace(
        message=message,
        finish_reason="stop",
    )
    usage = types.SimpleNamespace(
        prompt_tokens=50,
        completion_tokens=50,
        total_tokens=100,
    )
    return types.SimpleNamespace(
        choices=[choice],
        usage=usage,
    )


@dataclass
class _Completions:
    """Mimics client.chat.completions with a create() method."""

    responses: list[ScriptedResponse]
    calls: list[dict] = field(default_factory=list)
    _index: int = 0

    def create(self, **kwargs) -> object:
        """Pop the next scripted response, or return a stop response."""
        self.calls.append(kwargs)
        if self._index < len(self.responses):
            resp = self.responses[self._index]
            result = _build_response(resp, self._index)
            self._index += 1
            return result
        return _build_stop_response()


@dataclass
class _Chat:
    completions: _Completions


class ScriptedOpenAI:
    """Mock OpenAI client that returns pre-scripted tool call sequences.

    Usage::

        mock_client = ScriptedOpenAI([
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("get_target_assembly", {"function_name": "foo", "source_file": "bar.c"}),
            ]),
            ScriptedResponse(content="Done."),
        ])

        # Patch OpenAI() to return this:
        with patch("decomp_agent.agent.loop.OpenAI", return_value=mock_client):
            result = run_agent(...)

        # Inspect what was sent to the mock:
        assert len(mock_client.chat.completions.calls) == 2
    """

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self.chat = _Chat(completions=_Completions(responses=responses))

    @property
    def calls(self) -> list[dict]:
        """All create() calls recorded."""
        return self.chat.completions.calls
