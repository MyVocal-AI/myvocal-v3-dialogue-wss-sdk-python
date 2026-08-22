# MyVocal V3 Dialogue WebSocket SDK for Python

Async Python 3.9+ client for multi-context V3 dialogue synthesis.

## Local Installation

From the extracted ZIP:

```bash
python -m pip install dist/myvocal_v3_dialogue_wss-1.0.1-py3-none-any.whl
```

The package is not yet published to PyPI.

## Quick Start

```python
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
    key = os.environ["MYVOCAL_TTS_API_KEY"]
    voice = os.environ["MYVOCAL_VOICE_ID"]
    client = MultiContextClient(key, ConnectionOptions(format="mp3_128"))
    client.on_audio = lambda context_id, audio, alignment: print(
        context_id, len(audio)
    )

    await client.connect()
    await client.open_context(
        ContextOptions(
            "scene-1",
            [voice],
            VoiceSettings(0.5),
            [Input("Hello from MyVocal.", voice, True)],
        )
    )
    result = await client.close_socket(timeout=60)
    print(result.close_code)


asyncio.run(main())
```

## Options

- `format`: `mp3_64`, `mp3_96`, `mp3_128`, `mp3_192`, `pcm_8000`,
  `pcm_16000`, `pcm_22050`, `pcm_24000`, `pcm_44100`, `ulaw_8000`
- `language_code`: optional two-letter language code
- `timestamps`: enables alignment data
- `text_normalization`: `auto`, `on`, or `off`
- `seed`: optional integer from `1` to `4294967295`
- `enable_logging`: request-side service logging option

Use `keep_alive(context_id)` before a context reaches its 20-second
application idle timeout. WebSocket ping/pong does not replace this message.

`close_context()` waits for that context's `is_final`. `close_socket()` waits
for all active contexts to become final and for Close Code `1000`. The SDK does
not reconnect or replay text automatically.

Audio callbacks run directly on the reader and must return quickly. Control
and lifecycle callbacks use a 16-slot non-blocking queue; lifecycle callbacks
take priority during an event storm. Callback exceptions are reported as
`CallbackError` without changing a successful protocol result.

A server error with a registered `context_id` terminates only that context.
Connection-level errors remain sticky and are returned by later close calls.

Successfully generated text is charged at twice the standard TTS character
usage.

## Verification

```bash
python -m pytest -q
```

The live test is skipped unless `MYVOCAL_TTS_API_KEY` and
`MYVOCAL_VOICE_ID` are present in the current process.
