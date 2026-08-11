#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

# Dashboard'dan gelen update komutu hicbir URL, branch veya shell girdisi
# tasimaz. Root updater yalniz bu sabit kaynagi guncelleyebilir.
readonly REPO_URL="https://github.com/burakbagoglu/PoliceGame2D.git"
readonly BRANCH="main"
readonly INSTALL_ROOT="${POLIS_UPDATE_INSTALL_ROOT:-/opt/polisoyunu}"
readonly TARGET_USER="${POLIS_UPDATE_TARGET_USER:-pi}"
readonly STATUS_DIR="/var/lib/polisoyunu"
readonly STATUS_FILE="${STATUS_DIR}/update-status.json"
readonly LOCK_FILE="/run/lock/polisoyunu-client-update.lock"

TMP_ROOT=""
STAGE_ROOT=""
ROLLBACK_ROOT=""
NEW_VERSION=""

log() { printf '[client-update] %s\n' "$*"; }
fail() { log "HATA: $*" >&2; return 1; }

write_status() {
    local state="$1"
    local version="${2:-}"
    local message="${3:-}"
    install -d -m 0755 "${STATUS_DIR}"
    python3 - "${STATUS_FILE}" "${state}" "${version}" "${message}" <<'PY'
import json
import os
import sys
import tempfile
import time

path, state, version, message = sys.argv[1:]
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix="update-status-", suffix=".json", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({
            "state": state[:24],
            "version": version[:40],
            "message": message[:240],
            "updated_at": int(time.time()),
        }, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

safe_remove_tree() {
    local path="${1:-}"
    case "${path}" in
        /var/tmp/polisoyunu-update.*|/opt/.polisoyunu-stage.*|/opt/.polisoyunu-failed.*|/opt/.polisoyunu-rollback.*)
            [[ -e "${path}" ]] && rm -rf -- "${path}"
            ;;
        "") ;;
        *) fail "Guvenli olmayan gecici yol silinmedi: ${path}" ;;
    esac
}

restore_previous_release() {
    [[ -n "${ROLLBACK_ROOT}" && -d "${ROLLBACK_ROOT}" ]] || return 0
    local failed_root="/opt/.polisoyunu-failed.$$.${RANDOM}"
    if [[ -e "${INSTALL_ROOT}" ]]; then
        mv -- "${INSTALL_ROOT}" "${failed_root}"
    fi
    mv -- "${ROLLBACK_ROOT}" "${INSTALL_ROOT}"
    ROLLBACK_ROOT=""
    systemctl restart thief-game.service || true
    safe_remove_tree "${failed_root}" || true
}

cleanup() {
    safe_remove_tree "${TMP_ROOT}" || true
    safe_remove_tree "${STAGE_ROOT}" || true
}

on_error() {
    local exit_code=$?
    trap - ERR
    restore_previous_release || true
    write_status "failed" "${NEW_VERSION}" "Guncelleme basarisiz (kod ${exit_code}); onceki surum korundu" || true
    cleanup
    exit "${exit_code}"
}

trap on_error ERR
trap cleanup EXIT

[[ "${EUID}" -eq 0 ]] || fail "Updater root olarak calismali."
[[ "$#" -eq 0 ]] || fail "Updater arguman kabul etmez."
[[ "${INSTALL_ROOT}" == /opt/* && "${INSTALL_ROOT}" != /opt ]] || fail "Install root guvenli degil."
[[ "${INSTALL_ROOT}" != *[$'\t\r\n ']* ]] || fail "Install root bosluk veya satir sonu iceremez."
[[ "${TARGET_USER}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "Client kullanici adi guvenli degil."
[[ -d "${INSTALL_ROOT}" ]] || fail "Mevcut client kurulumu bulunamadi: ${INSTALL_ROOT}"
id "${TARGET_USER}" >/dev/null 2>&1 || fail "Client kullanicisi bulunamadi: ${TARGET_USER}"
command -v git >/dev/null 2>&1 || fail "git kurulu degil."
command -v rsync >/dev/null 2>&1 || fail "rsync kurulu degil."
command -v flock >/dev/null 2>&1 || fail "flock kurulu degil."

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "Baska bir guncelleme zaten calisiyor."
    exit 0
fi

write_status "running" "" "GitHub main branch indiriliyor"
TMP_ROOT="$(mktemp -d /var/tmp/polisoyunu-update.XXXXXX)"
STAGE_ROOT="$(mktemp -d /opt/.polisoyunu-stage.XXXXXX)"

log "Kaynak indiriliyor"
git clone --depth 1 --single-branch --branch "${BRANCH}" -- "${REPO_URL}" "${TMP_ROOT}/source"
NEW_VERSION="$(git -C "${TMP_ROOT}/source" rev-parse --short=12 HEAD)"

log "Yeni surum dogrulaniyor: ${NEW_VERSION}"
[[ -f "${TMP_ROOT}/source/thief_client/main.py" ]] || fail "Client main.py pakette yok."
[[ -f "${TMP_ROOT}/source/thief_client/update_pi.sh" ]] || fail "Updater pakette yok."
python3 -m py_compile \
    "${TMP_ROOT}/source/thief_client/main.py" \
    "${TMP_ROOT}/source/thief_client/lib/"*.py

rsync -a --delete \
    --exclude='.git/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
    --exclude='thief_client/scene_cache/' --exclude='thief_server/photo_sessions/' \
    "${TMP_ROOT}/source/" "${STAGE_ROOT}/"

# Cihaza ozel ekran/server/seri ayarlari yeni release'e tasinir.
if [[ -f "${INSTALL_ROOT}/thief_client/config.json" ]]; then
    install -d -m 0755 "${STAGE_ROOT}/thief_client"
    cp --preserve=mode "${INSTALL_ROOT}/thief_client/config.json" \
        "${STAGE_ROOT}/thief_client/config.json"
fi
chown -R "${TARGET_USER}:${TARGET_USER}" "${STAGE_ROOT}"
python3 -m py_compile \
    "${STAGE_ROOT}/thief_client/main.py" \
    "${STAGE_ROOT}/thief_client/lib/"*.py

log "Release atomik olarak degistiriliyor"
ROLLBACK_ROOT="/opt/.polisoyunu-rollback.$$.${RANDOM}"
if [[ -e "${INSTALL_ROOT}" ]]; then
    mv -- "${INSTALL_ROOT}" "${ROLLBACK_ROOT}"
fi
mv -- "${STAGE_ROOT}" "${INSTALL_ROOT}"
STAGE_ROOT=""

if ! systemctl restart thief-game.service; then
    fail "thief-game yeniden baslatilamadi."
fi
sleep 3
if ! systemctl is-active --quiet thief-game.service; then
    journalctl -u thief-game.service -n 60 --no-pager >&2 || true
    fail "Yeni client servisi aktif olmadi."
fi

# Basarili release kendi updater/service dosyalarini da yeniler.
install -o root -g root -m 0755 \
    "${INSTALL_ROOT}/thief_client/update_pi.sh" \
    /usr/local/sbin/polisoyunu-client-update
install -o root -g root -m 0755 \
    "${INSTALL_ROOT}/thief_client/request_update.sh" \
    /usr/local/sbin/polisoyunu-request-update
install -o root -g root -m 0644 \
    "${INSTALL_ROOT}/thief_client/thief-game-update.service" \
    /etc/systemd/system/thief-game-update.service
systemctl daemon-reload

safe_remove_tree "${ROLLBACK_ROOT}"
ROLLBACK_ROOT=""
write_status "success" "${NEW_VERSION}" "Guncelleme tamamlandi"
log "Guncelleme tamamlandi: ${NEW_VERSION}"
