"""
core/recognizer.py
==================
The recognizer seam: decouples the listener state machine from any
concrete speech engine.

Contract every backend must honor:
  - feed(bytes) accepts 16kHz s16 mono PCM and returns a RecognizerEvent
    (or None when the backend has nothing to report for this chunk).
  - "partial" events stream mid-utterance text as words form; "final"
    events carry the end-of-utterance transcription; "speech" events
    (empty text) report voice activity from backends that have no
    streaming partials, so the listener's inactivity window can refresh.
  - Event text is always stripped and lowercase. The normalization lives
    HERE, not in the listener, so an engine's output style (Whisper:
    punctuated, capitalized prose) never leaks past the seam -- the wake
    check / overrides / matcher downstream see identical text regardless
    of engine.

The listener never imports a speech engine; it consumes events. The seam
is what lets the state machine be tested engine-free (test_listener.py
injects scripted recognizers) and kept the Vosk->Whisper migration from
being a listener rewrite.
"""

import re
from dataclasses import dataclass

# One home for the capture format. pw-record's invocation and every
# recognizer must agree on this; it used to be hardcoded in two places
# in core/listener.py.
SAMPLE_RATE = 16000


@dataclass(frozen=True)
class RecognizerEvent:
    kind: str  # "partial" | "final" | "speech" | "error"
    text: str  # for "error", the failure message (not user text)


# -- Whisper output cleanup ----------------------------------------------------
# Whisper emits punctuated, capitalized prose with digits ("Open Dolphin on
# monitor 3.") and labels non-speech in brackets ("[typing]", "(music)").
# The seam contract wants bare lowercase words.

_NOISE_TAG_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_NON_WORD_RE = re.compile(r"[^a-z0-9']+")  # keep apostrophes: "what's playing"
_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def _clean_whisper_text(raw: str) -> str:
    """Normalize Whisper prose to the seam contract. May return ""
    (e.g. a segment that was nothing but a noise tag) -- callers treat
    empty as nothing-to-report."""
    text = _NOISE_TAG_RE.sub(" ", raw).lower()
    text = _NON_WORD_RE.sub(" ", text)
    # Whisper writes "monitor 3", but phrases and slot aliases are
    # word-based, so standalone small digits become words. Multi-digit
    # tokens pass through untouched.
    return " ".join(_DIGIT_WORDS.get(tok, tok) for tok in text.split())


class WhisperRecognizer:
    """VAD-segmenting wrapper for batch engines (whisper-server).

    Whisper has no streaming partials: whole audio in -> text out. So this
    class segments speech itself: buffer chunks while the injected VAD
    reports voice, and when silence has lasted `tail_ms`, hand the whole
    segment to `transcribe` and emit ONE "final". Mid-speech chunks emit
    "speech" events so the listener's inactivity window tracks the voice.

    Each VAD-split segment dispatches independently -- that is the fix for
    paused command chains (a pause > tail_ms simply closes one segment and
    the next one matches on its own).

    Injected collaborators (kept injectable for tests and backend choice):
      vad(chunk: bytes) -> bool        speech present in this 125ms chunk?
      transcribe(pcm: bytes) -> str    raw engine text for a whole segment

    Buffering detail: one chunk of pre-roll before speech onset and the
    first tail chunk after it are included in the segment, because word
    boundaries never align with chunk edges.
    """

    # Each VAD segment of a chain arrives as its OWN final -- the listener
    # must keep LISTENING after a match so the rest of the chain can land
    # (see "per-segment chains" in the listener).
    per_segment_finals = True

    def __init__(self, vad, transcribe, tail_ms: int = 400):
        self._vad = vad
        self._transcribe = transcribe
        self._tail_ms = tail_ms
        self._buffer: list = []
        self._preroll: bytes = b""
        self._in_speech = False
        self._silence_ms = 0.0

    def feed(self, data: bytes):
        chunk_ms = len(data) / 2 / SAMPLE_RATE * 1000  # s16 = 2 bytes/sample

        if self._vad(data):
            if not self._in_speech:
                self._in_speech = True
                self._buffer = [self._preroll] if self._preroll else []
            self._buffer.append(data)
            self._silence_ms = 0.0
            return RecognizerEvent(kind="speech", text="")

        if not self._in_speech:
            self._preroll = data
            return None

        # Silence inside an open segment: wait out the tail.
        self._silence_ms += chunk_ms
        if self._silence_ms == chunk_ms:
            self._buffer.append(data)  # first tail chunk: keeps the word's end
        if self._silence_ms < self._tail_ms:
            return None

        # Tail complete: close and transcribe the segment.
        pcm = b"".join(self._buffer)
        self._buffer = []
        self._in_speech = False
        self._silence_ms = 0.0
        self._preroll = data
        try:
            raw = self._transcribe(pcm)
        except Exception as e:
            # Server down/unreachable: drop THIS segment but keep the
            # recognizer alive -- an exception escaping feed() kills the
            # listener thread and leaves the app deaf with a healthy tray
            # icon. systemd is restarting the server (Restart=on-failure);
            # the next segment gets a fresh chance. Surface it as an
            # "error" event (not None) so the listener can warn the user
            # LOUDLY that transcription is down -- an outage must never
            # present as a dead app with a healthy tray icon (the
            # ggml-0.20 breakage, 2026-08-17).
            print(f"[recognizer] transcribe failed, segment dropped: {e}")
            return RecognizerEvent(kind="error", text=str(e))
        text = _clean_whisper_text(raw)
        if not text:
            return None
        return RecognizerEvent(kind="final", text=text)


def make_recognizer_factory():
    """Build the whisper recognizer factory.

    Called once at app startup (voice_commander.py). Heavy one-time setup
    happens HERE -- the whisper-server unit spin-up -- while the returned
    zero-arg factory only does cheap per-listener-run work, so mic
    restarts stay fast.
    """
    import core.commands as commands
    import core.vad as vad
    import core.whisper_client as whisper_client
    import core.whisper_server as whisper_server

    # Bring the transcription server up now (unit restart if config
    # changed). Failure is non-fatal: the listener surfaces unreachable-
    # server errors per segment, and systemd keeps retrying the unit.
    whisper_server.ensure_server()

    url = f"http://127.0.0.1:{commands.get_whisper_server_port()}/inference"
    vad_model = commands.get_vad_model_path()
    tail_ms = commands.get_whisper_vad_tail_ms()

    def factory():
        return WhisperRecognizer(
            vad=vad.SileroVAD(vad_model),
            transcribe=lambda pcm: whisper_client.transcribe_pcm(pcm, url),
            tail_ms=tail_ms,
        )

    return factory
