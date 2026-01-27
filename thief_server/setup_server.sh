#!/bin/bash
# Raspberry Pi 5 Server Kurulum Script'i

echo "=== Thief Game Server Kurulumu ==="

# Sistem güncellemesi
echo "[1/4] Sistem güncelleniyor..."
sudo apt update && sudo apt upgrade -y

# Gerekli paketler
echo "[2/4] Python paketleri yükleniyor..."
pip3 install --user fastapi uvicorn pydantic

# Service dosyasını kopyala
echo "[3/4] Systemd service kuruluyor..."
sudo cp thief-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thief-server.service

# IP adresini göster
echo "[4/4] Ağ bilgileri..."
IP_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo "=== Kurulum Tamamlandı ==="
echo ""
echo "Server IP Adresi: $IP_ADDR"
echo ""
echo "Client'larda config.json içinde şunu ayarlayın:"
echo "  \"server_url\": \"http://$IP_ADDR:8000/event\""
echo ""
echo "Manuel başlatma: python3 -m uvicorn main:app --host 0.0.0.0 --port 8000"
echo "Service başlatma: sudo systemctl start thief-server"
echo "Logları görme: journalctl -u thief-server -f"
echo ""
echo "Dashboard: http://$IP_ADDR:8000/"
