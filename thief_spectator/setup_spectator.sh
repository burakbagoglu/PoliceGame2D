#!/bin/bash
# Thief Spectator (Seyir Ekrani) Kurulum Script'i
# Dusuk RAM'li cihazlar icin (orn. Pi 3 / Pi Zero). Tarayici gerektirmez.

echo "=== Thief Spectator Kurulumu ==="

echo "[1/5] Sistem guncelleniyor..."
sudo apt update

echo "[2/5] Gerekli paketler yukleniyor..."
sudo apt install -y python3-pip python3-pygame

echo "[3/5] Python paketleri yukleniyor..."
pip3 install --user -r requirements.txt || true

echo "[4/5] Systemd service kuruluyor..."
sudo cp thief-spectator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thief-spectator.service

echo "[5/5] Otomatik baslatma (GUI olmadan)..."
sudo raspi-config nonint do_boot_behaviour B2 || true

# Ekran kararmasini engelle (kiosk)
sudo raspi-config nonint do_blanking 1 || true

echo ""
echo "=== Kurulum Tamamlandi ==="
echo ""
echo "Onemli notlar:"
echo "1. config.json icinde server_base_url degerini Pi 5 IP adresine gore ayarlayin"
echo "2. 'sudo reboot' ile yeniden baslatin"
echo ""
echo "Manuel baslatma: python3 screen.py"
echo "Farkli sunucu:   python3 screen.py http://192.168.1.10:8078"
echo "Service:         sudo systemctl start thief-spectator"
echo "Loglar:          journalctl -u thief-spectator -f"
