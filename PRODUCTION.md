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

## Seyir Ekrani (Spectator)

Sunucudaki tarayici tabanli seyir ekrani iki sekilde gosterilebilir:

1. **Tarayici** (guclu cihazlar): `http://<PI5_IP>:8078/screen`
2. **Pygame spectator** (dusuk RAM'li cihazlar, orn. Pi 3 / Pi Zero 512MB):
   `thief_spectator/` klasorundeki hafif uygulama. Chromium ~300MB+ RAM isterken
   bu uygulama ~40-60MB kullanir ve ayni JSON uclarini (`/score`, `/api/game/status`,
   `/history`) cekip ekrani native cizer.

   Kurulum (seyir cihazinda):
   ```bash
   cd /home/pi/thief_spectator
   # config.json icinde server_base_url'i Pi 5 IP'sine ayarla
   ./setup_spectator.sh
   sudo systemctl start thief-spectator
   ```
   Manuel: `python3 screen.py` veya `python3 screen.py http://192.168.1.10:8078`.
   Cikis: `Esc` / `Q`. Tam ekran ac/kapa: `F11`.

## Kurulum Sihirbazi (Client)

Client ilk acildiginda (`config.json` icinde `installed: false` ise) klavye ile
kontrol edilen bir kurulum ekrani gelir. Oyun calisirken **S** tusu ile tekrar acilir.

Kontroller:
- `Yukari / Asagi`: ayar sec
- `Sol / Sag` veya `- / +`: degeri degistir (basili tutunca hizlanir)
- `Enter`: kaydet ve cik (config.json'a yazilir, `installed: true` olur)
- `Esc`: iptal

Ayarlanabilenler: pleksi/oynanabilir alan (crop), hirsiz boyutu/hizi, zemin cizgisi,
animasyon FPS, hedef band, ekran ID, vurus bekleme suresi. Tum degisiklikler canli
onizlenir.

### Pleksi / Oynanabilir alan (siyah bar)

Pleksinin oldugu bolge disinda kalan her yer siyah bar olur; oyun yalnizca pleksi
alaninda calisir ve daha az piksel render edildigi icin Pi Zero'da daha hizlidir.

`config.json` -> `playarea`:
- `enabled`: `true` ise crop devrede.
- `mode`:
  - `physical`: `screen_diagonal_in` (ekran kosegeni, inc) + `plexi_width_cm` /
    `plexi_height_cm` (pleksi olculeri, cm) girilir; PPI uzerinden piksele cevrilir.
  - `manual_px`: dogrudan `x`, `y`, `width`, `height` (px) girilir.
- `align_x` / `align_y`: `center` | `left`/`right` | `top`/`bottom` | `custom`.
  `custom` secilirse `margin_left_cm` / `margin_top_cm` ile bar kalinligi elle girilir.

Oyun koordinatlari cozunurlukten bagimsiz tutulur:
- `thief_ground_pct`: zemin cizgisi (oynanabilir alan yuksekliginin %'si)
- `band_center_pct` / `band_width_px`: hedef band merkezi ve genisligi
Bu sayede ayni config farkli ekran boyutlarinda oranti koruyarak calisir.

## Environment Override

SD imajini cogaltirken config dosyasina dokunmadan su environment degerleri kullanilabilir:

- Server: `THIEF_PORT`, `THIEF_HOST`, `THIEF_DEBUG`, `THIEF_ACCESS_LOG`, `THIEF_SERVER_CONFIG`
- Client: `THIEF_SCREEN_ID`, `THIEF_SERVER_BASE_URL`, `THIEF_SERVER_URL`, `THIEF_SERIAL_PORT`, `THIEF_FULLSCREEN`, `THIEF_DEBUG`

## Dayaniklilik / Kiosk

Sahada kesintisiz calismak icin yapilan sertlestirmeler:

- **Systemd (her 3 servis):** `Restart=always` + `StartLimitIntervalSec=0`. Cok sayida
  crash olsa bile servis yeniden baslatmayi birakmaz; temiz cikista bile tekrar acilir.
- **Atomik config kaydi (client):** Kurulum sihirbazi ayarlari gecici dosyaya yazip
  `os.replace` ile tasir (+`fsync`). Kayit sirasinda guc kesilse bile `config.json` bozulmaz.
- **Ekran kararmasi (kiosk):** `setup_*.sh` icinde `raspi-config nonint do_blanking 1`
  ile ekran kararmasi kapatilir; spectator ayrica `SDL_VIDEO_ALLOW_SCREENSAVER=0` ayarlar.
- **Spectator yeniden baglanma:** Sunucu kapaliyken ustel backoff (tavan 5s) ile bosa
  yoklama yapilmaz; sunucu gelince otomatik baglanir. "Baglanti yok" durumu ekranda gosterilir.
- **Boot sirasi:** Spectator/client sunucudan once acilirsa beklemeye gecer; sunucu
  hazir olunca veriler akar (ekstra ayar gerekmez).

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
