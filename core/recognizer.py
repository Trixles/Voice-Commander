"""
core/recognizer.py
==================
The recognizer seam: decouples the listener state machine from any
concrete speech engine.

Contract every backend must honor:
  - feed(bytes) accepts 16kHz s16 mono PCM and returns a RecognizerEvent
    (or None when the backend has nothing to report for this chunk).
  - "partial" events stream mid-utterance text as words form; "final"
    events carry the end-of-utterance transcription.
  - Event text is always stripped and lowercase. The normalization lives
    HERE, not in the listener, so backends with different output styles
    (Vosk: bare lowercase; Whisper: punctuated prose) present identical
    text to the wake check / overrides / matcher downstream.

The listener never imports a speech engine; it consumes events. Engine
imports stay inside the concrete classes so an install that only uses
one backend never needs the other's package.
"""

import json
from dataclasses import dataclass

# One home for the capture format. pw-record's invocation and every
# recognizer must agree on this; it used to be hardcoded in two places
# in core/listener.py.
SAMPLE_RATE = 16000


@dataclass(frozen=True)
class RecognizerEvent:
    kind: str  # "partial" | "final"
    text: str


class VoskRecognizer:
    """Streaming wrapper around vosk.KaldiRecognizer.

    Vosk answers every chunk: AcceptWaveform(data) False means
    mid-utterance (partial text available), True means the engine
    detected end-of-utterance (final text available). So feed() always
    returns an event, never None.
    """

    def __init__(self, model):
        import vosk  # deferred: only a Vosk-backend install needs the package

        self._rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)

    def feed(self, data: bytes) -> RecognizerEvent:
        if self._rec.AcceptWaveform(data):
            text = json.loads(self._rec.Result()).get("text", "")
            kind = "final"
        else:
            text = json.loads(self._rec.PartialResult()).get("partial", "")
            kind = "partial"
        return RecognizerEvent(kind=kind, text=text.strip().lower())
