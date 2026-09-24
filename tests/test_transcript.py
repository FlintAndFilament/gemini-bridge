"""Tests for sidekick/transcript.py — TranscriptWriter and format."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from sidekick.transcript import TranscriptError, TranscriptWriter, _format_exchange


def test_transcript_file_created() -> None:
    startup = datetime(2026, 7, 2, 14, 30, 0)
    with tempfile.TemporaryDirectory() as tmp:
        writer = TranscriptWriter(tmp, startup)
        assert writer.path.name == "20260702-1430-sidekick-transcript.md"


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
            tool_name="ask",
            prompt="What is the capital of France?",
            response="Paris.",
            thinking="low",
            session="default",
            timestamp=ts,
        )
        content = writer.path.read_text()
    assert "[14:32:07] ask" in content
    assert "thinking: low | session: default" in content
    assert "What is the capital of France?" in content
    assert "Paris." in content


def test_append_multiple_exchanges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        writer = TranscriptWriter(tmp, datetime.now())
        for i in range(3):
            writer.append(
                tool_name="brainstorm",
                prompt=f"prompt {i}",
                response=f"response {i}",
                thinking="medium",
            )
        content = writer.path.read_text()
    assert content.count("brainstorm") == 3


def test_format_exchange_contains_all_fields() -> None:
    ts = datetime(2026, 7, 2, 9, 0, 1)
    result = _format_exchange("review", "my prompt", "my response", "high", "work", ts)
    assert "[09:00:01] review" in result
    assert "thinking: high | session: work" in result
    assert "my prompt" in result
    assert "my response" in result
    assert result.strip().endswith("---")


def test_append_survives_bad_path(capsys: object) -> None:
    writer = TranscriptWriter.__new__(TranscriptWriter)
    writer._path = Path("/nonexistent/really/bad/path/transcript.md")
    writer.append(
        tool_name="ask",
        prompt="p",
        response="r",
        thinking="none",
    )
    # Should not raise — write errors go to stderr, not exceptions


def test_append_renders_tool_calls(tmp_path: Path) -> None:
    writer = TranscriptWriter(str(tmp_path), datetime.now())
    writer.append(
        tool_name="review",
        prompt="p",
        response="r",
        thinking="low",
        tool_calls=["→ read_file(path='a.py') → 1.0 KiB", "✗ read_file(path='../x') → rejected"],
    )
    content = writer.path.read_text()
    assert "**Tool calls:**\n- → read_file(path='a.py') → 1.0 KiB\n- ✗ read_file" in content


def test_append_without_tool_calls_has_no_section(tmp_path: Path) -> None:
    writer = TranscriptWriter(str(tmp_path), datetime.now())
    writer.append(tool_name="ask", prompt="p", response="r", thinking="low")
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


def test_unresolvable_home_is_an_actionable_error_too() -> None:
    """expanduser() raises RuntimeError, not OSError, for an unknown ~user — it has to be
    inside the same guard or #87's raw traceback survives (#94)."""
    with pytest.raises(TranscriptError) as exc:
        TranscriptWriter("~nosuchuser12345/transcripts", datetime.now())
    message = str(exc.value)
    assert "transcript_dir" in message
    assert "~nosuchuser12345/transcripts" in message


class TestTranscriptBase:
    def test_relative_dir_resolves_against_base(self, tmp_path: Path) -> None:
        w = TranscriptWriter("./session-summaries", datetime.now(), base=tmp_path)
        assert w.path.parent == (tmp_path / "session-summaries").resolve()

    def test_absolute_dir_ignores_base(self, tmp_path: Path) -> None:
        target = tmp_path / "abs"
        w = TranscriptWriter(str(target), datetime.now(), base=tmp_path / "elsewhere")
        assert w.path.parent == target.resolve()

    def test_no_base_keeps_cwd_behaviour(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        w = TranscriptWriter("rel", datetime.now())
        assert w.path.parent == (tmp_path / "rel").resolve()
