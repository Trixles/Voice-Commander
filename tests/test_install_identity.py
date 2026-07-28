"""
Tests for the install identity: every path, unit name, lock socket, and
KWin namespace must derive from core/paths.py APP_NAME rather than
repeating the name as a literal.

That property is not academic. It is what made 2.0.0's rename
(voice-commander-whisper -> voice-commander, once the Vosk build was
retired to the Voice-Commander-Vosk repo) a two-constant edit instead of
an archaeology dig through every module. The sweep at the bottom is what
keeps it true: a hardcoded install name anywhere in core/ is a test
failure, so the next rename stays as cheap as this one was.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.paths as paths  # noqa: E402


def test_app_name():
    assert paths.APP_NAME == "voice-commander"


def test_derived_paths_live_under_the_app_namespace():
    assert paths.DATA_DIR.endswith("/.local/share/voice-commander")
    assert paths.CONFIG_DIR.endswith("/.config/voice-commander")
    assert paths.CONFIG_PATH == os.path.join(paths.CONFIG_DIR, "commands.json")
    assert paths.ICON_DIR == os.path.join(paths.DATA_DIR, "icons")
    assert paths.SERVICE_NAME == "voice-commander.service"
    assert paths.WHISPER_UNIT == "voice-commander-server.service"


def test_placer_id():
    # The placer's kwinrc config group is namespaced by its script Id
    # ("Script-<Id>"), so the Id owns the placement queue.
    assert paths.PLACER_ID == "vc-window-placer"


def test_commands_model_dirs_derive_from_paths():
    import core.commands as commands
    assert commands._DEFAULT_VOSK_MODEL_DIR.startswith(paths.DATA_DIR)
    assert commands._MODELS_DIR.startswith(paths.DATA_DIR)


def test_whisper_server_unit_and_env_derive_from_paths():
    import core.whisper_server as whisper_server
    assert whisper_server.UNIT == paths.WHISPER_UNIT
    assert whisper_server.ENV_PATH.startswith(paths.CONFIG_DIR)


def test_single_instance_lock_derives_from_app_name():
    import core.activate as activate
    assert paths.APP_NAME in activate.SINGLE_INSTANCE_NAME


def test_placer_group_and_path_derive_from_placer_id():
    import core.actions.windows as windows
    assert windows._PLACER_GROUP == f"Script-{paths.PLACER_ID}"


def test_install_name_is_never_hardcoded_outside_paths():
    # Sweep: no module under core/ (or the entry point) may write the
    # install name as a literal -- it must come from paths.APP_NAME.
    # core/paths.py is exempt: that's where the name is defined.
    # Comments and docstrings are exempt; this greps code lines only.
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths_py = os.path.join(root, "core", "paths.py")
    offenders = []
    files = [os.path.join(root, "voice_commander.py")]
    for dirpath, _, names in os.walk(os.path.join(root, "core")):
        files += [
            os.path.join(dirpath, n)
            for n in names
            if n.endswith(".py") and os.path.join(dirpath, n) != paths_py
        ]
    pattern = re.compile(r"voice-commander|vc-window-placer")
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
