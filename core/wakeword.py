"""
core/wakeword.py
================
Audio-level wake-word detection (openWakeWord) behind a small seam.

The engine answers one question per 125ms listener chunk: "did the wake
word just fire?". It runs ONLY while the listener is SLEEPING -- once
awake, transcription takes over and the engine is not fed (the listener
resets nothing; firing resets the model's own rolling state so a
re-entered SLEEPING can't instantly re-fire on stale audio).

Wake models are drop-in .onnx files in DATA_DIR/wakewords/ -- swapping
"computer_v2" for a better-trained model is a config change, not code.

openwakeword imports are deferred (same rule as vosk/onnxruntime in
their engines): only an install running wake_engine=openwakeword needs
the package.
"""

import core.commands as commands

# openWakeWord's native frame: 1280 samples (80ms) at 16kHz.
_OWW_FRAME_SAMPLES = 1280


def _load_oww_model(model_path: str, vad_threshold: float = 0.0):
    """Build the openwakeword Model (separated for test injection).

    vad_threshold > 0 turns on oww's bundled Silero speech gate: a
    frame's score is forced to 0 unless the VAD saw speech just before
    it. Its onnxruntime session is explicitly 1-thread upstream, so it
    can't reintroduce the s33 spin-pool CPU burn."""
    from openwakeword.model import Model  # deferred import

    kwargs = {"wakeword_models": [model_path], "inference_framework": "onnx"}
    if vad_threshold > 0:
        kwargs["vad_threshold"] = vad_threshold
    return Model(**kwargs)


class OpenWakeWordEngine:
    """Re-chunks listener audio to oww frames and applies the threshold."""

    def __init__(self, model, threshold: float):
        import numpy as np

        self._np = np
        self._model = model
        self._threshold = threshold
        self._buffer = np.array([], dtype=np.int16)
        # Score of the frame that fired, for the log line. Debugging the
        # s33 false fires was blind without it: "wake detected" alone
        # can't distinguish a confident hit from a threshold squeaker.
        self.last_score = 0.0

    def feed(self, data: bytes) -> bool:
        """Returns True when the wake word fired in this chunk's frames."""
        np = self._np
        self._buffer = np.concatenate(
            [self._buffer, np.frombuffer(data, dtype=np.int16)]
        )
        fired = False
        while len(self._buffer) >= _OWW_FRAME_SAMPLES:
            frame, self._buffer = (
                self._buffer[:_OWW_FRAME_SAMPLES],
                self._buffer[_OWW_FRAME_SAMPLES:],
            )
            scores = self._model.predict(frame)
            top = max(scores.values())
            if top >= self._threshold:
                fired = True
                self.last_score = max(self.last_score, float(top))
        if fired:
            # Drop the rolling audio state so the hot window can't re-fire
            # the instant the listener returns to SLEEPING.
            self._model.reset()
            self._buffer = np.array([], dtype=np.int16)
        else:
            self.last_score = 0.0
        return fired


def make_wake_engine_factory():
    """None for wake_engine=text (listener keeps classic behavior);
    otherwise a zero-arg factory building a fresh engine per listener
    run (mirrors the recognizer factory lifecycle)."""
    if commands.get_wake_engine() != "openwakeword":
        return None

    model_path = commands.get_wake_model_path()
    threshold = commands.get_wake_threshold()
    vad_threshold = commands.get_wake_vad_threshold()

    def factory():
        return OpenWakeWordEngine(
            model=_load_oww_model(model_path, vad_threshold),
            threshold=threshold,
        )

    return factory
