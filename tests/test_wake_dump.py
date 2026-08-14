"""
Tests for the wake-audio diagnostic capture (`core/wake_dump.py`).

Pure ring-buffer + stdlib `wave` writer, so this runs with no mic and no
Qt. The ring holds the last few seconds of raw PCM; a fire dumps it to a
timestamped `.wav`, and the dump directory is pruned to a cap.
"""

import os
import sys
import wave

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.wake_dump as wake_dump  # noqa: E402

# 1s of 16 kHz mono 16-bit audio = 32000 bytes. A 0.1s chunk = 3200.
_ONE_SEC = 16000 * 2
_CHUNK = 3200


def test_ring_trims_to_the_duration_budget():
    ring = wake_dump.WakeAudioRing(seconds=1.0)
    for _ in range(20):  # 20 * 0.1s = 2s pushed into a 1s ring
        ring.push(b"\x00" * _CHUNK)
    # Held audio never exceeds the 1s budget (whole-chunk trimming may
    # leave it at or just under, never over).
    assert len(ring.snapshot()) <= _ONE_SEC


def test_ring_keeps_the_most_recent_audio():
    # The newest chunk must survive; the oldest must be evicted.
    # Budget 0.2s = 6400 bytes = exactly two 3200-byte chunks, so the
    # third push evicts the first.
    ring = wake_dump.WakeAudioRing(seconds=0.2)
    ring.push(b"\x01" * _CHUNK)   # oldest
    ring.push(b"\x02" * _CHUNK)
    ring.push(b"\x03" * _CHUNK)   # newest
    snap = ring.snapshot()
    assert snap.endswith(b"\x03" * _CHUNK)     # newest retained
    assert b"\x01" not in snap                 # oldest evicted


def test_ring_keeps_one_oversized_chunk():
    # A single chunk larger than the whole budget must still be dumpable,
    # not trimmed to nothing.
    ring = wake_dump.WakeAudioRing(seconds=0.05)  # 1600 bytes budget
    ring.push(b"\x07" * _CHUNK)                    # 3200 bytes, over budget
    assert ring.snapshot() == b"\x07" * _CHUNK


def test_clear_empties_the_ring():
    ring = wake_dump.WakeAudioRing(seconds=1.0)
    ring.push(b"\x00" * _CHUNK)
    ring.clear()
    assert ring.snapshot() == b""


def test_write_wav_roundtrips_as_16k_mono_16bit(tmp_path):
    pcm = b"\x11\x22" * 8000  # 8000 frames
    path = str(tmp_path / "clip.wav")
    wake_dump.write_wav(path, pcm)
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(w.getnframes()) == pcm


def test_dump_writes_scored_filename_and_returns_path(tmp_path):
    d = str(tmp_path / "dumps")
    path = wake_dump.dump(d, b"\x00" * 3200, score=0.823)
    assert os.path.isfile(path)
    name = os.path.basename(path)
    assert name.startswith("wake_")
    assert name.endswith("_0.823.wav")


def test_dump_creates_the_directory(tmp_path):
    d = str(tmp_path / "made" / "on" / "demand")
    wake_dump.dump(d, b"\x00" * 3200, score=0.5)
    assert os.path.isdir(d)


def test_prune_keeps_only_the_newest(tmp_path):
    d = str(tmp_path)
    # Timestamped names sort chronologically; make five, keep two.
    for stamp in ("20260101_000001", "20260101_000002", "20260101_000003",
                  "20260101_000004", "20260101_000005"):
        wake_dump.write_wav(os.path.join(d, f"wake_{stamp}_0.900.wav"), b"\x00" * 32)
    wake_dump.prune(d, keep=2)
    survivors = sorted(os.path.basename(p) for p in
                       __import__("glob").glob(os.path.join(d, "wake_*.wav")))
    assert survivors == ["wake_20260101_000004_0.900.wav",
                         "wake_20260101_000005_0.900.wav"]


def test_prune_ignores_unrelated_files(tmp_path):
    d = str(tmp_path)
    wake_dump.write_wav(os.path.join(d, "wake_20260101_000001_0.900.wav"), b"\x00" * 32)
    keeper = os.path.join(d, "notes.txt")
    open(keeper, "w").close()
    wake_dump.prune(d, keep=0)  # delete every wake_*.wav
    assert not __import__("glob").glob(os.path.join(d, "wake_*.wav"))
    assert os.path.isfile(keeper)  # non-dump files untouched
