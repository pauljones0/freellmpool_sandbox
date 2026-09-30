"""Explicit upstream modality facts take precedence over generic name hints."""

import pytest

from freellmpool.discovery import normalize_models


@pytest.mark.parametrize("metadata", [
    {"task": {"name": "text-to-speech"}},
    {"model_type": "text-generation"},
    {"architecture": {"output_modalities": ["text"]}},
])
def test_explicit_modality_metadata_precedes_name_fallback(metadata: dict) -> None:
    model = normalize_models("groq", {"data": [{"id": "model", **metadata}]})[0]
    assert model["modalities"] == (["speech"] if "task" in metadata else ["chat"])
    assert model["metadata"]["modalities_inferred"] is False


def test_generic_chat_candidate_inference_remains_available() -> None:
    model = normalize_models("groq", {"data": [{"id": "model"}]})[0]
    assert model["modalities"] == ["chat"]
    assert model["metadata"]["modalities_inferred"] is True
