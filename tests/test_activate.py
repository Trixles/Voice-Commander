"""
tests/test_activate.py
======================
Single-instance activation plumbing (core/activate.py): the app-menu launcher
runs `voice-commander --activate`, which either tells the RUNNING instance to
open its Settings window (over the existing single-instance lock socket) or,
if nothing is running, starts the systemd user service. This is what makes
the app menu and the service coexist instead of racing for the lock.

Server side: `handle_pending` replaces the old read-nothing drain -- it reads
one line per connection and fires the open-settings callback on "open-settings",
while plain probe connections (terminal launches) still just get closed.

Run from the repo root:  pytest -q
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytest.importorskip("PySide6")

from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import activate  # noqa: E402

# Same lazy singleton pattern as the other Qt tests. Must be the full GUI
# QApplication, NOT QCoreApplication: tests share one process, and a bare
# QCoreApplication created first makes every later QApplication-needing test
# crash (Qt allows exactly one app object per process, kind included).
_app = QApplication.instance() or QApplication([])

# Use a test-private socket name so tests never touch (or get confused by)
# a real running Voice Commander instance on the developer's machine.
_TEST_SOCKET = f"vc-test-activate-{os.getpid()}"


def _listening_server():
    QLocalServer.removeServer(_TEST_SOCKET)
    server = QLocalServer()
    assert server.listen(_TEST_SOCKET)
    return server


def _pump(ms: int = 200) -> None:
    """Process Qt events for up to `ms` so socket signals get delivered."""
    from PySide6.QtCore import QDeadlineTimer, QEventLoop
    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        _app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)


def test_send_activation_reaches_running_instance():
    """--activate against a live instance delivers 'open-settings'."""
    server = _listening_server()
    opened = []
    server.newConnection.connect(
        lambda: activate.handle_pending(server, lambda: opened.append(True)))

    assert activate.send_activation(name=_TEST_SOCKET) is True
    _pump()
    assert opened == [True]
    server.close()


def test_send_activation_returns_false_when_nobody_listens():
    """No running instance: send fails so the caller starts the service."""
    QLocalServer.removeServer(_TEST_SOCKET)
    assert activate.send_activation(name=_TEST_SOCKET) is False


def test_activate_starts_service_when_not_running(monkeypatch):
    """activate() falls back to `systemctl --user start` when nobody holds
    the lock -- the app-menu path never runs the app standalone."""
    QLocalServer.removeServer(_TEST_SOCKET)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(activate, "run_capture", fake_run)
    rc = activate.activate(name=_TEST_SOCKET)

    assert rc == 0
    assert calls == [["systemctl", "--user", "start", "voice-commander.service"]]


def test_activate_does_not_start_service_when_running(monkeypatch):
    """activate() against a live instance must NOT touch systemctl."""
    server = _listening_server()
    server.newConnection.connect(
        lambda: activate.handle_pending(server, lambda: None))
    calls = []
    monkeypatch.setattr(
        activate, "run_capture",
        lambda argv, **kw: calls.append(argv))

    rc = activate.activate(name=_TEST_SOCKET)
    _pump()

    assert rc == 0
    assert calls == []
    server.close()


def test_probe_connection_without_data_is_just_closed():
    """A plain probe (terminal launch checking the lock) sends nothing --
    handle_pending must close it without firing the callback or hanging."""
    server = _listening_server()
    opened = []
    server.newConnection.connect(
        lambda: activate.handle_pending(server, lambda: opened.append(True)))

    probe = QLocalSocket()
    probe.connectToServer(_TEST_SOCKET)
    assert probe.waitForConnected(500)
    probe.disconnectFromServer()
    _pump()

    assert opened == []
    server.close()


def test_unknown_message_is_ignored():
    """Future-proofing: an unrecognized message must not open settings."""
    server = _listening_server()
    opened = []
    server.newConnection.connect(
        lambda: activate.handle_pending(server, lambda: opened.append(True)))

    sock = QLocalSocket()
    sock.connectToServer(_TEST_SOCKET)
    assert sock.waitForConnected(500)
    sock.write(b"make-me-a-sandwich\n")
    sock.flush()
    _pump()
    sock.disconnectFromServer()

    assert opened == []
    server.close()
