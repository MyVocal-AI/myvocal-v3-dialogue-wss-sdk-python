from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from .errors import ValidationError


ENDPOINT = (
    "wss://api.voicelibrary.co/enterprise/v1/"
    "text-to-dialogue/websocket/multi"
)
FORMATS = {
    "mp3_64",
    "mp3_96",
    "mp3_128",
    "mp3_192",
    "pcm_8000",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_44100",
    "ulaw_8000",
}
NORMALIZATION_MODES = {"auto", "on", "off"}


@dataclass
class ConnectionOptions:
    format: str = "mp3_128"
    language_code: Optional[str] = None
    timestamps: bool = False
    text_normalization: str = "on"
    seed: Optional[int] = None
    enable_logging: bool = True
    endpoint: str = ENDPOINT
    connect_timeout: float = 30.0

    def __post_init__(self):
        if self.format not in FORMATS:
            raise ValidationError("unsupported format")
        if self.language_code is not None:
            if (
                not isinstance(self.language_code, str)
                or len(self.language_code) != 2
                or not self.language_code.isascii()
                or not self.language_code.isalpha()
            ):
                raise ValidationError("language_code must be ISO 639-1")
            self.language_code = self.language_code.lower()
        if self.text_normalization not in NORMALIZATION_MODES:
            raise ValidationError("unsupported text_normalization")
        if self.seed is not None:
            if (
                isinstance(self.seed, bool)
                or not isinstance(self.seed, int)
                or self.seed < 1
                or self.seed > 4294967295
            ):
                raise ValidationError("seed must be between 1 and 4294967295")
        if self.connect_timeout <= 0:
            raise ValidationError("connect_timeout must be positive")

    def build_url(self):
        query = {
            "priority": "dedicated_concurrency",
            "model_id": "myvocal_v3",
            "format": self.format,
            "timestamps": str(self.timestamps).lower(),
            "text_normalization": self.text_normalization,
            "enable_logging": str(self.enable_logging).lower(),
        }
        if self.language_code is not None:
            query["language_code"] = self.language_code
        if self.seed is not None:
            query["seed"] = str(self.seed)
        return "%s?%s" % (self.endpoint, urlencode(query))


@dataclass
class VoiceSettings:
    stability: float

    def __post_init__(self):
        if (
            isinstance(self.stability, bool)
            or not isinstance(self.stability, (int, float))
            or self.stability < 0
            or self.stability > 1
        ):
            raise ValidationError("stability must be between 0 and 1")

    def to_wire(self):
        return {"stability": self.stability}


@dataclass
class Input:
    text: str
    voice_id: str
    new_turn: bool = False

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text:
            raise ValidationError("input text must not be empty")
        if not isinstance(self.voice_id, str) or not self.voice_id:
            raise ValidationError("voice_id must not be empty")

    def to_wire(self):
        value = {"text": self.text, "voice_id": self.voice_id}
        if self.new_turn:
            value["new_turn"] = True
        return value


@dataclass
class ContextOptions:
    context_id: str
    voices: List[str]
    voice_settings: Optional[VoiceSettings] = None
    inputs: List[Input] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.context_id, str) or not self.context_id:
            raise ValidationError("context_id must not be empty")
        if not 1 <= len(self.voices) <= 10:
            raise ValidationError("voices must contain between 1 and 10 items")
        if any(not isinstance(item, str) or not item for item in self.voices):
            raise ValidationError("voice ids must not be empty")
        if len(set(self.voices)) != len(self.voices):
            raise ValidationError("voice ids must be unique")
        registered = set(self.voices)
        if any(item.voice_id not in registered for item in self.inputs):
            raise ValidationError("input voice_id must be registered")

    def to_wire(self):
        value = {
            "context_id": self.context_id,
            "voices": list(self.voices),
        }
        if self.voice_settings is not None:
            value["voice_settings"] = self.voice_settings.to_wire()
        if self.inputs:
            value["inputs"] = [item.to_wire() for item in self.inputs]
        return value


@dataclass
class ContextResult:
    context_id: str
    audio: bytes = b""


@dataclass
class CloseResult:
    close_code: int


@dataclass
class ServerEvent:
    raw: Dict[str, Any]

    @property
    def context_id(self):
        return self.raw.get("context_id")

    @property
    def alignment(self):
        return self.raw.get("alignment")
