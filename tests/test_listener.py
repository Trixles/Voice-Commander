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


def run_machine(events, monkeypatch, try_match=None, state=State.SLEEPING,
                rec_cls=ScriptedRecognizer, wake_engine=None):
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
    # `notification_sources` is a parallel list (same index as
    # `notifications`) recording which of the two functions produced each
    # entry. That distinction is load-bearing: _notify always fires,
    # _notify_general is silenceable via the Options-tab toggle. Kept
    # separate from `notifications` so the many existing tests that index
    # `n[0]`/`n[1]` on a notification tuple see no change in shape.
    notifications = []
    notification_sources = []

    def _record_notify(*a, **k):
        notifications.append(a)
        notification_sources.append("_notify")

    def _record_notify_general(*a, **k):
        notifications.append(a)
        notification_sources.append("_notify_general")

    monkeypatch.setattr(listener, "_notify", _record_notify)
    monkeypatch.setattr(listener, "_notify_general", _record_notify_general)

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
    rec = rec_cls(events)
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
        wake_engine_factory=(lambda: wake_engine) if wake_engine else None,
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
        "notification_sources": notification_sources,
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


# -- Per-segment chains (whisper-mode: matches keep the window open) ----------

class PerSegmentRecognizer(ScriptedRecognizer):
    per_segment_finals = True


def run_per_segment(events, monkeypatch, **kwargs):
    return run_machine(events, monkeypatch, rec_cls=PerSegmentRecognizer, **kwargs)


def test_per_segment_match_stays_listening_for_the_next_segment(monkeypatch):
    # THE paused-chain fix (s30 live test): under a batch backend each
    # chain segment arrives as its OWN final. Sleeping after the first
    # match discards the rest of the chain -- a match must keep LISTENING.
    r = run_per_segment(
        [
            RecognizerEvent("final", "computer"),        # wake
            RecognizerEvent("final", "open dolphin on monitor one"),
            RecognizerEvent("final", "and open dolphin on monitor two"),
        ],
        monkeypatch,
        try_match=lambda t: t != "computer",
    )
    # The wake utterance itself passes through try_match (production
    # behavior -- it returns False for a bare wake word), so it appears
    # in the recorder before the two real segments.
    assert r["matched"] == [
        "computer",
        "open dolphin on monitor one",
        "open dolphin on monitor two",   # leading "and " stripped
    ]
    assert r["context"].state == State.LISTENING  # window still open at end


def test_vosk_mode_still_sleeps_after_match(monkeypatch):
    # Default recognizers (no per_segment_finals) keep the classic
    # behavior: chain arrives in one final, match -> back to sleep.
    r = run_machine(
        [RecognizerEvent("final", "open firefox")],
        monkeypatch,
        state=State.LISTENING,
    )
    assert r["context"].state == State.SLEEPING


def test_wake_and_command_in_one_final_stays_listening_per_segment(monkeypatch):
    # "computer open kate..." as a single utterance, then a pause, then
    # more chain: the wake-utterance match must also keep LISTENING.
    r = run_per_segment(
        [
            RecognizerEvent("final", "computer open kate on monitor three"),
            RecognizerEvent("final", "and lower volume"),
        ],
        monkeypatch,
    )
    assert len(r["matched"]) == 2
    assert r["context"].state == State.LISTENING


def test_window_expiry_after_successful_match_is_silent(monkeypatch):
    # A chain that matched something must NOT toast "No match" when the
    # window finally expires -- that toast is for failed wakes only.
    clock = {"t": 1000.0}
    monkeypatch.setattr(listener.time, "time", lambda: clock["t"])

    events = [
        RecognizerEvent("final", "computer"),
        RecognizerEvent("final", "open firefox"),   # matches, stays LISTENING
        RecognizerEvent("partial", ""),             # 20s later: window expired
    ]
    kwargs = {"try_match": lambda t: t != "computer"}
    orig_feed = ScriptedRecognizer.feed
    advances = [2.0, 20.0, 0.0]  # command lands IN window; then 20s of silence

    def feed_and_advance(self, data):
        ev = orig_feed(self, data)
        clock["t"] += advances.pop(0)
        return ev

    monkeypatch.setattr(PerSegmentRecognizer, "feed", feed_and_advance)
    r = run_per_segment(events, monkeypatch, **kwargs)
    assert r["matched"] == ["computer", "open firefox"]  # the chain DID fire
    assert r["context"].state == State.SLEEPING
    assert not any("No match" in str(n) for n in r["notifications"])


def test_window_expiry_with_no_match_still_toasts(monkeypatch):
    # The one-shot "No match" behavior survives for a wake that never
    # produced a command (existing invariant, now conditional).
    clock = {"t": 1000.0}
    monkeypatch.setattr(listener.time, "time", lambda: clock["t"])

    events = [
        RecognizerEvent("final", "computer"),
        RecognizerEvent("partial", ""),   # 20s later, nothing matched
    ]
    kwargs = {"try_match": lambda t: False}
    orig_feed = ScriptedRecognizer.feed

    def feed_and_advance(self, data):
        ev = orig_feed(self, data)
        clock["t"] += 20.0
        return ev

    monkeypatch.setattr(PerSegmentRecognizer, "feed", feed_and_advance)
    r = run_per_segment(events, monkeypatch, **kwargs)
    assert r["context"].state == State.SLEEPING
    assert any("No match" in str(n) for n in r["notifications"])


# -- Audio wake engine (openWakeWord) -----------------------------------------

class FakeWakeEngine:
    """Scripted audio wake: fires on the feed() calls marked True."""

    def __init__(self, fires):
        self.fires = list(fires)
        self.fed = []

    def feed(self, data):
        self.fed.append(data)
        return self.fires.pop(0)


def test_audio_wake_fires_into_listening_without_text(monkeypatch):
    # The whole point: SLEEPING -> LISTENING off the audio engine
    # (~100-200ms), no transcription involved in the ack.
    engine = FakeWakeEngine(fires=[True])
    r = run_machine(
        [RecognizerEvent("speech", "")],   # recognizer still buffering
        monkeypatch,
        wake_engine=engine,
    )
    assert State.LISTENING in r["states"]


def test_audio_wake_engine_not_fed_outside_sleeping(monkeypatch):
    engine = FakeWakeEngine(fires=[True, False, False])
    r = run_machine(
        [
            RecognizerEvent("speech", ""),   # chunk 1: fires -> LISTENING
            RecognizerEvent("speech", ""),   # chunk 2: LISTENING, not fed
            RecognizerEvent("speech", ""),   # chunk 3: LISTENING, not fed
        ],
        monkeypatch,
        wake_engine=engine,
    )
    assert len(engine.fed) == 1  # only the SLEEPING chunk reached the engine


def test_wake_final_after_audio_wake_is_swallowed(monkeypatch):
    # The utterance that fired the audio wake still produces a whisper
    # final ("computer") a beat later -- the existing wake-only swallow
    # must absorb it and keep LISTENING for the real command.
    engine = FakeWakeEngine(fires=[True, False, False])
    r = run_machine(
        [
            RecognizerEvent("speech", ""),
            RecognizerEvent("final", "computer"),
            RecognizerEvent("final", "open firefox"),
        ],
        monkeypatch,
        wake_engine=engine,
        try_match=lambda t: t != "computer",
    )
    assert r["matched"] == ["open firefox"]
    assert State.LISTENING in r["states"]


def test_wake_engine_factory_failure_degrades_to_text_wake(monkeypatch, capsys):
    # A broken wake engine (missing dep, missing model file) must NOT
    # kill the listener thread -- it degrades to classic text wake,
    # loudly. Found live in s31: openwakeword's sklearn import crashed
    # factory() inside the thread and left the app deaf with a healthy
    # tray icon.
    def exploding_factory():
        raise ModuleNotFoundError("No module named 'sklearn'")

    monkeypatch.setattr(listener, "notify", lambda *a, **k: None, raising=False)
    events = [RecognizerEvent("partial", "computer")]
    audio = queue.Queue()
    audio.put(b"chunk0")
    audio.put(None)
    monkeypatch.setattr(listener, "_open_pw_record", lambda s: type("P", (), {"kill": lambda self: None})())
    monkeypatch.setattr(listener, "_start_reader", lambda p: audio)
    monkeypatch.setattr(listener, "_notify_general", lambda *a, **k: None)
    monkeypatch.setattr(commands, "get_command_window", lambda: 8)
    monkeypatch.setattr(commands, "get_overrides", lambda: [])
    monkeypatch.setattr(commands, "try_match", lambda *a: False)

    context = Context()
    rec = ScriptedRecognizer(events)
    listener.run_listener(
        source="fake",
        recognizer_factory=lambda: rec,
        detector=WakeWordDetector(["computer"]),
        context=context,
        gui_env={},
        wake_engine_factory=exploding_factory,
    )
    # Text wake still worked: the partial woke the machine.
    assert context.state == State.LISTENING
    assert "wake engine failed" in capsys.readouterr().out.lower()


# NOTE: named distinctly from the FakeWakeEngine above (fires-list based) --
# same seam contract (feed(chunk) -> fired?), but this one also carries the
# verifier attributes (Task 3's engine.verified / engine.verifier_error).
# A same-named class here would silently clobber the module-level
# `FakeWakeEngine` name and break the fires-list tests above (Python
# resolves globals at call time, not def time).
class FakeVerifiableWakeEngine:
    """Seam-conformant audio wake engine: feed(chunk) -> fired?."""

    def __init__(self, fire_on=None, verified=False, verifier_error=None):
        self.calls = 0
        self.fire_on = fire_on
        self.last_score = 0.82
        self.verified = verified
        self.verifier_error = verifier_error

    def feed(self, data):
        fired = self.calls == self.fire_on
        self.calls += 1
        return fired


def test_verifier_load_failure_toasts_loudly(monkeypatch):
    # Unprotected wake must be impossible to miss -- and impossible to
    # silence via the notifications toggle (_notify, not _notify_general).
    eng = FakeVerifiableWakeEngine(verifier_error="computer_v2_verifier.pkl: boom")
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    matches = [
        src for n, src in zip(r["notifications"], r["notification_sources"])
        if "Wake verifier failed" in n[0]
    ]
    assert matches, "expected a 'Wake verifier failed' notification"
    # The whole point of this task: this toast must come from _notify, not
    # _notify_general, so it can't be silenced by the notifications toggle.
    # A swap back to _notify_general at the call site must fail here.
    assert matches == ["_notify"] * len(matches)


def test_healthy_verifier_does_not_toast(monkeypatch):
    eng = FakeVerifiableWakeEngine(verified=True)
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    assert not any("verifier" in n[0].lower() for n in r["notifications"])


def test_verified_audio_wake_still_reaches_listening(monkeypatch):
    # The verifier changes what last_score MEANS, not the wake path.
    eng = FakeVerifiableWakeEngine(fire_on=0, verified=True)
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    assert State.LISTENING in r["states"]


class ExplodingWakeEngine:
    """Constructs fine, then dies on the first frame -- the shape of a
    verifier .pkl that unpickles but raises inside predict_proba
    (sklearn version skew after a venv rebuild)."""

    def __init__(self):
        self.calls = 0
        self.verified = True
        self.verifier_error = None
        self.last_score = 0.0

    def feed(self, data):
        self.calls += 1
        raise RuntimeError("X has 96 features, but StandardScaler expects 48")


def test_wake_engine_death_mid_run_degrades_to_text_wake(monkeypatch):
    # An exception out of feed() used to escape run_listener entirely and
    # kill the listener THREAD, leaving the app deaf behind a healthy
    # tray icon (the s31 failure). It must degrade to text wake instead.
    eng = ExplodingWakeEngine()
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", ""),
         RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    matches = [
        src for n, src in zip(r["notifications"], r["notification_sources"])
        if "Wake engine failed" in n[0]
    ]
    assert matches, "expected a 'Wake engine failed' notification"
    # Always-on toast: a deaf wake path must not be silenceable by the
    # Options-tab notifications toggle.
    assert matches == ["_notify"]
    # Dropped after the first raise -- not retried (and re-toasted) on
    # every subsequent frame.
    assert eng.calls == 1


def test_dead_wake_engine_still_wakes_on_text(monkeypatch):
    # The degrade is only worth anything if text wake actually takes
    # over afterwards.
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "computer")],
        monkeypatch,
        wake_engine=ExplodingWakeEngine(),
    )
    assert State.LISTENING in r["states"]
