"""Scripted OpenAI client mock for deterministic E2E tests.

Returns pre-programmed tool-call sequences so the agent loop runs
real iteration logic without hitting the OpenAI API.

Mocks the Responses API (client.responses.create) used by loop.py.
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


def _build_function_call(tc: ScriptedToolCall, call_id: str) -> object:
    """Build a namespace object matching ResponseFunctionToolCall."""
    return types.SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=tc.name,
        arguments=json.dumps(tc.arguments),
        id=f"item_{call_id}",
        status="completed",
    )


def _build_message_output(text: str) -> object:
    """Build a namespace object matching ResponseOutputMessage."""
    return types.SimpleNamespace(
        type="message",
        role="assistant",
        content=[types.SimpleNamespace(type="output_text", text=text)],
        id="msg_output",
        status="completed",
    )


def _build_response(scripted: ScriptedResponse, call_index: int) -> object:
    """Build a namespace object matching an OpenAI Responses API Response."""
    output = []

    if scripted.tool_calls:
        for i, tc in enumerate(scripted.tool_calls):
            output.append(
                _build_function_call(tc, f"call_{call_index}_{i}")
            )

    if scripted.content:
        output.append(_build_message_output(scripted.content))

    # If no tool calls and no content, add a default stop message
    if not output:
        output.append(_build_message_output("I've completed my analysis."))

    input_toks = scripted.tokens // 2
    usage = types.SimpleNamespace(
        input_tokens=input_toks,
        output_tokens=scripted.tokens - input_toks,
        total_tokens=scripted.tokens,
        input_tokens_details=types.SimpleNamespace(
            cached_tokens=input_toks // 2,
        ),
    )

    return types.SimpleNamespace(
        id=f"resp_{call_index}",
        output=output,
        output_text=scripted.content or "",
        usage=usage,
    )


def _build_stop_response() -> object:
    """Build a terminal response with no tool calls (model stops)."""
    output = [_build_message_output("I've completed my analysis.")]

    usage = types.SimpleNamespace(
        input_tokens=50,
        output_tokens=50,
        total_tokens=100,
        input_tokens_details=types.SimpleNamespace(
            cached_tokens=25,
        ),
    )

    return types.SimpleNamespace(
        id="resp_stop",
        output=output,
        output_text="I've completed my analysis.",
        usage=usage,
    )


@dataclass
class _Responses:
    """Mimics client.responses with a create() method."""

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


class ScriptedOpenAI:
    """Mock OpenAI client that returns pre-scripted tool call sequences.

    Mocks the Responses API (client.responses.create).

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
        assert len(mock_client.responses.calls) == 2
    """

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self.responses = _Responses(responses=responses)

    @property
    def calls(self) -> list[dict]:
        """All create() calls recorded."""
        return self.responses.calls
