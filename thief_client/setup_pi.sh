#!/usr/bin/env bash
set -Eeuo pipefail

# Pi Zero 2 W tek komut kurulum/guncelleme araci.
# Ornek:
#   sudo ./thief_client/setup_pi.sh --screen-id 1 --server 192.168.1.10

SERVICE_NAME="thief-game"
INSTALL_ROOT="/opt/polisoyunu"
SCREEN_ID=""
SERVER_ADDRESS=""
SERIAL_PORT="/dev/ttyUSB0"
TARGET_USER="${SUDO_USER:-${USER:-pi}}"
WIFI_SSID=""
WIFI_PASSWORD=""
WIFI_COUNTRY="TR"
SKIP_APT_UPDATE=0
START_NOW=1

usage() {
    cat <<'EOF'
Kullanim:
  sudo ./thief_client/setup_pi.sh --screen-id 1 --server 192.168.1.10 [secenekler]

Zorunlu:
  --screen-id N          Ekran numarasi (1-8)
  --server IP|URL        Pi 4 adresi. Ornek: 192.168.1.10 veya http://192.168.1.10:8078

Secenekler:
  --serial-port PATH     Arduino seri portu (varsayilan /dev/ttyUSB0)
  --wifi-ssid SSID       Otomatik baglanilacak Wi-Fi adi
  --wifi-password PASS   Wi-Fi parolasi (8-63 karakter)
  --wifi-country CC      Iki harfli ulke kodu (varsayilan TR)
  --user USER            Servisi calistiracak kullanici
  --install-root PATH    Kurulum dizini (varsayilan /opt/polisoyunu)
  --skip-apt-update      apt update adimini atla
  --no-start             Kur ama servisi hemen baslatma
  -h, --help             Bu yardimi goster

Script tekrar calistirilabilir; mevcut config yedeklenir ve ekran/server ayarlari korunarak guncellenir.
EOF
}

log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { printf '\nHATA: %s\n' "$*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --screen-id) SCREEN_ID="${2:-}"; shift 2 ;;
        --server) SERVER_ADDRESS="${2:-}"; shift 2 ;;
        --serial-port) SERIAL_PORT="${2:-}"; shift 2 ;;
        --wifi-ssid) WIFI_SSID="${2:-}"; shift 2 ;;
        --wifi-password) WIFI_PASSWORD="${2:-}"; shift 2 ;;
        --wifi-country) WIFI_COUNTRY="${2:-}"; shift 2 ;;
        --user) TARGET_USER="${2:-}"; shift 2 ;;
        --install-root) INSTALL_ROOT="${2:-}"; shift 2 ;;
        --skip-apt-update) SKIP_APT_UPDATE=1; shift ;;
        --no-start) START_NOW=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Bilinmeyen secenek: $1" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || fail "Bu script sudo ile calistirilmali."
[[ "${SCREEN_ID}" =~ ^[1-8]$ ]] || fail "--screen-id 1 ile 8 arasinda olmali."
[[ -n "${SERVER_ADDRESS}" ]] || fail "--server zorunlu."
id "${TARGET_USER}" >/dev/null 2>&1 || fail "Kullanici bulunamadi: ${TARGET_USER}"
[[ "${INSTALL_ROOT}" == /* ]] || fail "--install-root mutlak bir yol olmali."
if [[ -n "${WIFI_SSID}" ]]; then
    [[ "${WIFI_SSID}" != *$'\n'* && "${WIFI_SSID}" != *$'\r'* ]] || fail "Wi-Fi adi satir sonu iceremez."
    (( ${#WIFI_SSID} <= 32 )) || fail "Wi-Fi adi en fazla 32 karakter olmali."
    (( ${#WIFI_PASSWORD} >= 8 && ${#WIFI_PASSWORD} <= 63 )) || fail "Wi-Fi parolasi 8-63 karakter olmali."
    [[ "${WIFI_COUNTRY}" =~ ^[A-Za-z]{2}$ ]] || fail "Wi-Fi ulke kodu iki harf olmali."
    WIFI_COUNTRY="${WIFI_COUNTRY^^}"
elif [[ -n "${WIFI_PASSWORD}" ]]; then
    fail "--wifi-password kullanildiysa --wifi-ssid de zorunlu."
fi
case "${INSTALL_ROOT%/}" in
    ""|/|/opt|/usr|/var|/home|/root) fail "Guvenli olmayan install-root: ${INSTALL_ROOT}" ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
[[ -f "${SOURCE_ROOT}/thief_client/main.py" ]] || fail "Proje kok dizini bulunamadi."

if [[ "${SERVER_ADDRESS}" =~ ^https?:// ]]; then
    SERVER_BASE="${SERVER_ADDRESS%/}"
else
    SERVER_BASE="http://${SERVER_ADDRESS%/}"
fi
[[ "${SERVER_BASE}" =~ :[0-9]+$ ]] || SERVER_BASE="${SERVER_BASE}:8078"

export DEBIAN_FRONTEND=noninteractive
if ((SKIP_APT_UPDATE == 0)); then
    log "Paket listesi guncelleniyor"
    apt-get update
fi

log "Pygame, serial, ag ve kurulum araclari yukleniyor"
apt-get install -y --no-install-recommends \
    python3 python3-pygame python3-serial python3-requests \
    rsync curl ca-certificates

WIFI_RESULT="mevcut sistem ayari"
if [[ -n "${WIFI_SSID}" ]]; then
    log "Wi-Fi otomatik baglantisi ayarlaniyor: ${WIFI_SSID}"
    command -v nmcli >/dev/null 2>&1 || fail "NetworkManager/nmcli bulunamadi; Raspberry Pi OS Bookworm veya daha yenisini kullanin."
    command -v raspi-config >/dev/null 2>&1 && raspi-config nonint do_wifi_country "${WIFI_COUNTRY}" || true
    command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi || true
    nmcli radio wifi on || true
    WIFI_CONNECTION="polisoyunu-wifi"
    if nmcli -t -f NAME connection show | grep -Fxq "${WIFI_CONNECTION}"; then
        nmcli connection modify "${WIFI_CONNECTION}" 802-11-wireless.ssid "${WIFI_SSID}"
    else
        nmcli connection add type wifi ifname wlan0 con-name "${WIFI_CONNECTION}" ssid "${WIFI_SSID}"
    fi
    nmcli connection modify "${WIFI_CONNECTION}" \
        connection.autoconnect yes connection.autoconnect-priority 100 \
        ipv4.method auto ipv6.method auto \
        wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${WIFI_PASSWORD}"
    WIFI_RESULT="${WIFI_SSID} (otomatik baglanti)"
fi

log "Kullanici donanim gruplarina ekleniyor"
for group in dialout video render input tty; do
    getent group "${group}" >/dev/null && usermod -a -G "${group}" "${TARGET_USER}" || true
done

log "Proje ${INSTALL_ROOT} dizinine kopyalaniyor"
install -d -m 0755 "${INSTALL_ROOT}"
EXISTING_CONFIG="${INSTALL_ROOT}/thief_client/config.json"
CONFIG_BACKUP=""
if [[ -f "${EXISTING_CONFIG}" ]]; then
    CONFIG_BACKUP="$(mktemp)"
    cp "${EXISTING_CONFIG}" "${CONFIG_BACKUP}"
fi
if [[ "$(readlink -f "${SOURCE_ROOT}")" != "$(readlink -f "${INSTALL_ROOT}")" ]]; then
    rsync -a --delete \
        --exclude='.git/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
        --exclude='thief_client/scene_cache/' --exclude='thief_server/photo_sessions/' \
        "${SOURCE_ROOT}/" "${INSTALL_ROOT}/"
fi
if [[ -n "${CONFIG_BACKUP}" ]]; then
    cp "${CONFIG_BACKUP}" "${EXISTING_CONFIG}"
    rm -f "${CONFIG_BACKUP}"
fi

log "Client config ayarlaniyor"
python3 - "${EXISTING_CONFIG}" "${SCREEN_ID}" "${SERVER_BASE}" "${SERIAL_PORT}" <<'PY'
import json
import os
import sys
import tempfile

path, screen_id, server_base, serial_port = sys.argv[1:]
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
data.update({
    "screen_id": int(screen_id),
    "server_base_url": server_base,
    "server_url": server_base.rstrip("/") + "/event",
    "serial_port": serial_port,
    "server_controlled": True,
    "fullscreen": True,
    "debug": False,
    "installed": True,
    "performance_profile": "pi_zero_2w",
    "render_width": 1280,
    "render_height": 720,
    "adaptive_quality": True,
})
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".json", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

chown -R "${TARGET_USER}:${TARGET_USER}" "${INSTALL_ROOT}"
chmod +x "${INSTALL_ROOT}/thief_client/setup_pi.sh"

log "Systemd servisi olusturuluyor"
cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Polis Oyunu - Pi Zero Client ${SCREEN_ID}
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${INSTALL_ROOT}/thief_client
ExecStart=/usr/bin/python3 -u ${INSTALL_ROOT}/thief_client/main.py
Restart=always
RestartSec=2
TimeoutStopSec=10
KillSignal=SIGINT
Environment=PYTHONUNBUFFERED=1
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_AUDIODRIVER=dummy
Environment=SDL_VIDEO_ALLOW_SCREENSAVER=0
Environment=THIEF_SCREEN_ID=${SCREEN_ID}
Environment=THIEF_SERVER_BASE_URL=${SERVER_BASE}
Environment=THIEF_SERVER_URL=${SERVER_BASE}/event
Environment=THIEF_SERIAL_PORT=${SERIAL_PORT}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

log "Ekran kararmasi kapatiliyor"
command -v raspi-config >/dev/null && raspi-config nonint do_blanking 1 || true

log "Kurulum dogrulaniyor"
python3 -m py_compile \
    "${INSTALL_ROOT}/thief_client/main.py" \
    "${INSTALL_ROOT}/thief_client/lib/"*.py
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
if ((START_NOW)); then
    systemctl restart "${SERVICE_NAME}.service"
    sleep 2
    systemctl is-active --quiet "${SERVICE_NAME}.service" || {
        journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager >&2
        fail "Servis baslatilamadi."
    }
fi

if curl --silent --show-error --max-time 3 "${SERVER_BASE}/health" >/dev/null; then
    SERVER_RESULT="ulasildi"
else
    SERVER_RESULT="su an ulasilamiyor; client baglanti gelince otomatik toparlanacak"
fi

cat <<EOF

============================================================
 Kurulum tamamlandi
 Ekran             : ${SCREEN_ID}
 Server            : ${SERVER_BASE} (${SERVER_RESULT})
 Arduino           : ${SERIAL_PORT}
 Wi-Fi             : ${WIFI_RESULT}
 Kurulum dizini    : ${INSTALL_ROOT}
 Servis            : ${SERVICE_NAME}.service
============================================================

Elektrik gidip geldiginde servis boot sirasinda otomatik acilir.
Oyun kapanir veya hata verirse systemd 2 saniye sonra yeniden baslatir.

Durum : sudo systemctl status ${SERVICE_NAME}
Log   : sudo journalctl -u ${SERVICE_NAME} -f
Yenile: ayni setup_pi.sh komutunu tekrar calistir
EOF
