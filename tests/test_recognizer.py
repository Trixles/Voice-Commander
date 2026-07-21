"""
Tests for core/recognizer.py -- the recognizer seam.

The seam decouples the listener state machine from any concrete speech
engine. Contract: a recognizer's feed(bytes) returns a RecognizerEvent
("partial" or "final") whose text is stripped and lowercase, so every
backend (Vosk today, Whisper next) presents identical text downstream.

VoskRecognizer is tested against a fake KaldiRecognizer that mimics the
real API shape (AcceptWaveform/PartialResult/Result returning Vosk's
JSON strings). A real vosk.Model needs the ~40MB model dir and seconds
of load time -- wrong tradeoff for a unit suite; the wrapper is thin
JSON plumbing and the fake pins exactly that plumbing.
"""

import json

import pytest

from core.recognizer import RecognizerEvent, SAMPLE_RATE, VoskRecognizer


class FakeKaldiRecognizer:
    """Mimics the vosk.KaldiRecognizer API surface the wrapper uses.

    Scripted: give it a list of (accept, payload) pairs; each feed() pops
    one. accept=False -> payload is the partial text, accept=True ->
    payload is the final text, delivered via the same JSON envelopes the
    real engine produces.
    """

    def __init__(self, model, sample_rate):
        self.model = model
        self.sample_rate = sample_rate
        self.script = []
        self.fed = []

    def AcceptWaveform(self, data):
        self.fed.append(data)
        accept, _ = self.script[len(self.fed) - 1]
        return accept

    def PartialResult(self):
        _, payload = self.script[len(self.fed) - 1]
        return json.dumps({"partial": payload})

    def Result(self):
        _, payload = self.script[len(self.fed) - 1]
        return json.dumps({"text": payload})


@pytest.fixture
def fake_vosk(monkeypatch):
    """Patch vosk.KaldiRecognizer, return the created fake for scripting."""
    import vosk

    created = {}

    def _ctor(model, sample_rate):
        created["rec"] = FakeKaldiRecognizer(model, sample_rate)
        return created["rec"]

    monkeypatch.setattr(vosk, "KaldiRecognizer", _ctor)
    return created


def test_sample_rate_is_hoisted_to_one_place():
    # listener.py:227 and the pw-record invocation both hardcoded 16000;
    # the seam owns the number now.
    assert SAMPLE_RATE == 16000


def test_vosk_recognizer_constructs_kaldi_with_model_and_sample_rate(fake_vosk):
    model = object()
    VoskRecognizer(model)
    assert fake_vosk["rec"].model is model
    assert fake_vosk["rec"].sample_rate == SAMPLE_RATE


def test_feed_returns_partial_event_mid_utterance(fake_vosk):
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(False, "open fire")]
    event = rec.feed(b"\x00\x01")
    assert event == RecognizerEvent(kind="partial", text="open fire")
    assert fake_vosk["rec"].fed == [b"\x00\x01"]


def test_feed_returns_final_event_at_end_of_utterance(fake_vosk):
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(True, "open firefox")]
    event = rec.feed(b"\x00")
    assert event == RecognizerEvent(kind="final", text="open firefox")


def test_final_text_is_stripped_and_lowercased(fake_vosk):
    # The seam contract: downstream (wake check, matcher, overrides) always
    # sees stripped lowercase text regardless of backend. Vosk is already
    # lowercase; Whisper won't be -- the contract lives here so the listener
    # never has to care.
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(True, "  Open Firefox \n")]
    assert rec.feed(b"") == RecognizerEvent(kind="final", text="open firefox")


def test_partial_text_is_stripped_and_lowercased(fake_vosk):
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(False, " Compu ")]
    assert rec.feed(b"") == RecognizerEvent(kind="partial", text="compu")


def test_empty_partial_still_yields_partial_event(fake_vosk):
    # The listener treats "no event" and "partial with empty text"
    # differently: empty partials must NOT refresh the command window,
    # and the listener guards on `if event.text`. The wrapper reports
    # honestly rather than swallowing.
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(False, "")]
    assert rec.feed(b"") == RecognizerEvent(kind="partial", text="")


def test_missing_json_keys_yield_empty_text(fake_vosk):
    # Vosk omits keys in some edge results; the wrapper must not KeyError.
    rec = VoskRecognizer(object())
    fake_vosk["rec"].script = [(True, None)]
    fake_vosk["rec"].Result = lambda: json.dumps({})
    assert rec.feed(b"") == RecognizerEvent(kind="final", text="")
