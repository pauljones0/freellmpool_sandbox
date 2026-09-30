"""Modality regression cases for ambiguous public model listings."""

import pytest

from freellmpool.discovery import normalize_models


@pytest.mark.parametrize("language", ["es-es", "de-de", "it-it", "en-us"])
def test_ovh_riva_tts_listing_is_speech_instead_of_chat(language: str) -> None:
    # OVH's public OpenAI listing omits type/task even for its speech endpoints.
    row = {"id": f"nvr-tts-{language}", "object": "model", "owned_by": "NVIDIA Riva",
           "context_length": 0, "max_completion_tokens": 0,
           "pricing": {"prompt": "0", "completion": "0", "request": "0"}}
    model = normalize_models("ovh", {"data": [row]})[0]
    assert model["modalities"] == ["speech"]
    assert model["metadata"]["modalities_inferred"] is True


@pytest.mark.parametrize("metadata", [
    {"task": {"name": "text-to-speech"}},
    {"model_type": "text-generation"},
    {"architecture": {"output_modalities": ["text"]}},
])
def test_ovh_explicit_modality_metadata_precedes_name_fallback(metadata: dict) -> None:
    model = normalize_models("ovh", {"data": [{"id": "nvr-tts-en-us", **metadata}]})[0]
    assert model["modalities"] == (["speech"] if "task" in metadata else ["chat"])
    assert model["metadata"]["modalities_inferred"] is False


def test_ovh_chat_inference_and_other_providers_remain_unchanged() -> None:
    rows = normalize_models("ovh", {"data": [{"id": "Qwen3.6-27B"}]})
    assert rows[0]["modalities"] == ["chat"]
    assert rows[0]["metadata"]["modalities_inferred"] is True
    assert normalize_models("groq", {"data": [{"id": "nvr-tts-en-us"}]})[0]["modalities"] == ["chat"]
