import asyncio
import hashlib
import json
import os

import pytest

from myvocal_v3_dialogue_wss import (
    ConnectionOptions,
    ContextOptions,
    Input,
    MultiContextClient,
    VoiceSettings,
)


@pytest.mark.live
async def test_live_two_contexts_close_socket():
    api_key = os.environ.get("MYVOCAL_TTS_API_KEY")
    voice_id = os.environ.get("MYVOCAL_VOICE_ID")
    if not api_key or not voice_id:
        pytest.skip("live credentials are process-only")

    audio = bytearray()
    audio_chunks = 0
    alignments = 0
    turn_finals = 0

    def on_audio(context_id, value, alignment):
        nonlocal audio_chunks, alignments
        assert context_id in {"py-live-a", "py-live-b"}
        audio.extend(value)
        audio_chunks += 1
        if alignment:
            alignments += 1

    def on_event(event):
        nonlocal turn_finals
        if event.raw.get("is_final_audio_for_turn") is True:
            turn_finals += 1

    client = MultiContextClient(
        api_key,
        ConnectionOptions(
            format="pcm_16000",
            language_code="en",
            timestamps=True,
            text_normalization="auto",
            seed=821001,
            enable_logging=False,
        ),
    )
    client.on_audio = on_audio
    client.on_event = on_event
    await client.connect()
    for context_id, text in (
        ("py-live-a", "Hello from the first SDK context."),
        ("py-live-b", "Hello from the second SDK context."),
    ):
        await client.open_context(
            ContextOptions(
                context_id,
                [voice_id],
                VoiceSettings(0.5),
                [Input(text, voice_id, True)],
            )
        )
    result = await client.close_socket(timeout=60)

    assert result.close_code == 1000
    assert client.context_done("py-live-a")
    assert client.context_done("py-live-b")
    assert audio_chunks > 0
    assert len(audio) > 0
    assert alignments > 0
    print(
        json.dumps(
            {
                "sdk": "python",
                "contextsFinal": 2,
                "turnFinals": turn_finals,
                "audioChunks": audio_chunks,
                "audioBytes": len(audio),
                "audioSha256": hashlib.sha256(audio).hexdigest(),
                "alignmentEvents": alignments,
                "closeCode": result.close_code,
                "pass": True,
            },
            separators=(",", ":"),
        )
    )
