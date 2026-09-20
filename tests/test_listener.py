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
                rec_cls=ScriptedRecognizer,
                playing_players=(), auto_pause=True,
                media_rows=None, wake_words=("computer",)):
    """Drive run_listener over `events` and return the harness artifacts.

    playing_players / auto_pause feed the auto-pause-on-wake hook: the hook
    shells out to playerctl on every wake, so it's stubbed here so tests never
    touch real media players (and never pause the developer's actual music).
    `playing_players` is what the stub reports as Playing; the returned
    `paused`/`resumed` lists record what the hook paused and resumed."""
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

    # Auto-pause hook: stub playerctl so a wake never shells out. Records what
    # the hook paused/resumed for assertions.
    paused = []
    resumed = []
    monkeypatch.setattr(commands, "get_auto_pause_media", lambda: auto_pause)
    list_playing_calls = []
    def _list_playing(env):
        list_playing_calls.append(1)
        return list(playing_players)
    monkeypatch.setattr(listener.media_control, "list_playing_players",
                        _list_playing)
    # Celery wake gate probe. In production ONE playerctl answers both the
    # gate and auto-pause, so the harness mirrors that: `media_rows` defaults
    # to rows derived from `playing_players`, and a test passes media_rows
    # explicitly only to stage celery-specific url/title/status data.
    if media_rows is None:
        media_rows = [(name, "Playing", "", "") for name in playing_players]
    snapshot_calls = []
    def _snapshot(env):
        snapshot_calls.append(1)
        return list(media_rows)
    monkeypatch.setattr(listener.media_control, "metadata_snapshot",
                        _snapshot, raising=False)
    monkeypatch.setattr(listener.media_control, "pause_players",
                        lambda names, env: paused.append(list(names)))
    monkeypatch.setattr(listener.media_control, "resume_players",
                        lambda names, env: resumed.append(list(names)))

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
        detector=WakeWordDetector(list(wake_words)),
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
        "notification_sources": notification_sources,
        "paused": paused,
        "resumed": resumed,
        "snapshot_calls": snapshot_calls,
        "list_playing_calls": list_playing_calls,
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
    # _normalize strips the AGC-hallucinated leading "the " (see the
    # "Normalization split" invariant) -- must survive the seam refactor.
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


# -- Auto-pause media on wake (integration through the real state machine) ----

def test_auto_pause_pauses_on_wake_and_resumes_on_sleep(monkeypatch):
    """Playing media is paused entering the wake window and resumed by name
    when the command finishes and the app returns to SLEEPING."""
    r = run_machine(
        [
            RecognizerEvent("partial", "computer"),    # wake -> pause
            RecognizerEvent("final", "open firefox"),  # match -> sleep -> resume
        ],
        monkeypatch,
        playing_players=["spotify"],
    )
    assert r["paused"] == [["spotify"]]
    assert r["resumed"] == [["spotify"]]
    assert r["context"].auto_paused_players == []
    assert r["context"].state == State.SLEEPING


def test_auto_pause_disabled_touches_nothing(monkeypatch):
    r = run_machine(
        [
            RecognizerEvent("partial", "computer"),
            RecognizerEvent("final", "open firefox"),
        ],
        monkeypatch,
        playing_players=["spotify"],
        auto_pause=False,
    )
    assert r["paused"] == []
    assert r["resumed"] == []


def test_auto_pause_nothing_playing_no_resume(monkeypatch):
    """Wake with nothing playing pauses nothing, so there's nothing to resume."""
    r = run_machine(
        [
            RecognizerEvent("partial", "computer"),
            RecognizerEvent("final", "open firefox"),
        ],
        monkeypatch,
        playing_players=[],
    )
    assert r["paused"] == []
    assert r["resumed"] == []


def test_entering_open_mic_ends_the_pause_window(monkeypatch):
    """A real wake pauses media; promoting that window to OPEN_MIC ends the
    transient window, so media resumes -- open mic itself never holds a pause."""
    r = run_machine(
        [
            RecognizerEvent("partial", "computer"),  # wake -> pause
            RecognizerEvent("final", "open mic"),    # LISTENING -> OPEN_MIC -> resume
        ],
        monkeypatch,
        playing_players=["spotify"],
    )
    assert r["paused"] == [["spotify"]]
    assert r["resumed"] == [["spotify"]]
    assert r["context"].state == State.OPEN_MIC


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


def test_streaming_mode_still_sleeps_after_match(monkeypatch):
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


def test_transcription_failure_warns_loudly_once(monkeypatch):
    # An "error" event (whisper-server down) must toast LOUDLY via _notify
    # (un-silenceable), and only ONCE across repeated failures -- not once
    # per dropped segment.
    r = run_machine(
        [RecognizerEvent("error", "connection refused"),
         RecognizerEvent("error", "connection refused"),
         RecognizerEvent("error", "connection refused")],
        monkeypatch,
    )
    warns = [(n, src) for n, src in
             zip(r["notifications"], r["notification_sources"])
             if "Transcription unavailable" in n[0]]
    assert len(warns) == 1                    # once, not per segment
    assert warns[0][1] == "_notify"           # un-silenceable, not _notify_general


def test_transcription_recovery_rearms_the_warning(monkeypatch):
    # After recovery (a real final), a NEW outage warns again -- the flag
    # must re-arm, not stay latched.
    r = run_machine(
        [RecognizerEvent("error", "down"),           # outage 1 -> warn
         RecognizerEvent("final", "open firefox"),   # recovers
         RecognizerEvent("error", "down again")],    # outage 2 -> warn again
        monkeypatch,
        try_match=lambda t: True,
    )
    warns = [n for n in r["notifications"] if "Transcription unavailable" in n[0]]
    assert len(warns) == 2


def test_text_wake_miss_still_nags(monkeypatch):
    # A TEXT wake (no audio engine) is self-confirmed; a miss must still
    # toast "No match" -- the confirm feature must not silence text wakes.
    r = run_machine(
        [RecognizerEvent("final", "computer"), RecognizerEvent("final", "nonsense")],
        monkeypatch,
        try_match=lambda t: False,
    )
    assert any("No match" in n[0] for n in r["notifications"])


# -- Celery Man wake gate ------------------------------------------------------
#
# The clip's audio says "computer" beyond its launch phrase (Paul's later
# "Computer, do we have any new sequences..."), which trips a spurious wake:
# window opens, auto-pause interrupts the video. The gate suppresses a wake
# whose ONLY trigger is a baked-in wake word while the celery video is
# actually Playing -- reality-checked via the same metadata snapshot
# auto-pause consumes, never a timer. Custom wake words are never gated.

_CELERY_ROW = ("firefox", "Playing",
               "https://www.youtube.com/watch?v=maAFcEU6atk", "Celery Man")


def test_final_wake_suppressed_while_celery_playing(monkeypatch):
    r = run_machine(
        [RecognizerEvent("final", "computer do we have any new sequences")],
        monkeypatch, media_rows=[_CELERY_ROW])
    assert r["context"].state == State.SLEEPING
    assert r["matched"] == []              # never reached the matcher
    assert State.LISTENING not in r["states"]  # no transition, no tray flicker
    assert all("Listening..." not in n for n in r["notifications"])


def test_partial_wake_suppressed_while_celery_playing(monkeypatch):
    r = run_machine([RecognizerEvent("partial", "computer")],
                    monkeypatch, media_rows=[_CELERY_ROW])
    assert r["context"].state == State.SLEEPING
    assert State.LISTENING not in r["states"]


def test_custom_wake_word_wakes_during_celery_without_probe(monkeypatch):
    """'hey dude' must keep working mid-video -- and the gate must not even
    pay for the playerctl probe when a custom word matched."""
    r = run_machine([RecognizerEvent("partial", "hey dude")],
                    monkeypatch, media_rows=[_CELERY_ROW],
                    wake_words=("computer", "hey dude"))
    assert r["context"].state == State.LISTENING
    assert r["snapshot_calls"] == []


def test_paused_celery_video_does_not_gate_the_wake(monkeypatch):
    """Paused video makes no sound: that 'computer' was the user."""
    row = ("firefox", "Paused",
           "https://www.youtube.com/watch?v=maAFcEU6atk", "Celery Man")
    r = run_machine([RecognizerEvent("partial", "computer")],
                    monkeypatch, media_rows=[row])
    assert r["context"].state == State.LISTENING


def test_auto_pause_reuses_gate_snapshot_net_zero_queries(monkeypatch):
    """A normal (non-celery) wake: the gate's snapshot feeds auto-pause, so
    list_playing_players is never called on the way in."""
    r = run_machine(
        [RecognizerEvent("final", "computer open reddit")],
        monkeypatch, try_match=lambda t: False,
        media_rows=[("spotify", "Playing", "", "Some Song")])
    assert r["paused"] == [["spotify"]]
    assert r["list_playing_calls"] == []
    assert r["snapshot_calls"] == [1]
