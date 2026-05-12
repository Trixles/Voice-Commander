"""
hardware/respeaker.py
=====================
Optional LED control for the ReSpeaker XVF3800 USB 4-mic array.

Exposes a single public function:

    set_state(state) -> None

Call it whenever VC state changes. If xvf_host is not on PATH or the device
is absent, this module logs once at import time and all calls become no-ops.

LED color map (matched exactly to tray icon SVG fill colors):
    SLEEPING   -> solid #00a8d3 (cyan-blue)
    LISTENING  -> solid #66ff75 (mint green)
    CONFIRMING -> solid #66ff75 (mint green) -- same as LISTENING
    OPEN_MIC   -> solid #ff584b (coral red)
    ERROR      -> solid #ffc400 (amber)

xvf_host commands used:
    LED_EFFECT <0-4>       0=off 1=breath 2=rainbow 3=solid 4=DoA
    LED_COLOR <0xRRGGBB>   color for modes 1 and 3
    LED_BRIGHTNESS <0-255>
"""

import logging
import shutil
import subprocess

from core.context import State

log = logging.getLogger(__name__)

# -- State -> (color hex,)
#    All states use solid mode (effect 3), brightness 100
_BRIGHTNESS = "100"

_STATE_COLORS = {
    State.SLEEPING:   "0x00a8d3",
    State.LISTENING:  "0x66ff75",
    State.CONFIRMING: "0x66ff75",
    State.OPEN_MIC:   "0xff584b",
    State.ERROR:      "0xffc400",
}

# -- Detect xvf_host once at import time
_XVF_HOST = shutil.which("xvf_host")
if _XVF_HOST is None:
    log.warning("respeaker: xvf_host not found on PATH -- LED control disabled")


def _run(cmd: list[str]) -> None:
    """Fire-and-forget subprocess call. Logs on failure, never raises."""
    try:
        subprocess.run(
            cmd,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log.debug("respeaker: xvf_host call failed: %s", exc)


def set_state(state) -> None:
    """Update the ReSpeaker LED ring to reflect the given VC state.

    Safe to call with any state value; unknown states are silently ignored.
    Does nothing if xvf_host was not found at import time.
    """
    if _XVF_HOST is None:
        return

    color = _STATE_COLORS.get(state)
    if color is None:
        return

    _run([_XVF_HOST, "LED_EFFECT", "3"])
    _run([_XVF_HOST, "LED_COLOR", color])
    _run([_XVF_HOST, "LED_BRIGHTNESS", _BRIGHTNESS])
