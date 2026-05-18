"""
core/edid.py
============
EDID parsing for friendly monitor names.

Reads /sys/class/drm/card*-*/edid (raw EDID bytes the kernel reads from the
monitor at connect time) and shells out to edid-decode to extract the
Display Product Name descriptor for each connected output.

Why /sys/class/drm: it's the only display-server-independent source of EDID
data on Linux. kscreen-doctor's JSON output sets edid=null, xrandr is X11,
and parsing EDID bytes ourselves means reinventing the PNP-ID registry.
edid-decode (in the edid-decode package) does the parsing for us.

Hard rule: NO Qt imports here. This module is imported by core/actions/windows.py
which must remain Qt-free.
"""

import glob
import os
import re

from core.run import run_capture


# Connector dir naming: /sys/class/drm/card<N>-<port>
# Examples: card1-DP-1, card1-HDMI-A-2, card1-Writeback-1
# We want to extract <port> (DP-1, HDMI-A-2, ...) and skip Writeback connectors
# (virtual, used by screen capture, always empty EDID).
_CONNECTOR_RE = re.compile(r"^card\d+-(.+)$")

# edid-decode prints two lines we care about:
#   "    Display Product Name: 'G27Q'"
# We extract the quoted value.
_NAME_RE = re.compile(r"Display Product Name:\s*'([^']*)'")


def _connector_dirs() -> list[str]:
    """Return paths like /sys/class/drm/card1-DP-1 for real (non-Writeback) connectors."""
    out = []
    for path in sorted(glob.glob("/sys/class/drm/card*-*")):
        name = os.path.basename(path)
        m = _CONNECTOR_RE.match(name)
        if not m:
            continue
        port = m.group(1)
        if port.startswith("Writeback"):
            continue
        out.append(path)
    return out


def _port_name(connector_dir: str) -> str:
    """card1-DP-1 -> DP-1"""
    base = os.path.basename(connector_dir)
    m = _CONNECTOR_RE.match(base)
    if not m:
        raise ValueError(f"Not a connector dir: {connector_dir}")
    return m.group(1)


# Sentinels for _parse_one's three-state return.
_PARSE_EMPTY = object()    # EDID exists but is empty/zero -> caller should skip
_PARSE_NO_NAME = object()  # EDID has data but no Display Product Name descriptor


def _parse_one(edid_path: str):
    """
    Run edid-decode on one EDID file. Returns:
      - str: the Display Product Name, e.g. "G27Q"
      - _PARSE_EMPTY: EDID is empty (treat the connector as unplugged)
      - _PARSE_NO_NAME: EDID is present but lacks the name descriptor,
        OR edid-decode failed unexpectedly. Caller should show a fallback.
    """
    try:
        result = run_capture(
            ["/usr/bin/edid-decode", edid_path],
            timeout=5,
        )
    except Exception as e:
        print(f"[edid] edid-decode failed on {edid_path}: {e}")
        return _PARSE_NO_NAME

    combined = result.stdout + result.stderr
    if "was empty" in combined:
        return _PARSE_EMPTY

    m = _NAME_RE.search(combined)
    if m:
        name = m.group(1).strip()
        if name:
            return name
    return _PARSE_NO_NAME


def get_monitor_friendly_names() -> dict[str, str]:
    """
    Return {port_name: friendly_label} for every connected physical monitor.

    Friendly label is the EDID Display Product Name verbatim (e.g. "G27Q",
    "DELL P2222H", "SAMSUNG"). If the monitor's EDID lacks a Display Product
    Name descriptor, the label is "Unknown display".

    Monitors with empty EDID (unplugged ports, virtual Writeback connectors)
    are omitted from the result entirely.
    """
    out: dict[str, str] = {}
    for d in _connector_dirs():
        edid_path = os.path.join(d, "edid")
        if not os.path.exists(edid_path):
            continue

        # NOTE: we don't pre-check os.path.getsize(). sysfs files always
        # report st_size=0 regardless of how much data they emit when read,
        # so a size check would skip every monitor. Empty-EDID detection is
        # delegated to edid-decode, which prints "EDID ... was empty" when
        # the kernel populated the file with zeros (disconnected output).

        result = _parse_one(edid_path)
        if result is _PARSE_EMPTY:
            continue
        port = _port_name(d)
        if result is _PARSE_NO_NAME:
            out[port] = "Unknown display"
        else:
            out[port] = result
    return out


if __name__ == "__main__":
    # Manual smoke test: `python -m core.edid`
    import json
    print(json.dumps(get_monitor_friendly_names(), indent=2))
