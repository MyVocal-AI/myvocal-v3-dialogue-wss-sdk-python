import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from myvocal_v3_dialogue_wss import (
    ConnectionOptions,
    ContextOptions,
    Input,
    ValidationError,
    VoiceSettings,
)


VECTORS = json.loads(
    (
        Path(__file__).parents[1]
        / "test-vectors"
        / "contract-cases.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("model", ["myvocal_v3", "myvocal_v4", "myvocal_v3_turbo", "future/model +1"])
def test_model_selection_is_encoded_once_without_whitelist(model):
    query = parse_qs(urlparse(ConnectionOptions(model_id=model).build_url()).query)
    assert query["model_id"] == [model]
    assert query["priority"] == ["dedicated_concurrency"]


def test_existing_positional_constructor_keeps_default_model():
    options = ConnectionOptions("mp3_128", "en", False, "on", None, True,
                                "wss://example.invalid/multi", 12.0)
    assert options.connect_timeout == 12.0
    assert parse_qs(urlparse(options.build_url()).query)["model_id"] == ["myvocal_v3"]


def test_url_contains_fixed_query_and_defaults():
    url = ConnectionOptions().build_url()
    query = parse_qs(urlparse(url).query)
    assert query["priority"] == ["dedicated_concurrency"]
    assert query["model_id"] == ["myvocal_v3"]
    assert query["format"] == ["mp3_128"]
    assert query["timestamps"] == ["false"]
    assert query["text_normalization"] == ["on"]
    assert query["enable_logging"] == ["true"]
    assert "seed" not in query
    assert "language_code" not in query


@pytest.mark.parametrize("format_name", VECTORS["formats"])
def test_all_public_formats_are_accepted(format_name):
    assert f"format={format_name}" in ConnectionOptions(
        format=format_name
    ).build_url()


@pytest.mark.parametrize("seed", VECTORS["seed"]["valid"])
def test_valid_seed_is_serialized(seed):
    assert f"seed={seed}" in ConnectionOptions(seed=seed).build_url()


@pytest.mark.parametrize("seed", VECTORS["seed"]["invalid"])
def test_invalid_seed_is_rejected(seed):
    with pytest.raises(ValidationError):
        ConnectionOptions(seed=seed)


def test_language_is_lowercased_and_invalid_language_is_rejected():
    assert "language_code=en" in ConnectionOptions(
        language_code="EN"
    ).build_url()
    with pytest.raises(ValidationError):
        ConnectionOptions(language_code="english")
    with pytest.raises(ValidationError):
        ConnectionOptions(language_code="éñ")


@pytest.mark.parametrize("model", ["myvocal_v3", "myvocal_v4", "myvocal_v3_turbo"])
@pytest.mark.parametrize("code", ["en", "zh", "ast", "yue", "ceb", "fil", "ckb", "kam"])
def test_supported_language_codes_reach_the_connection_query(model, code):
    options = ConnectionOptions(model_id=model, language_code=code.upper())
    query = parse_qs(urlparse(options.build_url()).query)
    assert query["language_code"] == [code]
    assert query["model_id"] == [model]


@pytest.mark.parametrize("code", ["e", "engl", "en-US", "a1", " yue", "粤语", ""])
def test_language_code_format_remains_validated(code):
    with pytest.raises(ValidationError):
        ConnectionOptions(language_code=code)


@pytest.mark.parametrize("stability", VECTORS["stability"]["valid"])
def test_valid_stability(stability):
    assert VoiceSettings(stability=stability).to_wire() == {
        "stability": stability
    }


@pytest.mark.parametrize("stability", VECTORS["stability"]["invalid"])
def test_invalid_stability(stability):
    with pytest.raises(ValidationError):
        VoiceSettings(stability=stability)


def test_context_and_input_wire_shape_omit_optional_values():
    context = ContextOptions(
        context_id="scene-1",
        voices=["voice-a"],
        voice_settings=VoiceSettings(0.5),
        inputs=[Input("Hello", "voice-a")],
    )
    assert context.to_wire() == {
        "context_id": "scene-1",
        "voices": ["voice-a"],
        "voice_settings": {"stability": 0.5},
        "inputs": [{"text": "Hello", "voice_id": "voice-a"}],
    }


def test_context_rejects_too_many_voices_and_unregistered_input_voice():
    with pytest.raises(ValidationError):
        ContextOptions("scene", [f"voice-{i}" for i in range(11)])
    with pytest.raises(ValidationError):
        ContextOptions(
            "scene",
            ["voice-a"],
            inputs=[Input("Hello", "voice-b")],
        )
