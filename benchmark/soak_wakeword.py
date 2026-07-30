"""Live wake-model soak test -- the ONLY trustworthy false-fire measurement.

Why live-only: s33 proved offline file scoring cannot certify false-fire
behavior. Only 3 of 8 live fires reproduced when re-scored from the saved
audio, because the culprits are 10-30ms non-speech transients (keyboard
clicks, sighs) whose score depends on where the 80ms analysis frame
boundary lands -- and offline re-framing from sample 0 uses a different
alignment than the live stream did.

Taps the mic with the SAME pw-record invocation the production listener
uses, re-chunks to oww's 1280-sample frames, and scores every frame
through several configs at once:

  v2            -- ungated control (should still show transient fires)
  v2+vad        -- oww's Silero speech gate; what production runs today
  v2+vad+ver    -- gate plus a speaker verifier trained on Tyler's voice

Edit `instances` / `STREAMS` to put other candidates on trial; every
stream scores the SAME audio at the same instant, so the comparison is
controlled rather than before-and-after.

Fires dump the preceding 4s of audio so any phantom becomes a concrete
artifact instead of a mystery.

Usage:  ./oww-venv/bin/python soak_wakeword.py
Output: soak/soak_log.txt  +  soak/fire_<time>_<stream>_<score>.wav
"""

import collections
import datetime
import os
import subprocess
import sys
import wave

import numpy as np

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
from openwakeword.model import Model  # noqa: E402

# Per-round output dir, overridable: VC_SOAK_DIR=soak/round3 ./oww-venv/...
#
# Give every soak round its OWN directory. train_v2_verifier.py globs
# `soak/fire_*.wav` (non-recursive) as training NEGATIVES, so dumps left
# loose in soak/ get silently conscripted into the next retrain -- and a
# soak's fires include the operator genuinely saying the wake word.
# Training on a real "computer" as a negative poisons the verifier; the
# trainer already carries a hand-maintained exclude list because of it.
# A subdirectory keeps that list finite instead of growing every round.
OUT = os.path.join(BENCH, os.environ.get("VC_SOAK_DIR", "soak"))
os.makedirs(OUT, exist_ok=True)

SR = 16000
FRAME = 1280            # oww's native 80ms frame
READ_BYTES = 4000       # 125ms, mirrors the production listener
LOG_FLOOR = 0.20        # log any score at/above this
FIRE_THR = 0.40         # save audio at/above this
COOLDOWN_S = 2.0        # per-stream, avoids duplicate dumps of one event
RING_S = 4              # seconds of audio retained for dumps
VAD_THR = 0.5           # matches the shipped wake_vad_threshold default
WAKE_THR = 0.5          # production wake_threshold

# The verifier gate MUST equal production's wake_threshold, because that is
# what core/wakeword.py pins it to. Set it lower and the verifier also
# re-scores frames the base model rejected, where its output can PROMOTE a
# frame into firing -- a regime production never enters and nobody has
# measured. This ran at 0.3 until 2026-07-30, so any soak log older than
# that measured a config production does not run.
#
# Reading the verif stream: FIRE_THR (0.40) is deliberately below
# WAKE_THR, so this script logs fires production would not have woken on.
# Only verif-stream fires at/above 0.5 are production-equivalent; below
# that the frame never reached the gate and carries an unverified score.
VERIFIER_GATE = WAKE_THR

V2 = f"{BENCH}/wakewords/computer_v2.onnx"
VC = f"{BENCH}/wakewords/computer_vc.onnx"
V2_VERIFIER = f"{BENCH}/verifier_clips/computer_v2_verifier.pkl"

# One Model instance can host several wake models and shares the feature
# computation between them, so grouping keeps CPU down. Streams are named
# (instance, model_key) -> label.
instances = {
    "bare": Model(wakeword_models=[V2], inference_framework="onnx"),
    "gated": Model(wakeword_models=[V2], vad_threshold=VAD_THR,
                   inference_framework="onnx"),
    "verif": Model(wakeword_models=[V2], vad_threshold=VAD_THR,
                   custom_verifier_models={"computer_v2": V2_VERIFIER},
                   custom_verifier_threshold=VERIFIER_GATE,
                   inference_framework="onnx"),
}
STREAMS = [
    ("bare", "computer_v2", "v2"),            # no gate: the control
    ("gated", "computer_v2", "v2+vad"),       # what production runs today
    ("verif", "computer_v2", "v2+vad+ver"),   # the candidate
]

ring = collections.deque(maxlen=RING_S * SR)
last_fire: dict = {}
fire_counts = {label: 0 for _, _, label in STREAMS}
log_path = os.path.join(OUT, "soak_log.txt")


def log(msg):
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"{stamp} {msg}\n")


def dump_ring(label, score):
    stamp = datetime.datetime.now().strftime("%H%M%S")
    safe = label.replace("+", "_")
    path = os.path.join(OUT, f"fire_{stamp}_{safe}_{score:.2f}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.array(ring, dtype=np.int16).tobytes())
    return os.path.basename(path)


source = subprocess.run(["pactl", "get-default-source"],
                        capture_output=True, text=True).stdout.strip()
log(f"soak start (vad_threshold={VAD_THR}), source={source}")
proc = subprocess.Popen(
    ["pw-record", f"--target={source}", "--format=s16", f"--rate={SR}",
     "--channels=1", "-"],
    stdout=subprocess.PIPE,
)

buf = np.array([], dtype=np.int16)
frames = 0
peaks = {label: 0.0 for _, _, label in STREAMS}
HEARTBEAT_FRAMES = 10 * 60 * SR // FRAME  # ~10 minutes of audio

while True:
    data = proc.stdout.read(READ_BYTES)
    if not data:
        log("pw-record stream ended")
        break
    samples = np.frombuffer(data, dtype=np.int16)
    ring.extend(samples)
    buf = np.concatenate([buf, samples])

    while len(buf) >= FRAME:
        frame, buf = buf[:FRAME], buf[FRAME:]
        preds = {name: inst.predict(frame) for name, inst in instances.items()}
        now = frames * FRAME / SR
        frames += 1

        for inst_name, model_key, label in STREAMS:
            score = float(preds[inst_name][model_key])
            peaks[label] = max(peaks[label], score)
            if score < LOG_FLOOR:
                continue
            if now - last_fire.get(label, -99) < COOLDOWN_S:
                continue
            last_fire[label] = now
            if score >= FIRE_THR:
                fire_counts[label] += 1
                log(f"FIRE {label} score={score:.3f} -> {dump_ring(label, score)}")
            else:
                log(f"near {label} score={score:.3f}")

        if frames % HEARTBEAT_FRAMES == 0:
            mins = frames * FRAME / SR / 60
            log(f"heartbeat: {mins:.0f} min | "
                + " | ".join(f"{k} fires={fire_counts[k]} peak={peaks[k]:.3f}"
                             for k in peaks))
            peaks = {k: 0.0 for k in peaks}
