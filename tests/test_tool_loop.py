"""Tests for run_tool_loop() in gemini_bridge/tool_loop.py — driven by a scripted fake model."""

from typing import Any

import pytest
from google.genai import types

from gemini_bridge.errors import ClientError
from gemini_bridge.tool_loop import (
    BUDGET_EXHAUSTED_PROMPT,
    ToolCallRecord,
    ToolRegistry,
    run_tool_loop,
)


def _text(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def _calls(
    *calls: tuple[str, dict[str, Any]], signature: bool = False
) -> types.GenerateContentResponse:
    parts = [
        types.Part(
            function_call=types.FunctionCall(name=name, args=args),
            thought_signature=b"sig" if signature and i == 0 else None,
        )
        for i, (name, args) in enumerate(calls)
    ]
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def _malformed() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL)]
    )


class FakeModel:
    """Returns scripted responses; records (contents, allow_tools) for each request."""

    def __init__(self, *responses: types.GenerateContentResponse, repeat_last: bool = False):
        self._responses = list(responses)
        self._repeat_last = repeat_last
        self.requests: list[tuple[list[types.Content], bool]] = []

    async def __call__(
        self, contents: list[types.Content], allow_tools: bool
    ) -> types.GenerateContentResponse:
        self.requests.append((list(contents), allow_tools))
        if len(self._responses) == 1 and self._repeat_last:
            return self._responses[0]
        return self._responses.pop(0)


def _registry(log: list[tuple[str, dict[str, Any]]]) -> ToolRegistry:
    reg = ToolRegistry()

    def make(name: str) -> Any:
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            log.append((name, args))
            if name == "boom":
                raise RuntimeError("kaput")
            return {"echo": args}

        return handler

    for name in ("read_file", "grep", "boom"):
        reg.add(types.FunctionDeclaration(name=name, description=name), make(name))
    return reg


def _user(text: str) -> list[types.Content]:
    return [types.Content(role="user", parts=[types.Part.from_text(text=text)])]


def _responses_in(content: types.Content) -> list[types.FunctionResponse]:
    return [p.function_response for p in content.parts or [] if p.function_response]


class TestRunToolLoop:
    async def test_text_only_single_request(self) -> None:
        model = FakeModel(_text("hi"))
        result = await run_tool_loop(model, _user("q"), ToolRegistry(), [])
        assert result.text == "hi"
        assert len(model.requests) == 1 and model.requests[0][1] is True

    async def test_one_call_then_answer(self) -> None:
        log: list[tuple[str, dict[str, Any]]] = []
        records: list[ToolCallRecord] = []
        model = FakeModel(_calls(("read_file", {"path": "a.py"})), _text("done"))
        result = await run_tool_loop(model, _user("q"), _registry(log), records)

        assert result.text == "done"
        assert log == [("read_file", {"path": "a.py"})]
        last = model.requests[1][0][-1]
        assert last.role == "user"
        [resp] = _responses_in(last)
        assert resp.name == "read_file" and resp.response == {"echo": {"path": "a.py"}}
        assert [r.name for r in records] == ["read_file"] and records[0].ok

    async def test_multiple_calls_in_one_turn_run_in_order(self) -> None:
        log: list[tuple[str, dict[str, Any]]] = []
        model = FakeModel(
            _calls(("grep", {"pattern": "x"}), ("read_file", {"path": "b"})), _text("ok")
        )
        await run_tool_loop(model, _user("q"), _registry(log), [])
        assert [name for name, _ in log] == ["grep", "read_file"]
        assert [r.name for r in _responses_in(model.requests[1][0][-1])] == ["grep", "read_file"]

    async def test_thought_signature_preserved_in_next_request(self) -> None:
        model = FakeModel(_calls(("read_file", {"path": "a"}), signature=True), _text("ok"))
        await run_tool_loop(model, _user("q"), _registry([]), [])
        model_turn = model.requests[1][0][-2]
        assert model_turn.role == "model"
        assert model_turn.parts[0].thought_signature == b"sig"  # type: ignore[index]

    async def test_handler_error_goes_back_to_model(self) -> None:
        records: list[ToolCallRecord] = []
        model = FakeModel(_calls(("boom", {})), _text("recovered"))
        result = await run_tool_loop(model, _user("q"), _registry([]), records)
        assert result.text == "recovered"
        [resp] = _responses_in(model.requests[1][0][-1])
        assert "kaput" in resp.response["error"]  # type: ignore[index]
        assert records[0].ok is False

    async def test_unknown_tool_is_error_result(self) -> None:
        records: list[ToolCallRecord] = []
        model = FakeModel(_calls(("rm_rf", {})), _text("ok"))
        await run_tool_loop(model, _user("q"), _registry([]), records)
        assert records[0].summary == "unknown tool: rm_rf"

    async def test_malformed_retried_then_succeeds(self) -> None:
        model = FakeModel(_malformed(), _malformed(), _text("ok"))
        result = await run_tool_loop(model, _user("q"), _registry([]), [])
        assert result.text == "ok" and len(model.requests) == 3

    async def test_malformed_exhausts_retries(self) -> None:
        model = FakeModel(_malformed(), _malformed(), _malformed())
        with pytest.raises(ClientError, match="MALFORMED_FUNCTION_CALL"):
            await run_tool_loop(model, _user("q"), _registry([]), [])

    async def test_round_cap_forces_final_answer(self) -> None:
        loop_call = _calls(("read_file", {"path": "a"}))
        model = FakeModel(loop_call, loop_call, _text("final"))
        records: list[ToolCallRecord] = []
        result = await run_tool_loop(model, _user("q"), _registry([]), records, max_rounds=2)

        assert result.text == "final"
        contents, allow_tools = model.requests[2]
        assert allow_tools is False
        assert contents[-1].parts[0].text == BUDGET_EXHAUSTED_PROMPT  # type: ignore[index]
        assert len(records) == 2

    async def test_empty_answer_raises_with_finish_reason(self) -> None:
        empty = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(role="model", parts=[]),
                    finish_reason=types.FinishReason.SAFETY,
                )
            ]
        )
        with pytest.raises(ClientError, match="finish_reason=SAFETY"):
            await run_tool_loop(FakeModel(empty), _user("q"), ToolRegistry(), [])

    async def test_input_contents_not_mutated(self) -> None:
        contents = _user("q")
        model = FakeModel(_calls(("read_file", {"path": "a"})), _text("ok"))
        await run_tool_loop(model, contents, _registry([]), [])
        assert len(contents) == 1

    async def test_result_content_is_final_model_turn(self) -> None:
        model = FakeModel(_calls(("read_file", {"path": "a"})), _text("final"))
        result = await run_tool_loop(model, _user("q"), _registry([]), [])
        assert result.content.role == "model"
        assert result.content.parts[0].text == "final"  # type: ignore[index]


class TestSummarize:
    """The one-line result summary written into the transcript for each tool call."""

    def test_write_reports_the_file_size_not_the_response_size(self) -> None:
        """A 5-byte write used to log as '50 B' — the size of the JSON acknowledgement,
        which a reader naturally takes for the size of the file."""
        from gemini_bridge.tool_loop import summarize

        ok, summary = summarize({"path": "probe.txt", "bytes_written": 5})
        assert ok is True
        assert summary == "wrote 5 B"

    def test_large_write_uses_kib(self) -> None:
        from gemini_bridge.tool_loop import summarize

        assert summarize({"path": "big.txt", "bytes_written": 4096})[1] == "wrote 4.0 KiB"

    def test_empty_write_is_reported_honestly(self) -> None:
        from gemini_bridge.tool_loop import summarize

        assert summarize({"path": "empty.txt", "bytes_written": 0})[1] == "wrote 0 B"

    def test_reads_still_report_what_came_back_into_context(self) -> None:
        """For reads the response size is the useful number: it is what the call cost."""
        from gemini_bridge.tool_loop import summarize

        ok, summary = summarize({"content": "x" * 2048})
        assert ok is True
        assert summary.endswith("KiB")
        assert not summary.startswith("wrote")

    def test_errors_are_unchanged(self) -> None:
        from gemini_bridge.tool_loop import summarize

        assert summarize({"error": "rejected: denied"}) == (False, "rejected: denied")
