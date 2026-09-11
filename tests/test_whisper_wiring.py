"""
Tests for the Whisper backend wiring: config keys, the whisper-server
HTTP client, the server unit manager, and the backend factory.

No Qt, no engines, no network beyond a loopback stdlib HTTP server
started inside the client tests.
"""

import json
import http.server
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
import core.recognizer as recognizer  # noqa: E402
import core.whisper_client as whisper_client  # noqa: E402
import core.whisper_server as whisper_server  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_globals():
    orig_config, orig_commands = commands._config, commands._commands
    yield
    commands._config, commands._commands = orig_config, orig_commands


# -- Config keys ---------------------------------------------------------------

def test_whisper_port_default_and_override():
    commands._config = {}
    assert commands.get_whisper_server_port() == 8910
    commands._config = {"whisper_server_port": 9001}
    assert commands.get_whisper_server_port() == 9001


def test_vad_tail_default_and_override():
    commands._config = {}
    assert commands.get_whisper_vad_tail_ms() == 400
    commands._config = {"whisper_vad_tail_ms": 300}
    assert commands.get_whisper_vad_tail_ms() == 300


def test_whisper_model_path_from_size_name():
    commands._config = {"whisper_model": "small.en"}
    path = commands.get_whisper_model_path()
    assert path.endswith("models/whisper/ggml-small.en.bin")
    assert os.path.isabs(path)


def test_whisper_model_path_default_is_base_en():
    commands._config = {}
    assert commands.get_whisper_model_path().endswith("ggml-base.en.bin")


def test_vad_model_path_is_absolute():
    commands._config = {}
    path = commands.get_vad_model_path()
    assert os.path.isabs(path)
    assert path.endswith("silero_vad.onnx")


def test_emit_defaults_includes_whisper_keys():
    # install.sh's config generator must ship the whisper keys so users can
    # discover them. The Vosk arm is gone (s40 scrub): no backend selector,
    # no vosk_model -- whisper IS the recognizer.
    out = subprocess.run(
        [sys.executable, "-m", "core.commands", "--emit-defaults"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    config = json.loads(out.stdout)
    assert "recognizer_backend" not in config
    assert "vosk_model" not in config
    assert config["whisper_model"] == "base.en"
    assert config["whisper_server_port"] == 8910
    assert config["whisper_vad_tail_ms"] == 400


# -- whisper-server HTTP client ------------------------------------------------

class _FakeWhisperHandler(http.server.BaseHTTPRequestHandler):
    """Answers like whisper-server's /inference: swallows multipart, returns
    JSON. Records the request body on the server object for assertions."""

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.last_body = self.rfile.read(length)
        self.server.last_path = self.path
        payload = json.dumps({"text": self.server.reply_text}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeWhisperHandler)
    srv.reply_text = "  Open Firefox.  "
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_transcribe_pcm_posts_wav_and_returns_raw_text(fake_server):
    url = f"http://127.0.0.1:{fake_server.server_address[1]}/inference"
    pcm = b"\x01\x02" * 4000
    text = whisper_client.transcribe_pcm(pcm, url)
    # Raw engine text comes back untouched -- cleanup is WhisperRecognizer's
    # job (seam contract), not the transport's.
    assert text == "  Open Firefox.  "
    assert fake_server.last_path == "/inference"
    # The multipart body must contain a WAV (RIFF header) wrapping our PCM.
    assert b"RIFF" in fake_server.last_body
    assert pcm in fake_server.last_body


def test_transcribe_pcm_raises_on_unreachable_server():
    with pytest.raises(OSError):
        whisper_client.transcribe_pcm(b"\x00\x00", "http://127.0.0.1:1/inference")


# -- Server unit manager -------------------------------------------------------

@pytest.fixture
def systemctl_log(monkeypatch, tmp_path):
    """Capture systemctl invocations; redirect the env file into tmp."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(whisper_server.subprocess, "run", fake_run)
    monkeypatch.setattr(whisper_server, "ENV_PATH", str(tmp_path / "whisper-server.env"))
    commands._config = {"whisper_model": "base.en"}
    return calls


def test_ensure_server_writes_env_file_and_starts_unit(systemctl_log):
    whisper_server.ensure_server()
    with open(whisper_server.ENV_PATH) as f:
        env = f.read()
    assert "WHISPER_MODEL_PATH=" in env and "ggml-base.en.bin" in env
    assert "WHISPER_PORT=8910" in env
    # Fresh env file -> restart (not start): the unit may be running with
    # stale settings from a previous configuration.
    assert ["systemctl", "--user", "restart", whisper_server.UNIT] in systemctl_log


def test_ensure_server_unchanged_env_only_starts(systemctl_log):
    whisper_server.ensure_server()
    systemctl_log.clear()
    whisper_server.ensure_server()  # second call: env identical
    assert ["systemctl", "--user", "start", whisper_server.UNIT] in systemctl_log
    assert ["systemctl", "--user", "restart", whisper_server.UNIT] not in systemctl_log


def test_ensure_server_model_change_restarts(systemctl_log):
    whisper_server.ensure_server()
    systemctl_log.clear()
    commands._config = {"whisper_model": "small.en"}
    whisper_server.ensure_server()
    assert ["systemctl", "--user", "restart", whisper_server.UNIT] in systemctl_log


def test_ensure_server_survives_systemctl_failure(monkeypatch, tmp_path, capsys):
    # Dev mode: unit not installed -> systemctl fails. ensure_server must
    # report False and print the captured stderr, never raise (the caller
    # decides what a dead server means for the app).
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Unit not found.")

    monkeypatch.setattr(whisper_server.subprocess, "run", fake_run)
    monkeypatch.setattr(whisper_server, "ENV_PATH", str(tmp_path / "w.env"))
    commands._config = {}
    assert whisper_server.ensure_server() is False
    assert "Unit not found." in capsys.readouterr().out


# -- Backend factory -----------------------------------------------------------

def test_factory_whisper_backend_wires_vad_client_and_server(monkeypatch):
    commands._config = {
        "whisper_server_port": 9001,
        "whisper_vad_tail_ms": 300,
    }
    ensured = []
    monkeypatch.setattr(whisper_server, "ensure_server", lambda: ensured.append(True) or True)

    import core.vad as vad_mod
    vads = []
    monkeypatch.setattr(vad_mod, "SileroVAD", lambda path: vads.append(path) or "VAD")

    built = {}

    def fake_whisper_rec(vad, transcribe, tail_ms):
        built.update(vad=vad, transcribe=transcribe, tail_ms=tail_ms)
        return "WREC"

    monkeypatch.setattr(recognizer, "WhisperRecognizer", fake_whisper_rec)
    posted = []
    monkeypatch.setattr(
        whisper_client, "transcribe_pcm",
        lambda pcm, url: posted.append((pcm, url)) or "text",
    )

    factory = recognizer.make_recognizer_factory()
    assert ensured == [True]  # server ensured at wiring time, not per restart
    assert factory() == "WREC"
    assert built["tail_ms"] == 300
    assert built["vad"] == "VAD"
    assert vads and vads[0].endswith("silero_vad.onnx")
    # The transcribe callable must target the configured port.
    built["transcribe"](b"pcm")
    assert posted == [(b"pcm", "http://127.0.0.1:9001/inference")]
