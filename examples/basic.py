import asyncio
import os

from myvocal_v3_dialogue_wss import (
    ConnectionOptions,
    ContextOptions,
    Input,
    MultiContextClient,
    VoiceSettings,
)


async def main():
    api_key = os.environ["MYVOCAL_TTS_API_KEY"]
    voice_id = os.environ["MYVOCAL_VOICE_ID"]
    total_audio = 0

    def on_audio(context_id, audio, alignment):
        nonlocal total_audio
        total_audio += len(audio)
        print("audio", context_id, len(audio), bool(alignment))

    client = MultiContextClient(
        api_key,
        ConnectionOptions(
            format="mp3_128",
            language_code="en",
            timestamps=True,
        ),
    )
    client.on_audio = on_audio
    await client.connect()
    await client.open_context(
        ContextOptions(
            "example-a",
            [voice_id],
            VoiceSettings(0.5),
            [Input("Hello from the first context.", voice_id, True)],
        )
    )
    await client.open_context(
        ContextOptions(
            "example-b",
            [voice_id],
            VoiceSettings(0.5),
            [Input("Hello from the second context.", voice_id, True)],
        )
    )
    result = await client.close_socket(timeout=60)
    print("close", result.close_code, "audio_bytes", total_audio)


if __name__ == "__main__":
    asyncio.run(main())
