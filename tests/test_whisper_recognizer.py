"""
Tests for WhisperRecognizer -- the batch-engine side of the seam.

Whisper has no streaming partials: whole audio in -> text out. The
recognizer therefore does VAD-driven segmentation itself: buffer while
the user speaks, and when a silence tail (default 400ms) closes the
segment, transcribe it and emit ONE "final" per segment. While speech is
active it emits "speech" events (voice heard, no text yet) so the
listener's inactivity window can refresh without partials.

Both collaborators are injected:
  vad(chunk: bytes) -> bool        is there speech in this chunk?
  transcribe(pcm: bytes) -> str    raw engine text for a whole segment
so these tests drive the real segmentation state machine and the real
Whisper-output cleanup with no model, server, or audio hardware.
Real Silero VAD / whisper-server integration is exercised separately
(benchmark/ spike scripts), not here.

Chunks: the listener feeds 4000-byte chunks = 125ms at 16kHz s16 mono.
Default 400ms tail = silence spanning >= 4 chunk-lengths after speech.
"""

import pytest

from core.recognizer import RecognizerEvent, WhisperRecognizer

CHUNK = b"\x00" * 4000  # one 125ms listener chunk (content irrelevant: VAD is injected)


def make(script, transcripts=None, **kwargs):
    """Build a WhisperRecognizer with a scripted VAD and canned transcripts.

    script: list of bools, one per expected feed() call (True = speech).
    transcripts: list of raw texts, popped once per transcribe() call.
    """
    vad_script = list(script)
    calls = {"transcribed": []}

    def vad(chunk):
        return vad_script.pop(0)

    def transcribe(pcm):
        calls["transcribed"].append(pcm)
        return (transcripts or ["placeholder"]).pop(0) if transcripts is not None else "placeholder"

    return WhisperRecognizer(vad=vad, transcribe=transcribe, **kwargs), calls


def feed_all(rec, n):
    return [rec.feed(CHUNK) for i in range(n)]


# -- Segmentation state machine ----------------------------------------------

def test_silence_reports_nothing_and_never_transcribes():
    rec, calls = make([False] * 6)
    assert feed_all(rec, 6) == [None] * 6
    assert calls["transcribed"] == []


def test_speech_chunks_emit_speech_events():
    rec, _ = make([False, True, True])
    assert feed_all(rec, 3) == [
        None,
        RecognizerEvent(kind="speech", text=""),
        RecognizerEvent(kind="speech", text=""),
    ]


def test_segment_closes_after_tail_silence_and_emits_final():
    # speech, then 4 chunks (500ms) of silence -> tail (400ms) exceeded
    # on the 4th, so the segment transcribes and finalizes.
    rec, calls = make([True, True, False, False, False, False], transcripts=["open firefox"])
    events = feed_all(rec, 6)
    assert events[-1] == RecognizerEvent(kind="final", text="open firefox")
    assert events[2:5] == [None, None, None]  # tail still open: nothing to report
    assert len(calls["transcribed"]) == 1


def test_brief_silence_inside_utterance_does_not_split():
    # 125ms dip (1 silent chunk) is far below the 400ms tail: the two
    # speech runs must land in ONE segment, one transcribe call.
    rec, calls = make(
        [True, False, True, False, False, False, False],
        transcripts=["open firefox on monitor two"],
    )
    events = feed_all(rec, 7)
    finals = [e for e in events if e and e.kind == "final"]
    assert len(finals) == 1
    assert len(calls["transcribed"]) == 1


def test_paused_chain_dispatches_per_segment():
    # THE fork's headline feature: a pause longer than the tail splits the
    # chain into independently-dispatched segments -- two finals, in order.
    rec, _ = make(
        [True, False, False, False, False,   # "open firefox" + tail
         True, False, False, False, False],  # "lower volume" + tail
        transcripts=["Open Firefox.", "Lower volume."],
    )
    events = feed_all(rec, 10)
    finals = [e for e in events if e and e.kind == "final"]
    assert finals == [
        RecognizerEvent(kind="final", text="open firefox"),
        RecognizerEvent(kind="final", text="lower volume"),
    ]


def test_segment_audio_includes_preroll_and_speech_but_not_tail():
    # The transcribed buffer must include one pre-roll chunk before speech
    # onset (word starts are never aligned to chunk edges) plus all speech
    # chunks. 1 preroll + 2 speech = 3 chunks; the tail silence after the
    # last speech chunk is NOT worth transcribing... except the first tail
    # chunk, kept for the symmetric reason (word ends aren't aligned either).
    rec, calls = make([False, True, True, False, False, False, False])
    feed_all(rec, 7)
    fed_bytes = len(calls["transcribed"][0])
    assert fed_bytes == 4 * len(CHUNK)  # preroll + speech + speech + first tail chunk


def test_buffer_resets_between_segments():
    rec, calls = make(
        [True, False, False, False, False,
         True, False, False, False, False],
        transcripts=["one", "two"],
    )
    feed_all(rec, 10)
    # Second segment must not contain the first segment's audio:
    # both are speech(1)+tail(1), plus preroll only if silence preceded.
    assert len(calls["transcribed"][1]) <= len(calls["transcribed"][0]) + len(CHUNK)


# -- Whisper output cleanup (the seam's lowercase-clean contract) -------------

def test_final_text_is_lowercased_and_punctuation_stripped():
    rec, _ = make([True, False, False, False, False],
                  transcripts=["  Computer, open Firefox!  "])
    finals = [e for e in feed_all(rec, 5) if e and e.kind == "final"]
    assert finals == [RecognizerEvent(kind="final", text="computer open firefox")]


def test_standalone_digits_become_words():
    # Whisper writes "monitor 3" where Vosk wrote "monitor three"; slot
    # aliases and phrases are word-based, so normalize small numbers.
    rec, _ = make([True, False, False, False, False],
                  transcripts=["Open Dolphin on monitor 3."])
    finals = [e for e in feed_all(rec, 5) if e and e.kind == "final"]
    assert finals[0].text == "open dolphin on monitor three"


def test_bracketed_noise_tags_are_dropped_entirely():
    # Whisper labels non-speech as "[typing]" / "(music)" etc. A segment
    # that is nothing but tags cleans to empty -> no final at all.
    rec, _ = make([True, False, False, False, False], transcripts=["[typing sounds]"])
    events = feed_all(rec, 5)
    assert [e for e in events if e and e.kind == "final"] == []


def test_inline_noise_tag_is_stripped_from_real_text():
    rec, _ = make([True, False, False, False, False],
                  transcripts=["(door closes) mute the volume"])
    finals = [e for e in feed_all(rec, 5) if e and e.kind == "final"]
    assert finals[0].text == "mute the volume"


def test_apostrophes_survive_cleanup():
    # Contractions must match phrases the way Vosk produced them.
    rec, _ = make([True, False, False, False, False], transcripts=["What's playing?"])
    finals = [e for e in feed_all(rec, 5) if e and e.kind == "final"]
    assert finals[0].text == "what's playing"


def test_transcribe_failure_emits_error_event_and_keeps_running():
    # whisper-server down mid-session: transcribe raises. The segment is
    # LOST (acceptable) but feed() must NOT raise -- an exception here
    # propagates into the listener thread and kills listening app-wide with
    # no visible symptom. It now returns an "error" event (not None) so the
    # listener can warn the user the backend is down; segment 2 recovers.
    calls = {"n": 0}

    def flaky_transcribe(pcm):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection refused")
        return "open firefox"

    vad_script = [True, False, False, False, False,   # segment 1: server down
                  True, False, False, False, False]   # segment 2: recovered

    rec = WhisperRecognizer(vad=lambda c: vad_script.pop(0), transcribe=flaky_transcribe)
    events = [rec.feed(CHUNK) for _ in range(10)]
    errors = [e for e in events if e and e.kind == "error"]
    finals = [e for e in events if e and e.kind == "final"]
    # Segment 1 surfaced as an error carrying the failure message (not None,
    # not a raise); segment 2 transcribed fine.
    assert len(errors) == 1 and "connection refused" in errors[0].text
    assert finals == [RecognizerEvent(kind="final", text="open firefox")]
