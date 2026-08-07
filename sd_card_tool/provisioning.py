from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shlex
import tarfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
_SERIAL_RE = re.compile(r"^/dev/[A-Za-z0-9._/-]+$")
_EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", "scene_cache", ".git"}


@dataclass(frozen=True)
class ProvisionSettings:
    screen_id: int
    server_address: str
    serial_port: str
    wifi_ssid: str
    wifi_password: str
    wifi_country: str
    hostname: str
    username: str
    user_password: str
    enable_ssh: bool = True
    serial_baud: int = 9600
    render_width: int = 1280
    render_height: int = 720
    target_fps: int = 30
    min_fps: int = 24
    playarea_enabled: bool = True

    @property
    def server_base_url(self) -> str:
        return normalize_server_address(self.server_address)

    def validate(self) -> None:
        if not 1 <= int(self.screen_id) <= 8:
            raise ValueError("Ekran numarasi 1 ile 8 arasinda olmali.")
        normalize_server_address(self.server_address)
        if not _SERIAL_RE.fullmatch(self.serial_port) or ".." in self.serial_port.split("/"):
            raise ValueError("Seri port /dev/ttyUSB0 veya /dev/ttyACM0 gibi olmali.")
        if not self.wifi_ssid or len(self.wifi_ssid.encode("utf-8")) > 32:
            raise ValueError("Wi-Fi adi 1-32 byte arasinda olmali.")
        if "\n" in self.wifi_ssid or "\r" in self.wifi_ssid:
            raise ValueError("Wi-Fi adi satir sonu iceremez.")
        if not self.wifi_password or len(self.wifi_password) < 8:
            raise ValueError("Wi-Fi sifresi en az 8 karakter olmali.")
        if len(self.wifi_password) > 63:
            raise ValueError("Wi-Fi sifresi en fazla 63 karakter olmali.")
        if not re.fullmatch(r"[A-Za-z]{2}", self.wifi_country):
            raise ValueError("Wi-Fi ulke kodu TR gibi iki harf olmali.")
        if not _HOSTNAME_RE.fullmatch(self.hostname):
            raise ValueError("Hostname yalnizca kucuk harf, rakam ve tire icerebilir.")
        if not _USERNAME_RE.fullmatch(self.username):
            raise ValueError("Linux kullanici adi gecersiz.")
        if len(self.user_password) < 8:
            raise ValueError("Pi kullanici sifresi en az 8 karakter olmali.")
        if any(char in self.user_password for char in ("\n", "\r", ":")):
            raise ValueError("Pi sifresi satir sonu veya iki nokta iceremez.")
        if not 1200 <= int(self.serial_baud) <= 2_000_000:
            raise ValueError("Seri baud 1200-2000000 arasinda olmali.")
        if (int(self.render_width), int(self.render_height)) not in {(1280, 720), (1920, 1080)}:
            raise ValueError("Render cozunurlugu 1280x720 veya 1920x1080 olmali.")
        if not 15 <= int(self.target_fps) <= 60:
            raise ValueError("Hedef FPS 15-60 arasinda olmali.")
        if not 15 <= int(self.min_fps) <= int(self.target_fps):
            raise ValueError("Minimum FPS 15 ile hedef FPS arasinda olmali.")


def normalize_server_address(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise ValueError("Server adresi zorunlu.")
    if "://" not in value:
        value = "http://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Server adresi IP, hostname veya http(s) URL olmali.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Server adresinde endpoint yolu kullanmayin; yalnizca IP/host girin.")
    try:
        port = parsed.port or 8078
    except ValueError as exc:
        raise ValueError("Server portu gecersiz.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Server portu 1-65535 arasinda olmali.")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{port}"


def _should_include(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    if any(part in _EXCLUDED_DIRS for part in relative.parts):
        return False
    if path.is_file() and (path.suffix in {".pyc", ".pyo"} or path.name.startswith("test_")):
        return False
    return True


def build_client_archive(client_source: str | Path) -> tuple[str, str]:
    source = Path(client_source).resolve()
    if not (source / "main.py").is_file() or not (source / "setup_pi.sh").is_file():
        raise ValueError(f"Gecerli thief_client klasoru bulunamadi: {source}")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if not _should_include(path, source) or not path.is_file():
                continue
            archive.add(path, arcname=(Path("thief_client") / path.relative_to(source)).as_posix())
    payload = buffer.getvalue()
    return base64.b64encode(payload).decode("ascii"), hashlib.sha256(payload).hexdigest()


def _q(value: object) -> str:
    return shlex.quote(str(value))


def build_first_run_script(settings: ProvisionSettings, client_source: str | Path) -> str:
    settings.validate()
    payload, package_sha256 = build_client_archive(client_source)
    server_base = settings.server_base_url
    initial_config = json.dumps({
        "serial_baud": int(settings.serial_baud),
        "performance_profile": "pi_zero_2w",
        "render_width": int(settings.render_width),
        "render_height": int(settings.render_height),
        "fps": int(settings.target_fps),
        "min_fps": int(settings.min_fps),
        "adaptive_quality": True,
        "playarea_enabled": bool(settings.playarea_enabled),
    }, separators=(",", ":"))
    wifi_security = "" if not settings.wifi_password else f'''\nnmcli connection modify "$WIFI_CONNECTION" \\
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASSWORD"'''
    ssh_install = "" if not settings.enable_ssh else '''\napt-get install -y --no-install-recommends openssh-server
systemctl enable ssh.service
systemctl restart ssh.service || true'''
    return f'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
exec >>/var/log/polisoyunu-firstboot.log 2>&1

echo "[polisoyunu] Ilk acilis kurulumu basladi: $(date --iso-8601=seconds)"
CLIENT_USER={_q(settings.username)}
CLIENT_PASSWORD={_q(settings.user_password)}
WIFI_SSID={_q(settings.wifi_ssid)}
WIFI_PASSWORD={_q(settings.wifi_password)}
WIFI_COUNTRY={_q(settings.wifi_country.upper())}
WIFI_CONNECTION="polisoyunu-wifi"
DEVICE_HOSTNAME={_q(settings.hostname)}
SERVER_BASE={_q(server_base)}
SCREEN_ID={int(settings.screen_id)}
SERIAL_PORT={_q(settings.serial_port)}
INITIAL_CONFIG={_q(initial_config)}

if ! id -u "$CLIENT_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$CLIENT_USER"
fi
printf '%s:%s\\n' "$CLIENT_USER" "$CLIENT_PASSWORD" | chpasswd
usermod -aG sudo "$CLIENT_USER"
printf '%s ALL=(ALL) NOPASSWD:ALL\\n' "$CLIENT_USER" >/etc/sudoers.d/90-polisoyunu
chmod 440 /etc/sudoers.d/90-polisoyunu

printf '%s\\n' "$DEVICE_HOSTNAME" >/etc/hostname
if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then
    sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1\\t$DEVICE_HOSTNAME/" /etc/hosts
else
    printf '127.0.1.1\\t%s\\n' "$DEVICE_HOSTNAME" >>/etc/hosts
fi
hostname "$DEVICE_HOSTNAME" || true

command -v raspi-config >/dev/null 2>&1 && raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true
command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi || true
systemctl enable NetworkManager.service || true
systemctl restart NetworkManager.service
sleep 3
nmcli radio wifi on
nmcli connection delete "$WIFI_CONNECTION" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname wlan0 con-name "$WIFI_CONNECTION" ssid "$WIFI_SSID"
nmcli connection modify "$WIFI_CONNECTION" connection.autoconnect yes ipv4.method auto ipv6.method auto{wifi_security}

connected=0
for attempt in $(seq 1 12); do
    if nmcli --wait 15 connection up "$WIFI_CONNECTION"; then
        connected=1
        break
    fi
    echo "[polisoyunu] Wi-Fi bekleniyor ($attempt/12)"
    sleep 5
done
if [[ "$connected" != 1 ]]; then
    echo "[polisoyunu] Wi-Fi baglantisi kurulamadi; sonraki bootta tekrar denenecek."
    exit 20
fi

install -d -m 0755 /opt/polisoyunu-provision
base64 -d >/tmp/polisoyunu-client.tar.gz <<'POLISOYUNU_CLIENT_ARCHIVE'
{payload}
POLISOYUNU_CLIENT_ARCHIVE
printf '%s  %s\\n' {_q(package_sha256)} /tmp/polisoyunu-client.tar.gz | sha256sum --check
tar -xzf /tmp/polisoyunu-client.tar.gz -C /opt/polisoyunu-provision
chmod +x /opt/polisoyunu-provision/thief_client/setup_pi.sh

bash /opt/polisoyunu-provision/thief_client/setup_pi.sh \\
    --screen-id "$SCREEN_ID" \\
    --server "$SERVER_BASE" \\
    --serial-port "$SERIAL_PORT" \\
    --user "$CLIENT_USER"
python3 - /opt/polisoyunu/thief_client/config.json "$INITIAL_CONFIG" <<'PYCONFIG'
import json
import os
import sys
import tempfile

path, raw = sys.argv[1:]
with open(path, "r", encoding="utf-8") as handle:
    config = json.load(handle)
overrides = json.loads(raw)
playarea_enabled = overrides.pop("playarea_enabled")
config.update(overrides)
config.setdefault("playarea", {{}})["enabled"] = playarea_enabled
fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".json", dir=os.path.dirname(path))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PYCONFIG
chown "$CLIENT_USER:$CLIENT_USER" /opt/polisoyunu/thief_client/config.json
{ssh_install}
command -v raspi-config >/dev/null 2>&1 && raspi-config nonint do_boot_behaviour B2 || true

install -d -m 0755 /var/lib/polisoyunu
cat >/var/lib/polisoyunu/provisioned.json <<EOF
{{"screen_id":$SCREEN_ID,"server":"$SERVER_BASE","package_sha256":"{package_sha256}"}}
EOF
rm -rf -- /opt/polisoyunu-provision /tmp/polisoyunu-client.tar.gz

for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [[ -f "$cmdline" ]]; then
        sed -i -E 's/[[:space:]]+systemd\\.run=[^ ]+//g; s/[[:space:]]+systemd\\.run_success_action=[^ ]+//g; s/[[:space:]]+systemd\\.unit=kernel-command-line\\.target//g' "$cmdline"
    fi
done
CLIENT_PASSWORD= WIFI_PASSWORD=
rm -f -- /boot/firmware/firstrun.sh /boot/firstrun.sh
sync
echo "[polisoyunu] Kurulum tamamlandi: $(date --iso-8601=seconds)"
'''