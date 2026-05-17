"""
core/paths.py
=============
Canonical filesystem paths used across modules.

Lives here so commands.py and actions/windows.py both import the same
string instead of expanding ~/.config/voice-commander/commands.json
twice and risking drift.
"""

import os


CONFIG_PATH = os.path.expanduser("~/.config/voice-commander/commands.json")
