#!/bin/bash
# Raspberry Pi 4 Server Kurulum Script'i

echo "=== Thief Game Server Kurulumu ==="

# Sistem güncellemesi
echo "[1/4] Sistem güncelleniyor..."
sudo apt update && sudo apt upgrade -y

# Gerekli paketler
echo "[2/4] Ses ve Python paketleri yükleniyor..."
sudo apt install -y python3-pip python3-pygame alsa-utils fswebcam v4l-utils avahi-daemon libnss-mdns
sudo systemctl enable --now avahi-daemon
pip3 install --user -r requirements.txt

# Pi 4 dahili 3.5 mm jakını ses çıkışı olarak seç. Bazı minimal
# imajlarda non-interactive audio komutu bulunmayabilir; cihaz seçimi uygulama
# içindeki auto-analog algılamasıyla yine yapılır.
echo "Pi 4 analog ses çıkışı yapılandırılıyor..."
if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_audio 1 || \
        echo "Uyarı: Analog çıkış otomatik seçilemedi; PRODUCTION.md adımlarını uygulayın."
fi

# Fotoğraf galerisi için root-okumalı operatör PIN'i üret
PHOTO_ENV_DIR=/etc/police-game
PHOTO_ENV_FILE="$PHOTO_ENV_DIR/photos.env"
if ! sudo test -f "$PHOTO_ENV_FILE"; then
    PHOTO_ADMIN_PIN=$(tr -dc '0-9' </dev/urandom | head -c 8)
    sudo install -d -m 700 "$PHOTO_ENV_DIR"
    printf 'THIEF_PHOTO_ADMIN_PIN=%s\n' "$PHOTO_ADMIN_PIN" | sudo tee "$PHOTO_ENV_FILE" >/dev/null
    sudo chmod 600 "$PHOTO_ENV_FILE"
    echo "Fotoğraf galerisi operatör PIN'i: $PHOTO_ADMIN_PIN"
    echo "Bu PIN'i güvenli bir yerde saklayın."
fi

# Service dosyasını kopyala
echo "[3/4] Systemd servisleri ve gunluk yedekleme kuruluyor..."
chmod +x backup_server.sh
sudo cp thief-server.service thief-server-backup.service thief-server-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thief-server.service
sudo systemctl enable --now thief-server-backup.timer

# IP adresini göster
echo "[4/4] Ağ bilgileri..."
IP_ADDR=$(hostname -I | awk '{print $1}')
MDNS_HOST="$(hostname).local"

echo ""
echo "=== Kurulum Tamamlandı ==="
echo ""
echo "Server IP Adresi: $IP_ADDR"
echo "Server mDNS Adresi: http://$MDNS_HOST:8078"
echo ""
echo "Client'larda config.json içinde şunu ayarlayın:"
echo "  \"server_url\": \"http://$IP_ADDR:8078/event\""
echo "  \"server_base_url\": \"http://$IP_ADDR:8078\""
echo "  mDNS alternatifi: http://$MDNS_HOST:8078"
echo ""
echo "Manuel başlatma: python3 main.py"
echo "Service başlatma: sudo systemctl start thief-server"
echo "Logları görme: journalctl -u thief-server -f"
echo ""
echo "Dashboard: http://$IP_ADDR:8078/dashboard"
echo "Ekran: http://$IP_ADDR:8078/screen"
echo "Sahne Editörü: http://$IP_ADDR:8078/scene-editor"
echo ""
echo "USB kamera kontrolü: v4l2-ctl --list-devices"
echo "Pi 4 analog jak kontrolü: aplay -l"
echo "Ses testi: speaker-test -c 2 -t wav"
