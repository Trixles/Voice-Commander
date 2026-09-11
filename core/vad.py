"""
core/vad.py
===========
Silero voice-activity detection for the Whisper backend.

Chunk-level: called once per 125ms listener chunk, answers "is someone
talking in this chunk?". Silero is a small recurrent onnx model; the
h/c state carried between calls is what gives it context across chunk
boundaries, so one SileroVAD instance must see a single continuous
audio stream (the recognizer factory builds a fresh one per listener
run, matching the mic-restart lifecycle).

onnxruntime/numpy imports are deferred to __init__: engine imports stay
out of module scope (see the recognizer-seam invariant), so importing
this module for tests never drags in the runtime.
"""

from core.recognizer import SAMPLE_RATE


class SileroVAD:
    def __init__(self, model_path: str, threshold: float = 0.5):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        # Single-threaded session: Silero is a ~1ms micro-model, but
        # onnxruntime's default is an intra-op pool sized to the CPU
        # whose idle workers BUSY-SPIN between inferences. With an
        # inference per 125ms chunk the pool never parks -- s33 measured
        # ~9 cores at 100% while idle in SLEEPING. One thread = no
        # worker pool = no spinning; latency is unchanged for a model
        # this small.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(model_path, sess_options=opts)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._threshold = threshold

    def __call__(self, chunk: bytes) -> bool:
        np = self._np
        audio = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        out, self._h, self._c = self._sess.run(
            None,
            {"input": audio[None, :], "sr": self._sr, "h": self._h, "c": self._c},
        )
        return float(out[0, 0]) >= self._threshold
