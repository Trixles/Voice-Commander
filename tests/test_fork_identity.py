"""
Tests for the fork's install identity: every path, unit name, lock
socket, and KWin namespace must live under "voice-commander-whisper" so
this fork can coexist on a machine with the parent Vosk install without
ever touching its files, services, or placer queue.

core/paths.py is the single source of truth; everything else derives.
These tests exist to make a hardcoded "voice-commander" string a test
failure instead of a silent collision with Tyler's daily driver.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.paths as paths  # noqa: E402


def test_app_name_is_the_fork():
    assert paths.APP_NAME == "voice-commander-whisper"


def test_derived_paths_live_under_the_fork_namespace():
    assert paths.DATA_DIR.endswith("/.local/share/voice-commander-whisper")
    assert paths.CONFIG_DIR.endswith("/.config/voice-commander-whisper")
    assert paths.CONFIG_PATH == os.path.join(paths.CONFIG_DIR, "commands.json")
    assert paths.ICON_DIR == os.path.join(paths.DATA_DIR, "icons")
    assert paths.SERVICE_NAME == "voice-commander-whisper.service"
    assert paths.WHISPER_UNIT == "voice-commander-whisper-server.service"


def test_placer_identity_is_forked():
    # The placer's kwinrc config group is namespaced by its script Id;
    # sharing the parent's Id would share (and corrupt) its queue.
    assert paths.PLACER_ID == "vcw-window-placer"
    assert paths.PLACER_ID != "vc-window-placer"


def test_commands_model_dirs_derive_from_paths():
    import core.commands as commands
    assert commands._DEFAULT_VOSK_MODEL_DIR.startswith(paths.DATA_DIR)
    assert commands._MODELS_DIR.startswith(paths.DATA_DIR)


def test_whisper_server_unit_and_env_derive_from_paths():
    import core.whisper_server as whisper_server
    assert whisper_server.UNIT == paths.WHISPER_UNIT
    assert whisper_server.ENV_PATH.startswith(paths.CONFIG_DIR)


def test_single_instance_lock_cannot_collide_with_parent():
    import core.activate as activate
    assert paths.APP_NAME in activate.SINGLE_INSTANCE_NAME


def test_placer_group_and_path_derive_from_placer_id():
    import core.actions.windows as windows
    assert windows._PLACER_GROUP == f"Script-{paths.PLACER_ID}"


def test_no_parent_paths_hardcoded_in_core():
    # Sweep: no module under core/ (or the entry point) may mention the
    # parent's bare install namespace. Comments/docstrings are exempt --
    # this greps only code lines. "voice-commander-whisper" contains
    # "voice-commander", so match the parent name only when NOT followed
    # by "-whisper".
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    files = [os.path.join(root, "voice_commander.py")]
    for dirpath, _, names in os.walk(os.path.join(root, "core")):
        files += [os.path.join(dirpath, n) for n in names if n.endswith(".py")]
    pattern = re.compile(r"voice-commander(?!-whisper)")
    for path in files:
        with open(path) as f:
            in_docstring = False
            for lineno, line in enumerate(f, 1):
                if line.count('"""') % 2 == 1:
                    in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                code = line.split("#")[0]
                if pattern.search(code):
                    offenders.append(f"{os.path.relpath(path, root)}:{lineno}")
    assert offenders == []
