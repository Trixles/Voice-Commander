"""
core/desktop.py
===============
Helpers for parsing freedesktop .desktop files.

Only the shared piece (extracting the executable token from an `Exec=`
value) lives here. Each caller keeps its own outer loop: commands.py
needs only the exec for browser detection; settings.py also wants
Name, Icon, NoDisplay and Type for the app picker. Their loops
legitimately differ.
"""

import re


def extract_exec_token(exec_value: str) -> str | None:
    """Pick the executable token out of a raw .desktop `Exec=` value.

    Strips `%x` field codes (e.g. `%u`, `%F`), then walks tokens left
    to right and returns the first one that isn't `env` and isn't a
    `KEY=VALUE` environment wrapper.

    Returns None if no usable token is found (empty Exec=, or only
    wrapper tokens).

    Examples:
        '/usr/bin/firefox --new-window %u' -> '/usr/bin/firefox'
        'env GDK_BACKEND=wayland gedit %F' -> 'gedit'
        ''                                 -> None
    """
    cleaned = re.sub(r"%\S", "", exec_value).strip()
    for token in cleaned.split():
        if token == "env":
            continue
        if "=" in token:
            continue
        return token
    return None
