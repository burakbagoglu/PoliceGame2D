import base64
import io
import shutil
import subprocess
import tarfile

import pytest

from sd_card_tool.provisioning import (
    ProvisionSettings,
    build_client_archive,
    build_first_run_script,
    normalize_server_address,
)


def settings(**overrides):
    values = {
        "screen_id": 3,
        "server_address": "192.168.1.10",
        "serial_port": "/dev/ttyACM0",
        "wifi_ssid": "Oyun WiFi",
        "wifi_password": "wifi-sifre-123",
        "wifi_country": "TR",
        "hostname": "polis-ekran-3",
        "username": "pi",
        "user_password": "pi-sifre-123",
        "enable_ssh": True,
    }
    values.update(overrides)
    return ProvisionSettings(**values)


def make_client(tmp_path):
    source = tmp_path / "thief_client"
    (source / "lib").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    (source / "setup_pi.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "lib" / "game.py").write_text("VALUE=1", encoding="utf-8")
    (source / "test_game.py").write_text("assert True", encoding="utf-8")
    (source / "__pycache__" / "main.pyc").write_bytes(b"cache")
    return source


def test_server_address_is_normalized():
    assert normalize_server_address("192.168.1.10") == "http://192.168.1.10:8078"
    assert normalize_server_address("https://game.local:9000/") == "https://game.local:9000"


@pytest.mark.parametrize("value", ["", "ftp://server", "http://server:99999", "http://server/api"])
def test_invalid_server_address_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_server_address(value)


def test_settings_reject_unsafe_values():
    with pytest.raises(ValueError, match="Ekran"):
        settings(screen_id=9).validate()
    with pytest.raises(ValueError, match="Seri"):
        settings(serial_port="COM3").validate()
    with pytest.raises(ValueError, match="Seri"):
        settings(serial_port="/dev/../etc/passwd").validate()
    with pytest.raises(ValueError, match="sifresi"):
        settings(wifi_password="short").validate()


def test_client_archive_excludes_tests_and_caches(tmp_path):
    source = make_client(tmp_path)
    encoded, digest = build_client_archive(source)
    payload = base64.b64decode(encoded)
    assert len(digest) == 64
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        names = archive.getnames()
    assert "thief_client/main.py" in names
    assert "thief_client/lib/game.py" in names
    assert not any("test_game" in name or "__pycache__" in name for name in names)


def test_first_run_script_contains_offline_client_and_no_git_clone(tmp_path):
    source = make_client(tmp_path)
    script = build_first_run_script(settings(), source)
    assert "POLISOYUNU_CLIENT_ARCHIVE" in script
    assert "--screen-id \"$SCREEN_ID\"" in script
    assert "http://192.168.1.10:8078" in script
    assert "git clone" not in script
    assert '"render_width":1280' in script
    assert '"serial_baud":9600' in script
    assert "PYCONFIG" in script
    assert r"systemd\.run_success_action" in script

    bash = shutil.which("bash")
    if bash:
        result = subprocess.run(
            [bash, "-n"], input=script.encode("utf-8"), capture_output=True
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")