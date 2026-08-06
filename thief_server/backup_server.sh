#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${THIEF_BACKUP_DIR:-/var/backups/polisoyunu}"
RETENTION_DAYS="${THIEF_BACKUP_RETENTION_DAYS:-30}"

case "$BACKUP_DIR" in
    /*) ;;
    *) echo "Hata: THIEF_BACKUP_DIR mutlak bir yol olmali." >&2; exit 2 ;;
esac
case "$BACKUP_DIR" in
    /|/var|/home|/opt|/usr|/etc) echo "Hata: yedek hedefi fazla genis: $BACKUP_DIR" >&2; exit 2 ;;
esac
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || { echo "Hata: saklama suresi sayi olmali." >&2; exit 2; }

install -d -m 700 "$BACKUP_DIR"
STAGING="$(mktemp -d "$BACKUP_DIR/.staging.XXXXXX")"
cleanup() { rm -rf -- "$STAGING"; }
trap cleanup EXIT

copy_if_present() {
    local source="$1"
    local target="$2"
    if [[ -e "$source" ]]; then
        cp -a -- "$source" "$STAGING/$target"
    fi
}

copy_if_present "$SCRIPT_DIR/config.json" config.json
copy_if_present "$SCRIPT_DIR/scene_data" scene_data
copy_if_present "$SCRIPT_DIR/photo_sessions" photo_sessions
copy_if_present "$SCRIPT_DIR/runtime_state.json" runtime_state.json
copy_if_present /etc/police-game/photos.env photos.env

cat >"$STAGING/manifest.txt" <<EOF
created_at=$(date --iso-8601=seconds)
hostname=$(hostname)
server_dir=$SCRIPT_DIR
EOF

STAMP="$(date +%Y%m%d-%H%M%S)"
FINAL="$BACKUP_DIR/polisoyunu-$STAMP.tar.gz"
TEMP_ARCHIVE="$BACKUP_DIR/.polisoyunu-$STAMP.tar.gz.tmp"
tar -C "$STAGING" -czf "$TEMP_ARCHIVE" .
chmod 600 "$TEMP_ARCHIVE"
mv -- "$TEMP_ARCHIVE" "$FINAL"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'polisoyunu-*.tar.gz' \
    -mtime "+$RETENTION_DAYS" -delete

echo "Yedek hazir: $FINAL"