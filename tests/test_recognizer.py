"""
Tests for core/recognizer.py -- the recognizer seam.

The seam decouples the listener state machine from any concrete speech
engine. Contract: a recognizer's feed(bytes) returns a RecognizerEvent
("partial" or "final") whose text is stripped and lowercase, so any
backend presents identical text downstream. WhisperRecognizer's behavior
is pinned in tests/test_whisper_recognizer.py; this file keeps the
seam-wide constants honest.
"""

from core.recognizer import SAMPLE_RATE


def test_sample_rate_is_hoisted_to_one_place():
    # listener.py and the pw-record invocation both hardcoded 16000 once;
    # the seam owns the number now.
    assert SAMPLE_RATE == 16000
