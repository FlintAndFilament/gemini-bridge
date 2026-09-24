"""Tests for sidekick.models — backend detection, shortlists, schema hints, chat filter.

The critical case is is_chat_capable: image/tts models ALSO report 'generateContent',
so a naive `'generateContent' in supported_actions` filter is wrong. These tests pin the
name-based exclusion that guards that trap (verified live 2026-07-08).
"""

from types import SimpleNamespace

import pytest

from sidekick import models


def _meta(name: str, actions=("generateContent", "countTokens")) -> SimpleNamespace:
    """Lightweight stand-in for google.genai.types.Model (no network)."""
    return SimpleNamespace(name=name, supported_actions=list(actions))


class TestBackendFor:
    def test_api_key_is_developer(self) -> None:
        assert models.backend_for("api_key") == models.DEVELOPER_API

    def test_other_methods_are_vertex(self) -> None:
        for method in ("adc", "env", "keychain", "something-else"):
            assert models.backend_for(method) == models.VERTEX


class TestShortlist:
    def test_developer_includes_latest_aliases(self) -> None:
        ids = [model_id for model_id, _ in models.shortlist(models.DEVELOPER_API)]
        assert "gemini-flash-latest" in ids
        assert "gemini-pro-latest" in ids
        assert "gemini-3.1-flash-lite" in ids

    def test_vertex_excludes_latest_aliases(self) -> None:
        ids = [model_id for model_id, _ in models.shortlist(models.VERTEX)]
        assert ids, "vertex shortlist must not be empty"
        assert all("-latest" not in model_id for model_id in ids)
        assert "gemini-3.5-flash" in ids

    def test_shortlists_drop_retiring_2_5_flash(self) -> None:
        # #58: the curated lists lead with long-lived 3.x; the Oct-2026-retiring 2.5-flash is out.
        for backend in (models.DEVELOPER_API, models.VERTEX):
            ids = [model_id for model_id, _ in models.shortlist(backend)]
            assert "gemini-2.5-flash" not in ids, backend
            assert "gemini-3.1-flash-lite" in ids, backend

    def test_default_family_leads_both_backends(self) -> None:
        # gemini-3.5-flash is the server default; it should be presented first.
        for backend in (models.DEVELOPER_API, models.VERTEX):
            first_id = models.shortlist(backend)[0][0]
            assert first_id == "gemini-3.5-flash"

    def test_labels_do_not_hardcode_default_word(self) -> None:
        # The default is marked dynamically, never baked into a static label (staleness guard).
        for backend in (models.DEVELOPER_API, models.VERTEX):
            for _, label in models.shortlist(backend):
                assert "default" not in label.lower()

    def test_unknown_backend_falls_back_to_developer(self) -> None:
        assert models.shortlist("nonsense") == models.shortlist(models.DEVELOPER_API)


class TestSchemaHint:
    def test_developer_hint_mentions_alias_default_and_list_tool(self) -> None:
        hint = models.schema_hint(models.DEVELOPER_API, "gemini-3.5-flash")
        assert "gemini-flash-latest" in hint
        assert "gemini-3.5-flash" in hint  # named as the server default
        assert "gemini_list_models" in hint

    def test_vertex_hint_omits_latest_aliases(self) -> None:
        hint = models.schema_hint(models.VERTEX, "gemini-3.5-flash")
        assert "-latest" not in hint
        assert "gemini-3.5-flash" in hint


class TestIsChatCapable:
    def test_flash_included(self) -> None:
        assert models.is_chat_capable(_meta("models/gemini-2.5-flash")) is True

    def test_image_excluded_despite_generatecontent(self) -> None:
        # The core trap: image models carry generateContent but must be dropped.
        assert models.is_chat_capable(_meta("models/gemini-2.5-flash-image")) is False

    def test_tts_excluded_despite_generatecontent(self) -> None:
        assert models.is_chat_capable(_meta("models/gemini-2.5-flash-preview-tts")) is False

    def test_gemini3_pro_image_excluded(self) -> None:
        assert models.is_chat_capable(_meta("models/gemini-3-pro-image")) is False

    def test_embedding_excluded(self) -> None:
        meta = _meta("models/gemini-embedding-001", actions=["embedContent"])
        assert models.is_chat_capable(meta) is False

    def test_live_excluded(self) -> None:
        assert models.is_chat_capable(_meta("models/gemini-live-2.5-flash-preview")) is False

    def test_no_generatecontent_excluded(self) -> None:
        # Developer API: supported_actions populated but generateContent absent → excluded.
        assert (
            models.is_chat_capable(_meta("models/gemini-2.5-flash", actions=["countTokens"]))
            is False
        )

    def test_vertex_none_actions_passes_name_gates(self) -> None:
        # Vertex AI returns supported_actions=None for all models; the action gate must be
        # skipped so name-based gates remain the only filter.
        none_meta = SimpleNamespace(
            name="publishers/google/models/gemini-2.5-flash", supported_actions=None
        )
        assert models.is_chat_capable(none_meta) is True

    def test_vertex_none_actions_non_chat_still_excluded(self) -> None:
        # Even with supported_actions=None, the name-based gates still block non-chat models.
        for name in (
            "publishers/google/models/gemini-2.5-pro-tts",
            "publishers/google/models/gemini-2.5-flash-tts",
            "publishers/google/models/gemini-live-2.5-flash-native-audio",
            "publishers/google/models/gemini-3.1-flash-image",
            "publishers/google/models/gemini-embedding-2",
        ):
            none_meta = SimpleNamespace(name=name, supported_actions=None)
            assert models.is_chat_capable(none_meta) is False, name

    def test_bare_name_without_prefix(self) -> None:
        # Some SDK paths yield the id without a "models/" prefix.
        assert models.is_chat_capable(_meta("gemini-2.5-pro")) is True

    # --- allowlist behavior (real ids from the live catalog 2026-07-08) ---

    def test_gemini3_preview_included(self) -> None:
        # Valid Gemini 3 previews must be listed (hyphen form, no dot).
        assert models.is_chat_capable(_meta("models/gemini-3-pro-preview")) is True
        assert models.is_chat_capable(_meta("models/gemini-3-flash-preview")) is True
        assert models.is_chat_capable(_meta("models/gemini-3.1-pro-preview")) is True

    def test_latest_aliases_included(self) -> None:
        for alias in ("gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"):
            assert models.is_chat_capable(_meta(f"models/{alias}")) is True

    def test_non_gemini_families_excluded(self) -> None:
        # Real non-chat / non-gemini families that carry generateContent but must NOT list.
        for name in (
            "models/gemma-4-31b-it",
            "models/lyria-3-pro-preview",
            "models/nano-banana-pro-preview",
            "models/antigravity-preview-05-2026",
            "models/deep-research-pro-preview-12-2025",
        ):
            assert models.is_chat_capable(_meta(name)) is False, name

    def test_specialized_gemini_variants_excluded(self) -> None:
        # gemini-prefixed but not text-chat.
        for name in (
            "models/gemini-2.5-computer-use-preview-10-2025",
            "models/gemini-robotics-er-1.6-preview",
            "models/gemini-omni-flash-preview",
        ):
            assert models.is_chat_capable(_meta(name)) is False, name


class TestResolveLatest:
    """#69: pick the newest model per family from the live catalog."""

    LIVE = [  # Developer API catalog, 2026-09-17
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-transcribe",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-pro-latest",
        "gemini-2.5-pro-preview-tts",
        "gemini-3-pro-image-preview",
    ]

    def test_matches_googles_own_aliases(self) -> None:
        # Live probe: flash-latest -> 3.8-flash, flash-lite-latest -> 3.5-flash-lite,
        # pro-latest -> 3.1-pro-preview.
        assert models.resolve_latest(self.LIVE) == {
            "flash": "gemini-3.8-flash",
            "flash-lite": "gemini-3.5-flash-lite",
            "pro": "gemini-3.1-pro-preview",
        }

    def test_numeric_not_lexical_ordering(self) -> None:
        ids = ["gemini-3.8-flash", "gemini-3.10-flash", "gemini-3.9-flash"]
        assert models.resolve_latest(ids)["flash"] == "gemini-3.10-flash"

    def test_major_version_wins(self) -> None:
        assert models.resolve_latest(["gemini-3.9-flash", "gemini-4-flash"])["flash"] == (
            "gemini-4-flash"
        )

    def test_ga_preferred_over_preview_at_same_version(self) -> None:
        ids = ["gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite"]
        assert models.resolve_latest(ids)["flash-lite"] == "gemini-3.1-flash-lite"

    def test_newer_preview_beats_older_ga(self) -> None:
        assert models.resolve_latest(["gemini-2.5-pro", "gemini-3.1-pro-preview"])["pro"] == (
            "gemini-3.1-pro-preview"
        )

    def test_prefixed_names_and_missing_families(self) -> None:
        out = models.resolve_latest(["models/gemini-3.8-flash", "publishers/google/models/x"])
        assert out == {"flash": "gemini-3.8-flash"}

    @pytest.mark.parametrize(
        "model_id",
        [
            "gemini-3.1-pro-preview-customtools",
            "gemini-3.5-transcribe",
            "gemini-2.5-flash-001",
            "gemini-3-pro-image-preview",
            "gemini-flash-latest",
            "gemini-2.5-flash-preview-09-2025",
        ],
    )
    def test_variants_never_selected(self, model_id: str) -> None:
        assert models.resolve_latest([model_id]) == {}


class TestAliasFamily:
    @pytest.mark.parametrize(
        ("name", "family"),
        [
            ("flash", "flash"),
            ("flash-lite", "flash-lite"),
            ("pro", "pro"),
            ("FLASH", "flash"),
            ("gemini-flash-latest", "flash"),
            ("gemini-flash-lite-latest", "flash-lite"),
            ("gemini-pro-latest", "pro"),
        ],
    )
    def test_aliases(self, name: str, family: str) -> None:
        assert models.alias_family(name) == family

    @pytest.mark.parametrize("name", ["gemini-3.8-flash", "gemini-3.1-pro-preview", "gpt"])
    def test_concrete_ids_are_not_aliases(self, name: str) -> None:
        assert models.alias_family(name) is None


class TestSchemaHintAliases:
    def test_lists_aliases_with_resolution(self) -> None:
        latest = {"flash": "gemini-3.8-flash", "pro": "gemini-3.1-pro-preview"}
        hint = models.schema_hint(models.VERTEX, "gemini-3.8-flash", latest)
        assert "flash (→ gemini-3.8-flash)" in hint
        assert "pro (→ gemini-3.1-pro-preview)" in hint
        assert "flash-lite" in hint  # unresolved family still offered

    def test_aliases_offered_without_resolution(self) -> None:
        hint = models.schema_hint(models.DEVELOPER_API, "gemini-3.5-flash")
        assert "flash, flash-lite, pro" in hint


class TestFutureGenerations:
    """Finding 3: the list filter must accept every generation _model_family accepts."""

    @pytest.mark.parametrize("name", ["models/gemini-4-flash", "models/gemini-10.2-pro"])
    def test_later_generations_listed(self, name: str) -> None:
        assert models.is_chat_capable(_meta(name))

    @pytest.mark.parametrize("name", ["models/gemini-1.5-flash", "models/gemini-embedding-001"])
    def test_older_or_non_generation_excluded(self, name: str) -> None:
        assert not models.is_chat_capable(_meta(name))
