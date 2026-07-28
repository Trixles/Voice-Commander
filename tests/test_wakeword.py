"""
Tests for the audio wake engine (openWakeWord behind a seam).

The engine consumes the listener's 4000-byte (125ms) chunks and answers
"did the wake word just fire?". openWakeWord natively wants 1280-sample
(80ms) frames, so the wrapper re-chunks internally. The oww model object
is injected for tests (production builds it lazily from config).

Config: wake_engine ("text" = classic transcription-based detection /
"openwakeword"), wake_model (file stem in DATA_DIR/wakewords/),
wake_threshold. Factory returns None for "text" -- the listener treats
a None engine as "no audio wake" and behaves exactly as before.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
import core.paths as paths  # noqa: E402
import core.wakeword as wakeword  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_globals():
    orig_config, orig_commands = commands._config, commands._commands
    yield
    commands._config, commands._commands = orig_config, orig_commands


# -- Config keys ---------------------------------------------------------------

def test_wake_engine_defaults_to_text():
    commands._config = {}
    assert commands.get_wake_engine() == "text"


def test_wake_engine_unknown_falls_back_to_text():
    commands._config = {"wake_engine": "porcupine"}  # RIP
    assert commands.get_wake_engine() == "text"


def test_wake_model_path_and_threshold():
    commands._config = {"wake_model": "computer_v2", "wake_threshold": 0.7}
    path = commands.get_wake_model_path()
    assert path == os.path.join(paths.DATA_DIR, "wakewords", "computer_v2.onnx")
    assert commands.get_wake_threshold() == 0.7
    commands._config = {}
    assert commands.get_wake_threshold() == 0.5


def test_wake_verifier_defaults_to_off():
    # Shipped default is no verifier: a fresh install behaves exactly as
    # it did before this feature existed.
    commands._config = {}
    assert commands.get_wake_verifier_path() is None


def test_wake_verifier_blank_string_is_off():
    commands._config = {"wake_verifier": "   "}
    assert commands.get_wake_verifier_path() is None


def test_wake_verifier_path_resolves_stem_to_pkl():
    commands._config = {"wake_verifier": "computer_v2_verifier"}
    assert commands.get_wake_verifier_path() == os.path.join(
        paths.DATA_DIR, "wakewords", "computer_v2_verifier.pkl"
    )


class FakeOwwModel:
    """Mimics openwakeword.Model: predict(np_chunk) -> {name: score}."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.chunk_sizes = []
        self.resets = 0

    def predict(self, chunk):
        self.chunk_sizes.append(len(chunk))
        return {"computer_v2": self.scores.pop(0)}

    def reset(self):
        self.resets += 1


# -- Engine wrapper ------------------------------------------------------------

def test_rechunks_to_oww_frame_size():
    # Two 2000-sample listener chunks = 4000 samples = 3 full oww frames
    # (1280 each) + 160 samples carried in the buffer for next time.
    fake = FakeOwwModel(scores=[0.0, 0.0, 0.0])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.5)
    assert eng.feed(b"\x00" * 4000) is False
    assert eng.feed(b"\x00" * 4000) is False
    assert fake.chunk_sizes == [1280, 1280, 1280]


def test_fires_when_any_frame_crosses_threshold():
    fake = FakeOwwModel(scores=[0.1, 0.96, 0.2])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.5)
    assert eng.feed(b"\x00" * 4000) is False   # frame 1: 0.1
    assert eng.feed(b"\x00" * 4000) is True    # frames 2-3: 0.96 fires


def test_fire_resets_model_state():
    # Without a reset, the model's rolling audio buffer still scores hot
    # right after a fire -- re-entering SLEEPING would instantly re-fire.
    fake = FakeOwwModel(scores=[0.99])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.5)
    eng.feed(b"\x00" * 2560)
    assert fake.resets == 1


def test_respects_threshold():
    fake = FakeOwwModel(scores=[0.6, 0.6])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.9)
    assert eng.feed(b"\x00" * 2560) is False


# -- Factory -------------------------------------------------------------------

def test_factory_returns_none_for_text_engine():
    commands._config = {"wake_engine": "text"}
    assert wakeword.make_wake_engine_factory() is None


def test_factory_builds_engine_from_config(monkeypatch):
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
        "wake_threshold": 0.8,
    }
    built = {}

    def fake_load(model_path, vad_threshold=0.0):
        built["path"] = model_path
        built["vad"] = vad_threshold
        return FakeOwwModel(scores=[0.0])

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    factory = wakeword.make_wake_engine_factory()
    eng = factory()
    assert built["path"].endswith("wakewords/computer_v2.onnx")
    assert eng._threshold == 0.8
    assert built["vad"] == 0.5  # speech gate on by default
    assert eng.feed(b"\x00" * 2560) is False  # wired and callable


def test_wake_vad_threshold_config():
    # The speech gate that killed the s33 non-speech false fires
    # (keyboard clicks, sighs). 0 disables it.
    commands._config = {}
    assert commands.get_wake_vad_threshold() == 0.5
    commands._config = {"wake_vad_threshold": 0}
    assert commands.get_wake_vad_threshold() == 0


def test_fire_records_score_for_logging():
    # Without the score, a false fire and a genuine wake are
    # indistinguishable in the logs -- s33 debugged blind for a day.
    # 2560 bytes = 1280 int16 samples = exactly one oww frame.
    fake = FakeOwwModel(scores=[0.1, 0.83])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.5)
    assert eng.feed(b"\x00" * 5120) is True     # two frames: 0.1 then 0.83
    assert eng.last_score == pytest.approx(0.83)


def test_score_clears_when_no_fire():
    fake = FakeOwwModel(scores=[0.9, 0.1, 0.1])
    eng = wakeword.OpenWakeWordEngine(model=fake, threshold=0.5)
    eng.feed(b"\x00" * 2560)                    # one frame: 0.9 fires
    assert eng.last_score == pytest.approx(0.9)
    eng.feed(b"\x00" * 5120)                    # two quiet frames
    assert eng.last_score == 0.0                # stale score not reused
