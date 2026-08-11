"""Tests for the allowlisted Pi client update control path."""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


_SPEC = spec_from_file_location(
    "thief_client_update_main",
    Path(__file__).with_name("main.py"),
)
_CLIENT_MAIN = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CLIENT_MAIN)
ThiefGame = _CLIENT_MAIN.ThiefGame


def test_update_status_is_added_to_telemetry(tmp_path, monkeypatch):
    status_file = tmp_path / "update-status.json"
    status_file.write_text(
        json.dumps({
            "state": "failed",
            "version": "abc123",
            "message": "service check failed",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("POLIS_UPDATE_STATUS_FILE", str(status_file))

    assert ThiefGame._read_update_status() == {
        "update_state": "failed",
        "update_version": "abc123",
        "update_error": "service check failed",
    }


def test_update_request_runs_only_fixed_helper(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(_CLIENT_MAIN.subprocess, "run", fake_run)

    assert ThiefGame._request_remote_update() is True
    assert calls[0][0] == [
        "sudo",
        "-n",
        "/usr/local/sbin/polisoyunu-request-update",
    ]
    assert "shell" not in calls[0][1]


def test_update_scripts_are_argument_free_and_allowlisted():
    root = Path(__file__).parent
    updater = (root / "update_pi.sh").read_text(encoding="utf-8")
    helper = (root / "request_update.sh").read_text(encoding="utf-8")
    setup = (root / "setup_pi.sh").read_text(encoding="utf-8")

    assert 'readonly BRANCH="main"' in updater
    assert 'readonly REPO_URL="https://github.com/burakbagoglu/PoliceGame2D.git"' in updater
    assert '[[ "$#" -eq 0 ]]' in updater
    assert "eval " not in updater
    assert '[ "$#" -eq 0 ]' in helper
    assert "systemctl start --no-block thief-game-update.service" in helper
    assert "NOPASSWD: /usr/local/sbin/polisoyunu-request-update" in setup
