"""
Tests for the listener state machine driven through the recognizer seam.

run_listener historically called vosk.KaldiRecognizer inline, which made
the state machine untestable without a live engine. The seam refactor
makes it consume RecognizerEvents from an injected recognizer_factory --
so these tests drive the REAL state machine (real WakeWordDetector, real
Context, real _normalize) with a scripted fake recognizer and fake audio
plumbing. No audio, no Vosk, no Qt.

Each test scripts a sequence of recognizer events, runs the loop to
completion (audio queue ends with a None sentinel -> loop breaks), and
asserts on state transitions and matcher calls.
"""

import queue

import pytest

import core.commands as commands
import core.listener as listener
from core.context import Context, State
from core.recognizer import RecognizerEvent
from core.wake import WakeWordDetector


class ScriptedRecognizer:
    """Seam-conformant fake: returns the next scripted event per feed()."""

    def __init__(self, events):
        self.events = list(events)
        self.fed = []

    def feed(self, data):
        self.fed.append(data)
        return self.events.pop(0)


def run_machine(events, monkeypatch, try_match=None, state=State.SLEEPING):
    """Drive run_listener over `events` and return the harness artifacts."""
    # LISTENING can only be entered through the wake path in production --
    # that's what arms command_window_start. Injecting the state directly
    # would trip the window-expiry check on the first loop (start time 0.0),
    # so tests that want LISTENING get there the honest way: a wake partial.
    if state == State.LISTENING:
        events = [RecognizerEvent("partial", "computer")] + list(events)
        state = State.SLEEPING

    # Fake audio plumbing: one dummy chunk per event, then the None
    # sentinel _start_reader uses to signal pw-record death -> loop exits.
    audio = queue.Queue()
    for i in range(len(events)):
        audio.put(b"chunk%d" % i)
    audio.put(None)

    class FakeProc:
        def kill(self):
            pass

    monkeypatch.setattr(listener, "_open_pw_record", lambda source: FakeProc())
    monkeypatch.setattr(listener, "_start_reader", lambda proc: audio)

    # Notifications are side effects; capture instead of toasting.
    notifications = []
    monkeypatch.setattr(listener, "_notify", lambda *a, **k: notifications.append(a))
    monkeypatch.setattr(listener, "_notify_general", lambda *a, **k: notifications.append(a))

    # Config readers hit disk; pin them.
    monkeypatch.setattr(commands, "get_command_window", lambda: 8)
    monkeypatch.setattr(commands, "get_open_mic_phrases", lambda: {"open mic"})
    monkeypatch.setattr(commands, "get_close_mic_phrases", lambda: {"close mic"})
    monkeypatch.setattr(commands, "get_overrides", lambda: [])

    matched = []
    def _try_match(text, gui_env, context):
        matched.append(text)
        return try_match(text) if try_match else True
    monkeypatch.setattr(commands, "try_match", _try_match)

    context = Context()
    context.state = state
    rec = ScriptedRecognizer(events)
    factories = []
    state_queue = queue.Queue()

    def factory():
        factories.append(rec)
        return rec

    listener.run_listener(
        source="fake-source",
        recognizer_factory=factory,
        detector=WakeWordDetector(["computer"]),
        context=context,
        gui_env={},
        state_queue=state_queue,
    )

    states = []
    while not state_queue.empty():
        states.append(state_queue.get())
    return {
        "context": context,
        "states": states,
        "matched": matched,
        "rec": rec,
        "factories": factories,
        "notifications": notifications,
    }


def test_factory_is_called_once_and_all_chunks_are_fed(monkeypatch):
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
    )
    assert len(r["factories"]) == 1
    assert r["rec"].fed == [b"chunk0", b"chunk1"]


def test_wake_word_in_partial_acknowledges_immediately(monkeypatch):
    # THE latency feature: SLEEPING -> LISTENING off a partial, before
    # the utterance finalizes (see "Wake is acknowledged on partials").
    r = run_machine([RecognizerEvent("partial", "computer")], monkeypatch)
    assert State.LISTENING in r["states"]


def test_non_wake_partial_stays_asleep(monkeypatch):
    r = run_machine([RecognizerEvent("partial", "computerized nonsense")], monkeypatch)
    assert r["context"].state == State.SLEEPING
    assert State.LISTENING not in r["states"]


def test_final_command_in_listening_dispatches_and_sleeps(monkeypatch):
    r = run_machine(
        [RecognizerEvent("final", "open firefox")],
        monkeypatch,
        state=State.LISTENING,
    )
    assert r["matched"] == ["open firefox"]
    assert r["context"].state == State.SLEEPING


def test_final_text_is_normalized_before_matching(monkeypatch):
    # _normalize strips the AGC-hallucinated leading "the " (see "Vosk
    # normalization split" invariant) -- must survive the seam refactor.
    r = run_machine(
        [RecognizerEvent("final", "the open firefox")],
        monkeypatch,
        state=State.LISTENING,
    )
    assert r["matched"] == ["open firefox"]


def test_bare_stopword_final_is_dropped(monkeypatch):
    r = run_machine(
        [RecognizerEvent("final", "uh")],
        monkeypatch,
        state=State.LISTENING,
    )
    assert r["matched"] == []
    assert r["context"].state == State.LISTENING


def test_wake_only_final_is_swallowed_and_keeps_listening(monkeypatch):
    r = run_machine(
        [RecognizerEvent("final", "computer")],
        monkeypatch,
        state=State.LISTENING,
    )
    assert r["matched"] == []
    assert r["context"].state == State.LISTENING


def test_total_miss_is_one_shot_back_to_sleep(monkeypatch):
    r = run_machine(
        [RecognizerEvent("final", "gibberish nothing")],
        monkeypatch,
        try_match=lambda text: False,
        state=State.LISTENING,
    )
    assert r["matched"] == ["gibberish nothing"]  # it DID reach the matcher
    assert r["context"].state == State.SLEEPING


def test_full_wake_then_command_flow(monkeypatch):
    r = run_machine(
        [
            RecognizerEvent("partial", "compu"),
            RecognizerEvent("partial", "computer"),
            RecognizerEvent("final", "computer"),        # wake-only: swallowed
            RecognizerEvent("partial", "open fire"),
            RecognizerEvent("final", "open firefox"),
        ],
        monkeypatch,
    )
    assert r["matched"] == ["open firefox"]
    assert r["states"][0] == State.LISTENING  # woke off the partial
    assert r["context"].state == State.SLEEPING


def test_speech_event_refreshes_command_window(monkeypatch):
    # Whisper-backed recognizers have no partials; they emit "speech"
    # events while the user talks. Those must refresh the inactivity
    # window exactly like text partials do, or a long utterance falls
    # asleep mid-sentence. Proven with a fake clock: window is 8s, the
    # final lands 10s after wake -- only survivable if the speech event
    # at +5s refreshed the window.
    clock = {"t": 1000.0}
    monkeypatch.setattr(listener.time, "time", lambda: clock["t"])

    events = [
        RecognizerEvent("partial", "computer"),  # t=1000: wake, window starts
        RecognizerEvent("speech", ""),           # t=1005: must refresh window
        RecognizerEvent("final", "open firefox"),  # t=1010: 10s > 8s window
    ]
    orig_feed = ScriptedRecognizer.feed

    def feed_and_advance(self, data):
        ev = orig_feed(self, data)
        clock["t"] += 5.0
        return ev

    monkeypatch.setattr(ScriptedRecognizer, "feed", feed_and_advance)
    r = run_machine(events, monkeypatch)
    assert r["matched"] == ["open firefox"]


def test_speech_event_while_sleeping_is_ignored(monkeypatch):
    # No text, so nothing to wake on; must not crash or change state.
    r = run_machine([RecognizerEvent("speech", "")], monkeypatch)
    assert r["context"].state == State.SLEEPING
    assert r["matched"] == []


def test_none_event_is_skipped(monkeypatch):
    # Batch backends buffering mid-segment return None; the loop just
    # moves on to the next chunk.
    r = run_machine([None, RecognizerEvent("partial", "computer")], monkeypatch)
    assert State.LISTENING in r["states"]


def test_listener_module_does_not_import_vosk():
    # The whole point of the seam: engine imports live behind it, so a
    # Whisper-only install never needs the vosk package.
    import core.listener as mod
    assert not hasattr(mod, "vosk")
