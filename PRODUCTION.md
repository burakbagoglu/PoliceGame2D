# Production Checklist

Bu notlar Pi 5 server ve Pi Zero client kurulumunu sahada daha sorunsuz calistirmak icin kisa kontrol listesidir.

## Server

1. Pi 5 icin statik IP veya DHCP reservation ayarla. Varsayilan dokuman IP: `192.168.1.10`.
2. `thief_server/config.json` icinde:
   - `port`: `8078`
   - `debug`: `false`
   - `access_log`: `false`
   - `num_screens`: sahadaki ekran sayisi
3. Kurulum:
   ```bash
   cd /home/pi/thief_server
   ./setup_server.sh
   sudo systemctl start thief-server
   sudo systemctl status thief-server
   ```
4. Kontrol:
   - Dashboard: `http://<PI5_IP>:8078/dashboard`
   - Seyir ekrani: `http://<PI5_IP>:8078/screen`
   - Health: `http://<PI5_IP>:8078/health`

## Client

Her Pi Zero icin:

1. `thief_client/config.json` icinde benzersiz `screen_id` ver.
2. `server_base_url` ve `server_url` degerlerini Pi 5 IP adresine gore ayarla:
   ```json
   "server_base_url": "http://192.168.1.10:8078",
   "server_url": "http://192.168.1.10:8078/event"
   ```
3. Prod icin `debug` degeri `false` olmali.
4. Arduino portunu dogrula: `serial_port` genelde `/dev/ttyUSB0` veya `/dev/ttyACM0`.
5. Kurulum:
   ```bash
   cd /home/pi/thief_client
   ./setup_pi.sh
   sudo systemctl start thief-game
   sudo systemctl status thief-game
   ```

## Environment Override

SD imajini cogaltirken config dosyasina dokunmadan su environment degerleri kullanilabilir:

- Server: `THIEF_PORT`, `THIEF_HOST`, `THIEF_DEBUG`, `THIEF_ACCESS_LOG`, `THIEF_SERVER_CONFIG`
- Client: `THIEF_SCREEN_ID`, `THIEF_SERVER_BASE_URL`, `THIEF_SERVER_URL`, `THIEF_SERIAL_PORT`, `THIEF_FULLSCREEN`, `THIEF_DEBUG`

## Smoke Test

1. Server acikken `GET /health` 200 donmeli.
2. Her client `GET /spawn/poll?screen_id=N` ile serverda aktif ekran olarak gorunmeli.
3. Dashboarddan `Kisa Tur` baslat.
4. Her ekranda spawn geliyor mu kontrol et.
5. Bir vurus sonrasi:
   - Client lokal skoru artmali.
   - Dashboard toplam skoru artmali.
6. Dashboarddan skor sifirla:
   - Dashboard skoru 0 olmali.
   - Client ekrandaki lokal skor bir sonraki poll sonrasi 0 olmali.
