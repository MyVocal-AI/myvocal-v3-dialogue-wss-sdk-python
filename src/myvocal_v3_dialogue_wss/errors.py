class SDKError(Exception):
    """Base error for the SDK."""


class ValidationError(SDKError):
    """Raised when a client-side option violates the public contract."""


class StateError(SDKError):
    """Raised when an operation is invalid for the current lifecycle state."""


class TransportError(SDKError):
    """Raised when the WebSocket transport cannot complete the operation."""


class AuthenticationError(SDKError):
    """Raised when the WebSocket handshake rejects authentication."""


class SDKTimeoutError(SDKError):
    """Raised when a bounded SDK operation exceeds its deadline."""


class ProtocolError(SDKError):
    """Raised when the server reports a terminal protocol error."""

    def __init__(
        self,
        code,
        message,
        field=None,
        context_id=None,
        close_code=None,
    ):
        super().__init__(message)
        self.code = code
        self.field = field
        self.context_id = context_id
        self.close_code = close_code


class CallbackError(SDKError):
    """Reports a user callback failure without changing protocol outcome."""

    def __init__(self, callback_name, cause):
        super().__init__("%s callback failed: %s" % (callback_name, cause))
        self.callback_name = callback_name
        self.cause = cause
