import asyncio
import base64
import inspect
import json

from .errors import (
    AuthenticationError,
    CallbackError,
    ProtocolError,
    SDKTimeoutError,
    StateError,
    TransportError,
    ValidationError,
)
from .models import (
    CloseResult,
    ConnectionOptions,
    ContextOptions,
    ContextResult,
    Input,
    ServerEvent,
)


class _ContextState:
    def __init__(self, options, future):
        self.options = options
        self.future = future
        self.closing = False
        self.done = False
        self.error = None
        self.audio = bytearray()


class MultiContextClient:
    def __init__(
        self,
        api_key,
        options=None,
        connect_factory=None,
        accumulate_audio=False,
    ):
        if not isinstance(api_key, str) or not api_key:
            raise ValidationError("api_key must not be empty")
        self._api_key = api_key
        self.options = options or ConnectionOptions()
        self._connect_factory = connect_factory or self._default_connect
        self._accumulate_audio = accumulate_audio
        self._socket = None
        self._reader_task = None
        self._closed_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._control_queue = asyncio.Queue(maxsize=16)
        self._callback_task = None
        self._contexts = {}
        self._sticky_error = None
        self._closing_socket = False
        self._aborting = False
        self._socket_result = None
        self.on_audio = None
        self.on_event = None
        self.on_context_final = None
        self.on_close = None
        self.on_error = None

    async def _default_connect(self, url, api_key, timeout):
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            from websockets import connect
        try:
            return await connect(
                url,
                additional_headers={"api-key": api_key},
                open_timeout=timeout,
                max_size=None,
            )
        except TypeError:
            return await connect(
                url,
                extra_headers={"api-key": api_key},
                open_timeout=timeout,
                max_size=None,
            )

    async def connect(self):
        if self._socket is not None:
            raise StateError("client is already connected")
        try:
            self._socket = await self._connect_factory(
                self.options.build_url(),
                self._api_key,
                self.options.connect_timeout,
            )
        except Exception as error:
            status = getattr(error, "status_code", None)
            response = getattr(error, "response", None)
            status = status or getattr(response, "status_code", None)
            if status in (401, 403):
                raise AuthenticationError(
                    "WebSocket authentication failed"
                )
            raise TransportError("WebSocket connection failed: %s" % error)
        self._callback_task = asyncio.create_task(self._callback_loop())
        self._reader_task = asyncio.create_task(self._read_loop())
        return self

    async def open_context(self, options):
        self._require_connected()
        if not isinstance(options, ContextOptions):
            raise ValidationError("options must be ContextOptions")
        if self._closing_socket:
            raise StateError("socket is closing")
        if options.context_id in self._contexts:
            raise StateError("context_id is already registered")
        active = sum(not item.done for item in self._contexts.values())
        if active >= 5:
            raise StateError("at most 5 active contexts are allowed")
        future = asyncio.get_running_loop().create_future()
        self._contexts[options.context_id] = _ContextState(options, future)
        await self._send(options.to_wire())

    async def send_inputs(self, context_id, inputs):
        state = self._require_open_context(context_id)
        values = list(inputs)
        if not values:
            raise ValidationError("inputs must not be empty")
        registered = set(state.options.voices)
        if any(not isinstance(item, Input) for item in values):
            raise ValidationError("inputs must contain Input values")
        if any(item.voice_id not in registered for item in values):
            raise ValidationError("input voice_id must be registered")
        await self._send(
            {
                "context_id": context_id,
                "inputs": [item.to_wire() for item in values],
            }
        )

    async def flush(self, context_id):
        self._require_open_context(context_id)
        await self._send({"context_id": context_id, "flush": True})

    async def keep_alive(self, context_id):
        self._require_open_context(context_id)
        await self._send({"context_id": context_id, "keep_alive": True})

    async def close_context(self, context_id, timeout=60):
        self._require_connected()
        state = self._contexts.get(context_id)
        if state is None:
            raise StateError("unknown context_id")
        if state.done:
            if state.error is not None:
                raise state.error
            self._raise_sticky()
            return state.future.result()
        if not state.closing:
            state.closing = True
            await self._send(
                {"context_id": context_id, "close_context": True}
            )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(state.future), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise SDKTimeoutError("timed out waiting for context final")
        if state.error is not None:
            raise state.error
        self._raise_sticky()
        return result

    async def close_socket(self, timeout=60):
        if self._socket_result is not None:
            self._raise_sticky()
            return self._socket_result
        if self._socket is None:
            raise StateError("client has not connected")
        if self._closing_socket:
            try:
                await asyncio.wait_for(
                    self._closed_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self.abort()
                raise SDKTimeoutError(
                    "timed out waiting for socket completion"
                )
            self._raise_sticky()
            close_code = getattr(self._socket, "close_code", None)
            if close_code != 1000:
                raise TransportError(
                    "WebSocket closed with code %s" % close_code
                )
            self._socket_result = CloseResult(close_code)
            return self._socket_result
        self._raise_sticky()
        self._require_connected()
        self._closing_socket = True
        active = [
            state.future for state in self._contexts.values() if not state.done
        ]
        await self._send({"close_socket": True})

        async def wait_for_completion():
            if active:
                await asyncio.gather(
                    *(asyncio.shield(item) for item in active)
                )
            await self._closed_event.wait()

        try:
            await asyncio.wait_for(wait_for_completion(), timeout=timeout)
        except asyncio.TimeoutError:
            await self.abort()
            raise SDKTimeoutError(
                "timed out waiting for socket completion"
            )
        self._raise_sticky()
        close_code = getattr(self._socket, "close_code", None)
        if close_code != 1000:
            raise TransportError(
                "WebSocket closed with code %s" % close_code
            )
        self._socket_result = CloseResult(close_code)
        return self._socket_result

    async def wait_closed(self):
        if self._socket is None:
            raise StateError("client has not connected")
        await self._closed_event.wait()
        self._raise_sticky()
        close_code = getattr(self._socket, "close_code", None)
        if not self._aborting and close_code != 1000:
            raise TransportError(
                "WebSocket closed with code %s" % close_code
            )
        return CloseResult(close_code)

    async def abort(self):
        if self._socket is None:
            return
        self._aborting = True
        try:
            await self._socket.close(code=1000)
        finally:
            if self._reader_task is not None:
                await self._reader_task
            if self._callback_task is not None:
                self._callback_task.cancel()
                try:
                    await self._callback_task
                except asyncio.CancelledError:
                    pass

    def context_done(self, context_id):
        state = self._contexts.get(context_id)
        return bool(state and state.done)

    async def _send(self, payload):
        self._raise_sticky()
        try:
            async with self._send_lock:
                await self._socket.send(
                    json.dumps(
                        payload, separators=(",", ":"), ensure_ascii=False
                    )
                )
        except Exception as error:
            transport_error = TransportError(
                "WebSocket send failed: %s" % error
            )
            self._set_sticky(transport_error)
            raise transport_error

    async def _read_loop(self):
        try:
            async for message in self._socket:
                await self._handle_message(message)
        except Exception as error:
            if not self._aborting:
                code = getattr(error, "code", None)
                if code == 1008:
                    self._set_sticky(
                        ProtocolError(
                            "POLICY_CLOSE",
                            "WebSocket policy close",
                            close_code=1008,
                        )
                    )
                else:
                    self._set_sticky(
                        TransportError(
                            "WebSocket read failed: %s" % error
                        )
                    )
        finally:
            if not self._aborting:
                close_code = getattr(self._socket, "close_code", None)
                unfinished = any(
                    not state.done for state in self._contexts.values()
                )
                if close_code == 1008:
                    self._set_sticky(
                        ProtocolError(
                            "POLICY_CLOSE",
                            "WebSocket policy close",
                            close_code=1008,
                        )
                    )
                elif close_code != 1000:
                    self._set_sticky(
                        TransportError(
                            "WebSocket closed with code %s" % close_code
                        )
                    )
                elif unfinished and self._sticky_error is None:
                    self._set_sticky(
                        ProtocolError(
                            "MISSING_FINAL",
                            "socket closed before every context was final",
                        )
                    )
            self._queue_callback(
                "on_close",
                getattr(self._socket, "close_code", None),
                getattr(self._socket, "close_reason", None),
                critical=True,
            )
            self._queue_control_item((True, None, None))
            self._closed_event.set()

    async def _handle_message(self, message):
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError:
                self._set_sticky(
                    ProtocolError(
                        "INVALID_FRAME",
                        "binary frame was not UTF-8 JSON",
                    )
                )
                return
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            self._set_sticky(
                ProtocolError("INVALID_JSON", "server frame was not JSON")
            )
            return
        if not isinstance(payload, dict):
            self._set_sticky(
                ProtocolError("INVALID_JSON", "server event must be an object")
            )
            return

        error = self._protocol_error(payload)
        if error is not None:
            context_id = error.context_id
            state = self._contexts.get(context_id) if context_id else None
            if state is not None:
                state.error = error
                state.done = True
                if not state.future.done():
                    state.future.set_result(
                        ContextResult(context_id, bytes(state.audio))
                    )
            else:
                self._set_sticky(error)
            self._queue_callback("on_error", error, critical=True)
            return

        context_id = payload.get("context_id")
        state = self._contexts.get(context_id) if context_id else None
        audio = b""
        encoded_audio = payload.get("audio")
        if encoded_audio:
            try:
                audio = base64.b64decode(encoded_audio, validate=True)
            except (ValueError, TypeError):
                error = ProtocolError(
                    "INVALID_AUDIO", "audio was not valid base64"
                )
                self._set_sticky(error)
                self._queue_callback("on_error", error, critical=True)
                return
            if state is None:
                error = ProtocolError(
                    "UNKNOWN_CONTEXT",
                    "audio event referenced an unknown context",
                    "context_id",
                )
                self._set_sticky(error)
                self._queue_callback("on_error", error, critical=True)
                return
            if self._accumulate_audio:
                state.audio.extend(audio)
            await self._invoke_callback(
                "on_audio", context_id, audio, payload.get("alignment")
            )

        if payload.get("is_final") is True:
            if state is None:
                error = ProtocolError(
                    "UNKNOWN_CONTEXT",
                    "final event referenced an unknown context",
                    "context_id",
                )
                self._set_sticky(error)
                self._queue_callback("on_error", error, critical=True)
                return
            state.done = True
            if not state.future.done():
                result = ContextResult(context_id, bytes(state.audio))
                self._queue_callback(
                    "on_context_final", result, critical=True
                )
                state.future.set_result(result)

        self._queue_callback("on_event", ServerEvent(payload))

    def _protocol_error(self, payload):
        code = payload.get("code") or payload.get("error_code")
        error_value = payload.get("error")
        if not code and isinstance(error_value, dict):
            code = error_value.get("code")
        if not code and isinstance(error_value, str):
            code = error_value
        if not code:
            return None
        message = payload.get("message") or payload.get("status_msg")
        field = payload.get("field")
        if isinstance(payload.get("error"), dict):
            message = message or payload["error"].get("message")
            field = field or payload["error"].get("field")
        return ProtocolError(
            str(code),
            message or str(code),
            field,
            payload.get("context_id"),
        )

    async def _invoke_callback(self, name, *args):
        callback = getattr(self, name)
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            if name == "on_error":
                return
            callback_error = CallbackError(name, error)
            error_callback = self.on_error
            if error_callback is not None:
                try:
                    result = error_callback(callback_error)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass

    def _queue_callback(self, name, *args, critical=False):
        if getattr(self, name) is None:
            return
        self._queue_control_item((critical, name, args))

    def _queue_control_item(self, item):
        if self._control_queue.full():
            critical = item[0]
            if not critical:
                return
            try:
                self._control_queue.get_nowait()
                self._control_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self._control_queue.put_nowait(item)

    async def _callback_loop(self):
        while True:
            item = await self._control_queue.get()
            try:
                _, name, args = item
                if name is None:
                    return
                await self._invoke_callback(name, *args)
            finally:
                self._control_queue.task_done()

    def _require_connected(self):
        self._raise_sticky()
        if self._socket is None or self._closed_event.is_set():
            raise StateError("client is not connected")

    def _require_open_context(self, context_id):
        self._require_connected()
        state = self._contexts.get(context_id)
        if state is None:
            raise StateError("unknown context_id")
        if state.closing or state.done:
            raise StateError("context is closing or closed")
        return state

    def _set_sticky(self, error):
        if self._sticky_error is None:
            self._sticky_error = error
            for state in self._contexts.values():
                if not state.future.done():
                    state.future.set_result(
                        ContextResult(
                            state.options.context_id, bytes(state.audio)
                        )
                    )

    def _raise_sticky(self):
        if self._sticky_error is not None:
            raise self._sticky_error
