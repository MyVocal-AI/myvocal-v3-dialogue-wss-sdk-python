import asyncio
import base64
import json

import pytest

from myvocal_v3_dialogue_wss import (
    CallbackError,
    ConnectionOptions,
    ContextOptions,
    Input,
    MultiContextClient,
    ProtocolError,
    SDKTimeoutError,
    StateError,
)


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.close_code = None
        self.closed = False

    async def send(self, message):
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(message)

    async def close(self, code=1000):
        self.closed = True
        self.close_code = code
        await self.incoming.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def feed(self, payload):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        await self.incoming.put(payload)

    async def server_close(self, code=1000):
        self.close_code = code
        await self.incoming.put(None)


@pytest.fixture
def socket():
    return FakeSocket()


@pytest.fixture
def client(socket):
    async def connect_factory(url, api_key, timeout):
        assert api_key == "test-key"
        assert "priority=dedicated_concurrency" in url
        return socket

    return MultiContextClient(
        "test-key",
        ConnectionOptions(),
        connect_factory=connect_factory,
    )


async def test_open_send_and_control_messages_are_serialized(client, socket):
    await client.connect()
    await client.open_context(
        ContextOptions("ctx", ["voice-a"])
    )
    await client.send_inputs("ctx", [Input("Hello", "voice-a", True)])
    await client.flush("ctx")
    await client.keep_alive("ctx")
    assert [json.loads(item) for item in socket.sent] == [
        {"context_id": "ctx", "voices": ["voice-a"]},
        {
            "context_id": "ctx",
            "inputs": [
                {
                    "text": "Hello",
                    "voice_id": "voice-a",
                    "new_turn": True,
                }
            ],
        },
        {"context_id": "ctx", "flush": True},
        {"context_id": "ctx", "keep_alive": True},
    ]
    await client.abort()


async def test_audio_is_decoded_in_order_and_turn_final_is_not_context_final(
    client, socket
):
    chunks = []
    client.on_audio = lambda context_id, audio, alignment: chunks.append(audio)
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed(
        {
            "context_id": "ctx",
            "audio": base64.b64encode(b"one").decode(),
        }
    )
    await socket.feed(
        {
            "context_id": "ctx",
            "audio": base64.b64encode(b"two").decode(),
            "is_final_audio_for_turn": True,
        }
    )
    await asyncio.sleep(0)
    assert chunks == [b"one", b"two"]
    assert not client.context_done("ctx")
    await socket.feed({"context_id": "ctx", "is_final": True})
    await asyncio.sleep(0)
    assert client.context_done("ctx")
    await client.abort()


async def test_close_context_waits_for_is_final(client, socket):
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    task = asyncio.create_task(client.close_context("ctx", timeout=1))
    await asyncio.sleep(0)
    assert json.loads(socket.sent[-1]) == {
        "context_id": "ctx",
        "close_context": True,
    }
    assert not task.done()
    await socket.feed({"context_id": "ctx", "is_final": True})
    result = await task
    assert result.context_id == "ctx"
    await client.abort()


async def test_close_socket_requires_all_finals_and_close_1000(client, socket):
    await client.connect()
    await client.open_context(ContextOptions("a", ["voice-a"]))
    await client.open_context(ContextOptions("b", ["voice-b"]))
    task = asyncio.create_task(client.close_socket(timeout=1))
    await asyncio.sleep(0)
    assert json.loads(socket.sent[-1]) == {"close_socket": True}
    await socket.feed({"context_id": "a", "is_final": True})
    await socket.feed({"context_id": "b", "is_final": True})
    await socket.server_close(1000)
    result = await task
    assert result.close_code == 1000


async def test_protocol_error_remains_sticky_across_close_1000(client, socket):
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed(
        {
            "code": "INVALID_PARAMETER",
            "field": "inputs.voice_id",
            "message": "invalid voice",
        }
    )
    await socket.server_close(1000)
    await asyncio.sleep(0)
    with pytest.raises(ProtocolError) as error:
        await client.wait_closed()
    assert error.value.code == "INVALID_PARAMETER"
    assert error.value.field == "inputs.voice_id"


async def test_callback_failure_isolated_from_success(client, socket):
    errors = []

    def bad_callback(event):
        raise RuntimeError("boom")

    client.on_event = bad_callback
    client.on_error = errors.append
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed({"context_id": "ctx", "is_final": True})
    for _ in range(20):
        if errors:
            break
        await asyncio.sleep(0.01)
    assert client.context_done("ctx")
    assert isinstance(errors[0], CallbackError)
    await client.abort()


async def test_send_after_context_close_is_rejected(client, socket):
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    task = asyncio.create_task(client.close_context("ctx", timeout=1))
    await asyncio.sleep(0)
    with pytest.raises(StateError):
        await client.send_inputs("ctx", [Input("late", "voice-a")])
    await socket.feed({"context_id": "ctx", "is_final": True})
    await task
    await client.abort()


async def test_duplicate_context_and_socket_close_are_idempotent(
    client, socket
):
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    first_context = asyncio.create_task(
        client.close_context("ctx", timeout=1)
    )
    second_context = asyncio.create_task(
        client.close_context("ctx", timeout=1)
    )
    await asyncio.sleep(0)
    context_closes = [
        item
        for item in socket.sent
        if json.loads(item).get("close_context") is True
    ]
    assert len(context_closes) == 1
    await socket.feed({"context_id": "ctx", "is_final": True})
    first_result, second_result = await asyncio.gather(
        first_context, second_context
    )
    assert first_result.context_id == second_result.context_id == "ctx"

    first_socket = asyncio.create_task(client.close_socket(timeout=1))
    second_socket = asyncio.create_task(client.close_socket(timeout=1))
    await asyncio.sleep(0)
    socket_closes = [
        item
        for item in socket.sent
        if json.loads(item).get("close_socket") is True
    ]
    assert len(socket_closes) == 1
    await socket.server_close(1000)
    first_close, second_close = await asyncio.gather(
        first_socket, second_socket
    )
    assert first_close.close_code == second_close.close_code == 1000


async def test_policy_close_without_json_is_protocol_error(client, socket):
    await client.connect()
    await socket.server_close(1008)
    with pytest.raises(ProtocolError) as error:
        await client.wait_closed()
    assert error.value.code == "POLICY_CLOSE"
    assert error.value.close_code == 1008


async def test_binary_json_and_completion_callbacks(client, socket):
    context_finals = []
    closes = []
    client.on_context_final = context_finals.append
    client.on_close = lambda code, reason: closes.append((code, reason))
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed(
        json.dumps({"context_id": "ctx", "is_final": True}).encode()
    )
    await socket.server_close(1000)
    await client.wait_closed()
    assert context_finals[0].context_id == "ctx"
    assert closes[0][0] == 1000


async def test_socket_close_timeout_uses_timeout_category(client, socket):
    await client.connect()
    with pytest.raises(SDKTimeoutError):
        await client.close_socket(timeout=0.01)
    assert socket.closed


async def test_active_context_limit_and_context_timeout(client, socket):
    await client.connect()
    for index in range(5):
        await client.open_context(
            ContextOptions("ctx-%d" % index, ["voice-a"])
        )
    with pytest.raises(StateError):
        await client.open_context(ContextOptions("ctx-5", ["voice-a"]))
    with pytest.raises(SDKTimeoutError):
        await client.close_context("ctx-0", timeout=0.01)
    assert not socket.closed
    await client.abort()


async def test_close_1000_before_context_final_is_protocol_error(
    client, socket
):
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.server_close(1000)
    with pytest.raises(ProtocolError) as error:
        await client.wait_closed()
    assert error.value.code == "MISSING_FINAL"


async def test_context_error_isolated_and_string_error_is_terminal(
    client, socket
):
    await client.connect()
    await client.open_context(ContextOptions("bad", ["voice-a"]))
    await client.open_context(ContextOptions("good", ["voice-b"]))
    await socket.feed(
        {
            "error": "TASK_FAILED",
            "message": "bad context",
            "context_id": "bad",
        }
    )
    await asyncio.sleep(0)

    await client.send_inputs("good", [Input("continue", "voice-b")])
    with pytest.raises(ProtocolError) as error:
        await client.close_context("bad", timeout=1)
    assert error.value.code == "TASK_FAILED"
    assert error.value.context_id == "bad"
    await client.abort()


async def test_close_socket_after_terminal_close_preserves_sticky_error(
    client, socket
):
    await client.connect()
    await socket.feed({"error": "SESSION_FAILED", "message": "failed"})
    await socket.server_close(1000)
    with pytest.raises(ProtocolError) as error:
        await client.close_socket(timeout=1)
    assert error.value.code == "SESSION_FAILED"


async def test_slow_control_callback_does_not_block_reader(client, socket):
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def slow_event(_):
        callback_started.set()
        await release_callback.wait()

    client.on_event = slow_event
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed({"context_id": "ctx", "status": "progress"})
    await callback_started.wait()
    await socket.feed({"context_id": "ctx", "is_final": True})
    await asyncio.sleep(0.05)
    assert client.context_done("ctx")
    release_callback.set()
    await client.abort()


async def test_control_event_storm_is_bounded(client, socket):
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    received = []

    async def slow_event(event):
        received.append(event)
        callback_started.set()
        if len(received) == 1:
            await release_callback.wait()

    client.on_event = slow_event
    await client.connect()
    await client.open_context(ContextOptions("ctx", ["voice-a"]))
    await socket.feed({"context_id": "ctx", "sequence": 0})
    await callback_started.wait()
    for sequence in range(1, 201):
        await socket.feed({"context_id": "ctx", "sequence": sequence})
    await socket.feed({"context_id": "ctx", "is_final": True})
    await asyncio.sleep(0.05)
    assert client.context_done("ctx")
    assert client._control_queue.qsize() <= 16
    release_callback.set()
    await client.abort()
