# MyVocal Dialogue WebSocket SDK for Python

Async Python client for multi-role dialogue synthesis. Requires Python 3.9 or later and an enterprise TTS API Key with dedicated concurrency.

## Install

Download and extract [SDK v1.1.2](https://github.com/MyVocal-AI/myvocal-v3-dialogue-wss-sdk-python/releases/tag/v1.1.2), then run from the extracted folder:

```bash
python -m pip install dist/myvocal_v3_dialogue_wss-1.1.2-py3-none-any.whl
```

The wheel installs the required `websockets` dependency. Use the supplied wheel; this package is not available on PyPI.

## Choose a model

Supported models: `myvocal_v3` (default), `myvocal_v4`, and `myvocal_v3_turbo`.
Use these SDKs with the dialogue Multi-Context WebSocket endpoint. `myvocal_v4` and `myvocal_v3_turbo` do not support ordinary TTS WebSocket endpoints.

```python
from myvocal_v3_dialogue_wss import ConnectionOptions

options = ConnectionOptions(model_id="myvocal_v4", format="mp3_128")
# Or model_id="myvocal_v3_turbo"
```

## Output languages

`myvocal_v4` and `myvocal_v3_turbo` support the same 94 output languages. Use `zh` for Mandarin Chinese and `yue` for Cantonese. See the [language list](https://api-docs.myvocal.ai/64249845f0#输出语言) for all supported codes.

```python
options = ConnectionOptions(model_id="myvocal_v4", language_code="yue")
```

Omit the language option to select a language from the input text. Provide text in the desired language; the option does not translate it.

## Run the example

Set the following environment variables before running the example:

```bash
export MYVOCAL_TTS_API_KEY='YOUR_API_KEY'
export MYVOCAL_VOICE_ID='YOUR_VOICE_ID'
export MYVOCAL_MODEL_ID='myvocal_v4'
```

PowerShell:

```powershell
$env:MYVOCAL_TTS_API_KEY='YOUR_API_KEY'
$env:MYVOCAL_VOICE_ID='YOUR_VOICE_ID'
$env:MYVOCAL_MODEL_ID='myvocal_v4'
```

`MYVOCAL_MODEL_ID` is optional; the default is `myvocal_v3`. Running the example makes a real synthesis request and consumes character usage. It prints audio byte counts and the connection close code.

```bash
python examples/basic.py
```

## Quick start

```python
import asyncio
import os
from myvocal_v3_dialogue_wss import (
    ConnectionOptions, ContextOptions, Input, MultiContextClient,
)

async def main():
    voice = os.environ["MYVOCAL_VOICE_ID"]
    client = MultiContextClient(
        os.environ["MYVOCAL_TTS_API_KEY"],
        ConnectionOptions(model_id="myvocal_v4"),
    )
    client.on_audio = lambda context_id, audio, alignment: print(context_id, len(audio))
    await client.connect()
    await client.open_context(ContextOptions(
        context_id="scene-1",
        voices=[voice],
        inputs=[Input("Hello from MyVocal.", voice, True)],
    ))
    result = await client.close_socket(timeout=60)
    print(result.close_code)

asyncio.run(main())
```

## Use the SDK in your application

- [Usage guide](USAGE.md): connection options, contexts, audio callbacks, completion, errors, and timeouts.
- [API reference](https://api-docs.myvocal.ai/3892090w0): messages, model support, and limits.
- [Character usage](https://api-docs.myvocal.ai/336818975e0).

Keep callbacks short and handle audio separately for each context. Wait for context final events and Close Code `1000` when finishing the connection. The SDK does not automatically reconnect or replay text.

Licensed under [Apache License 2.0](LICENSE). Dependency notices are in [NOTICE](NOTICE).
