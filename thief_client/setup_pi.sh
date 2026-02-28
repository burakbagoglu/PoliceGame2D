#!/bin/bash
# Raspberry Pi Zero 2 W Kurulum Script'i
# Piezo sensör doğrudan GPIO'ya bağlanır (Arduino gereksiz)

echo "=== Thief Game Client Kurulumu ==="

# Sistem güncellemesi
echo "[1/6] Sistem güncelleniyor..."
sudo apt update && sudo apt upgrade -y

# Gerekli paketler
echo "[2/6] Gerekli paketler yükleniyor..."
sudo apt install -y python3-pip python3-pygame python3-rpi.gpio

# Python paketleri
echo "[3/6] Python paketleri yükleniyor..."
pip3 install --user requests

# Kullanıcıyı gpio grubuna ekle
echo "[4/6] Kullanıcı ayarları..."
sudo usermod -a -G gpio $USER

# Service dosyasını kopyala
echo "[5/6] Systemd service kuruluyor..."
sudo cp thief-game.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thief-game.service

# Otomatik login ayarı (GUI olmadan)
echo "[6/6] Otomatik başlatma ayarlanıyor..."
sudo raspi-config nonint do_boot_behaviour B2

echo ""
echo "=== Kurulum Tamamlandı ==="
echo ""
echo "Önemli notlar:"
echo "1. config.json dosyasında screen_id değerini ayarlayın (1-5)"
echo "2. Piezo sensör bağlantısı:"
echo "   VCC → 3.3V (Pin 1)"
echo "   GND → GND  (Pin 6)"
echo "   S   → GPIO 17 (Pin 11)"
echo "3. 'sudo reboot' ile yeniden başlatın"
echo ""
echo "Manuel başlatma: python3 main.py"
echo "Service başlatma: sudo systemctl start thief-game"
echo "Logları görme: journalctl -u thief-game -f"
