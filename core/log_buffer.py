"""
core/log_buffer.py
==================
Shared in-memory log buffer for the Settings Log tab.

Written by the listener and the dispatcher as commands are heard,
matched, and fired. Read by the Settings dialog's Log tab via a
500ms polling timer.

Lives in its own leaf module to break a former circular-import shape:
commands.py needs to write to it, but it used to live in listener.py
which imports commands. The fix was eight function-scope
`from core.listener import LOG_BUFFER` lines inside commands.py to
defer the import past module load. With the buffer here, both modules
just `from core.log_buffer import LOG_BUFFER` at top of file.

# Quirks (preserved from ARCHITECTURE.md > LOG_BUFFER quirks)
# - _poll_log() in settings.py compares tuple(LOG_BUFFER) snapshots
#   rather than tracking a cursor, because LOG_BUFFER is a deque at
#   maxlen=200 and any cursor-based math breaks the moment the buffer
#   wraps.
# - The Settings 'Clear Log' button must clear LOG_BUFFER itself, not
#   just the QTextEdit widget -- otherwise the next poll repopulates
#   the widget from the buffer.
"""

from collections import deque


# Capped at 200 to bound memory. Lines are timestamped strings appended
# by the listener and dispatcher.
LOG_BUFFER: deque[str] = deque(maxlen=200)
