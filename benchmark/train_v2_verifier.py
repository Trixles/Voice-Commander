"""Train + evaluate a custom speaker verifier for computer_v2.

The verifier is openWakeWord's own second-stage filter: a small classifier
that runs ONLY when the base model fires and answers "is this the target
speaker actually saying the wake word?". It lives inside the wake engine --
no listener changes, no new failure paths.

Training data:
  positives  -- Tyler saying "computer" (verifier_clips/pos_train)
  negatives  -- Tyler talking normally (verifier_clips/neg)
                + ROUND 1 false-fire audio (soak/round1), which is exactly
                  the "false activation examples" upstream recommends.

Held out so the evaluation isn't circular:
  ROUND 2 false fires  -- must be SUPPRESSED
  genuine "Computer." dumps + holdout positives -- must still PASS
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openwakeword
from openwakeword.model import Model
from spike_oww_wakeword import load_wav, CHUNK

BENCH = os.path.dirname(os.path.abspath(__file__))
# core/paths.py is the single source of install identity and is
# stdlib-only, so importing it here costs nothing and can't drift.
sys.path.insert(0, os.path.dirname(BENCH))
import core.paths as paths  # noqa: E402

V2 = f"{BENCH}/wakewords/computer_v2.onnx"
# Deploy target: the drop-in dir the app reads, alongside the .onnx
# models. install.sh skips files already present here, so a reinstall
# will not clobber it. The benchmark copy stays for the offline
# evaluation table this script prints.
OUT = os.path.join(paths.DATA_DIR, "wakewords", "computer_v2_verifier.pkl")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# Round-1 dumps used as training negatives. 131325 is excluded: it scored
# 0.99 on v2 with an empty transcript and may have been a genuine wake --
# training on a real "computer" as a negative would poison the verifier.
R1_EXCLUDE = {"fire_131325_v2_0.99.wav"}

# Round-2 dumps that are known-genuine (whisper transcribed "Computer.").
R2_GENUINE = {"fire_142546_v2_0.96.wav", "fire_142546_v2_vad_0.96.wav",
              "fire_152823_v2_0.97.wav", "fire_152823_v2_vad_0.97.wav",
              "fire_152824_vc_0.83.wav", "fire_152824_vc_vad_0.83.wav",
              "fire_152824_vc_vad_ver_0.79.wav",
              "fire_153306_v2_0.98.wav", "fire_153306_v2_vad_0.98.wav"}

pos = sorted(glob.glob(f"{BENCH}/verifier_clips/pos_train/*.wav"))
neg = sorted(glob.glob(f"{BENCH}/verifier_clips/neg/*.wav"))
neg += [p for p in sorted(glob.glob(f"{BENCH}/soak/round1/fire_*.wav"))
        if os.path.basename(p) not in R1_EXCLUDE]

print(f"training: {len(pos)} positives, {len(neg)} negatives "
      f"({len(neg) - 5} of them real false-fire audio)")
openwakeword.train_custom_verifier(
    positive_reference_clips=pos,
    negative_reference_clips=neg,
    output_path=OUT,
    model_name=V2,
)
print(f"saved {os.path.getsize(OUT)} bytes -> {OUT}\n")


def peak(path, verifier_threshold=None):
    kw = dict(wakeword_models=[V2], vad_threshold=0.5, inference_framework="onnx")
    if verifier_threshold is not None:
        kw["custom_verifier_models"] = {"computer_v2": OUT}
        kw["custom_verifier_threshold"] = verifier_threshold
    m = Model(**kw)
    a = load_wav(path)
    return max(float(m.predict(a[i:i + CHUNK])["computer_v2"])
               for i in range(0, len(a) - CHUNK, CHUNK))


held_false = [p for p in sorted(glob.glob(f"{BENCH}/soak/fire_*.wav"))
              if os.path.basename(p) not in R2_GENUINE]
held_true = [p for p in sorted(glob.glob(f"{BENCH}/soak/fire_*.wav"))
             if os.path.basename(p) in R2_GENUINE]
holdout_pos = sorted(glob.glob(f"{BENCH}/verifier_clips/pos_holdout/*.wav"))

print(f"{'clip':40s} {'no verifier':>12s} {'+verifier':>10s}")
for label, clips in (("HELD-OUT FALSE FIRES (want LOW)", held_false),
                     ("GENUINE WAKES (want HIGH)", held_true),
                     ("HOLDOUT POSITIVES (want HIGH)", holdout_pos)):
    print(f"-- {label}")
    for p in clips:
        print(f"  {os.path.basename(p):38s} {peak(p):12.3f} {peak(p, 0.3):10.3f}")
