"""
core/wake.py
============
Wake word detector.

Substring check against the list of wake words on Vosk-transcribed text.
Zero external dependencies.
"""


class WakeWordDetector:
    def __init__(self, wake_words: list[str]):
        self.wake_words = [w.lower().strip() for w in wake_words]

    def check(self, text: str) -> bool:
        t = text.lower()
        return any(w in t for w in self.wake_words)
