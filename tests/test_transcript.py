"""Tests for gemini_bridge/transcript.py — TranscriptWriter and format."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from gemini_bridge.transcript import TranscriptError, TranscriptWriter, _format_exchange


def test_transcript_file_created() -> None:
    startup = datetime(2026, 7, 2, 14, 30, 0)
    with tempfile.TemporaryDirectory() as tmp:
        writer = TranscriptWriter(tmp, startup)
        assert writer.path.name == "20260702-1430-gemini-bridge-transcript.md"


def test_transcript_dir_created() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        new_dir = Path(tmp) / "nested" / "transcripts"
        writer = TranscriptWriter(str(new_dir), datetime.now())
        assert writer.path.parent.exists()


def test_append_writes_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        writer = TranscriptWriter(tmp, datetime.now())
        ts = datetime(2026, 7, 2, 14, 32, 7)
        writer.append(
            tool_name="gemini_ask",
            prompt="What is the capital of France?",
            response="Paris.",
            thinking="low",
            session="default",
            timestamp=ts,
        )
        content = writer.path.read_text()
    assert "[14:32:07] gemini_ask" in content
    assert "thinking: low | session: default" in content
    assert "What is the capital of France?" in content
    assert "Paris." in content


def test_append_multiple_exchanges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        writer = TranscriptWriter(tmp, datetime.now())
        for i in range(3):
            writer.append(
                tool_name="gemini_brainstorm",
                prompt=f"prompt {i}",
                response=f"response {i}",
                thinking="medium",
            )
        content = writer.path.read_text()
    assert content.count("gemini_brainstorm") == 3


def test_format_exchange_contains_all_fields() -> None:
    ts = datetime(2026, 7, 2, 9, 0, 1)
    result = _format_exchange("gemini_review", "my prompt", "my response", "high", "work", ts)
    assert "[09:00:01] gemini_review" in result
    assert "thinking: high | session: work" in result
    assert "my prompt" in result
    assert "my response" in result
    assert result.strip().endswith("---")


def test_append_survives_bad_path(capsys: object) -> None:
    writer = TranscriptWriter.__new__(TranscriptWriter)
    writer._path = Path("/nonexistent/really/bad/path/transcript.md")
    writer.append(
        tool_name="gemini_ask",
        prompt="p",
        response="r",
        thinking="none",
    )
    # Should not raise — write errors go to stderr, not exceptions


def test_append_renders_tool_calls(tmp_path: Path) -> None:
    writer = TranscriptWriter(str(tmp_path), datetime.now())
    writer.append(
        tool_name="gemini_review",
        prompt="p",
        response="r",
        thinking="low",
        tool_calls=["→ read_file(path='a.py') → 1.0 KiB", "✗ read_file(path='../x') → rejected"],
    )
    content = writer.path.read_text()
    assert "**Tool calls:**\n- → read_file(path='a.py') → 1.0 KiB\n- ✗ read_file" in content


def test_append_without_tool_calls_has_no_section(tmp_path: Path) -> None:
    writer = TranscriptWriter(str(tmp_path), datetime.now())
    writer.append(tool_name="gemini_ask", prompt="p", response="r", thinking="low")
    assert "Tool calls" not in writer.path.read_text()


def test_unusable_transcript_dir_raises_an_actionable_error(tmp_path: Path) -> None:
    """A transcript_dir that cannot be created must not crash startup with a raw traceback:
    it names the directory and the config field, like the config and auth errors (#87)."""
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")

    with pytest.raises(TranscriptError) as exc:
        TranscriptWriter(str(blocker / "transcripts"), datetime.now())
    message = str(exc.value)
    assert "transcript_dir" in message
    assert str(blocker / "transcripts") in message
