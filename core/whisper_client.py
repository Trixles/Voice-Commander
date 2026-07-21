"""
core/whisper_client.py
======================
HTTP client for whisper-server's /inference endpoint. Stdlib only --
the transport must not add dependencies to the Qt-free config path or
the base install.

Returns RAW engine text: prose with punctuation/caps and possible noise
tags. Normalizing that to the seam contract is WhisperRecognizer's job
(core/recognizer.py), not the transport's.
"""

import io
import json
import urllib.request
import uuid
import wave

from core.recognizer import SAMPLE_RATE


def transcribe_pcm(pcm: bytes, url: str, timeout: float = 30.0) -> str:
    """POST a segment of 16kHz s16 mono PCM to whisper-server, return its
    raw transcription text. Raises OSError (urllib.error.URLError is a
    subclass) when the server is unreachable -- callers decide policy."""
    # whisper-server wants a WAV file upload, so wrap the raw PCM in a
    # RIFF header in memory.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)

    # Multipart/form-data by hand: stdlib has no helper. Each part is
    # separated by a boundary line that must not occur in the payload --
    # a uuid makes collision practically impossible.
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="segment.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode()
        + buf.getvalue()
        + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="response_format"\r\n\r\n'
            f"json\r\n--{boundary}--\r\n"
        ).encode()
    )

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["text"]
