"""
core/wake_dump.py
=================
Optional diagnostic capture of the audio that fires the wake engine.

The audio wake engine (`core/wakeword.py`) judges raw sound, not text, so
a false fire leaves nothing inspectable behind: the journal records the
engine's OUTPUT (a score) but not its INPUT (the ~1s waveform), which is
processed frame-by-frame in memory and discarded. This module keeps that
input around long enough to write it to disk WHEN a wake fires, turning a
misfire from a lost float into a concrete `.wav` we can replay through the
model and score with or without the verifier.

Privacy shape (deliberate): the ring is pushed ONLY while SLEEPING, so a
dump contains the ambient audio that led up to a wake and the wake sound
itself -- never the command you spoke afterward, which happens in
LISTENING and is never buffered. It is off by default (`wake_audio_dump`
config key) and capped, so a diagnostic period can't fill the disk.

Kept Qt-free and hardware-free: a pure ring buffer plus a stdlib `wave`
writer, unit-tested without a mic (`tests/test_wake_dump.py`).
"""

import collections
import glob
import os
import time
import wave

# Capture format mirrors the production listener's pw-record invocation:
# 16 kHz, mono, signed 16-bit. Kept here so the writer and the ring size
# agree without the caller having to thread the numbers through.
_RATE = 16000
_SAMPLE_BYTES = 2


class WakeAudioRing:
    """A fixed-duration ring of raw PCM bytes -- the last `seconds` of
    audio, trimmed from the front as new chunks arrive."""

    def __init__(self, seconds: float = 4.0):
        self._budget = int(seconds * _RATE * _SAMPLE_BYTES)
        self._chunks: collections.deque = collections.deque()
        self._nbytes = 0

    def push(self, data: bytes) -> None:
        self._chunks.append(data)
        self._nbytes += len(data)
        # Trim whole chunks from the front until back under budget. Keep at
        # least one chunk even if a single chunk exceeds the budget, so a
        # fire always has something to dump.
        while self._nbytes > self._budget and len(self._chunks) > 1:
            self._nbytes -= len(self._chunks.popleft())

    def snapshot(self) -> bytes:
        """The buffered audio, oldest-to-newest, as one PCM blob."""
        return b"".join(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._nbytes = 0


def write_wav(path: str, pcm: bytes) -> None:
    """Write raw PCM as a 16 kHz mono 16-bit WAV."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_SAMPLE_BYTES)
        w.setframerate(_RATE)
        w.writeframes(pcm)


def prune(directory: str, keep: int) -> None:
    """Delete oldest `wake_*.wav` beyond the `keep` newest. Filenames carry
    a sortable timestamp, so lexical order is chronological."""
    files = sorted(glob.glob(os.path.join(directory, "wake_*.wav")))
    for stale in files[:-keep] if keep > 0 else files:
        try:
            os.remove(stale)
        except OSError:
            pass  # best-effort; a diagnostic must never raise into the caller


def dump(directory: str, pcm: bytes, score: float, keep: int = 50) -> str:
    """Write `pcm` to `directory` as `wake_<timestamp>_<score>.wav`, prune
    to the newest `keep`, and return the path written. The timestamp is
    date-first so the lexical sort `prune` relies on is chronological."""
    os.makedirs(directory, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(directory, f"wake_{ts}_{score:.3f}.wav")
    write_wav(path, pcm)
    prune(directory, keep)
    return path
