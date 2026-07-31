# Production Checklist

Bu notlar Pi 4 server ve Pi Zero client kurulumunu sahada daha sorunsuz calistirmak icin kisa kontrol listesidir.

## Server

1. Pi 4 icin statik IP veya DHCP reservation ayarla. Varsayilan dokuman IP: `192.168.1.10`.
2. `thief_server/config.json` icinde:
   - `port`: `8078`
   - `debug`: `false`
   - `access_log`: `false`
   - `num_screens`: `8` (oyun mimarisi sabit sekiz ekran kullanir)
3. Kurulum:
   ```bash
   cd /home/pi/thief_server
   ./setup_server.sh
   sudo systemctl start thief-server
   sudo systemctl status thief-server
   ```
4. Kontrol:
   - Dashboard: `http://<PI4_IP>:8078/dashboard`
   - Seyir ekrani: `http://<PI4_IP>:8078/screen`
   - Health: `http://<PI4_IP>:8078/health`

## Client

Her Pi Zero icin:

1. `thief_client/config.json` icinde benzersiz `screen_id` ver.
2. `server_base_url` ve `server_url` degerlerini Pi 4 IP adresine gore ayarla:
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

1. **Tarayici** (guclu cihazlar): `http://<PI4_IP>:8078/screen`
2. **Pygame spectator** (dusuk RAM'li cihazlar, orn. Pi 3 / Pi Zero 512MB):
   `thief_spectator/` klasorundeki hafif uygulama. Chromium ~300MB+ RAM isterken
   bu uygulama ~40-60MB kullanir ve ayni JSON uclarini (`/score`, `/api/game/status`,
   `/history`) cekip ekrani native cizer.

   Kurulum (seyir cihazinda):
   ```bash
   cd /home/pi/thief_spectator
   # config.json icinde server_base_url'i Pi 4 IP'sine ayarla
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

## Server Pi 4 3.5 mm Analog Ses

Ses Pi Zero client'lardan degil, Pi 4 server'in dahili analog jakindan cikar:

```text
Pi 4 3.5 mm jak -> 3.5 mm ses kablosu -> aktif hoparlor
```

Pi 4 jak cikisi pasif hoparloru dogrudan surmek icin tasarlanmamistir; aktif
hoparlor veya harici amfi kullan.

Kontrol:

```bash
aplay -l
speaker-test -c 2 -t wav
```

`setup_server.sh`, Raspberry Pi OS destekliyorsa `raspi-config nonint do_audio 1`
ile analog cikisi secer. `thief_server/config.json` icindeki
`audio.device_name` varsayilan olarak `auto-analog` degerindedir. Server once
SDL cihazlarinda `Headphones`, `Analog` veya HDMI olmayan `bcm2835` cikisini
arar; listeleme yoksa `/proc/asound/cards` icinden dahili analog karti bulur.

Raspberry Pi OS Desktop/PipeWire kullaniyorsan mevcut cikislari ve varsayilani
su komutlarla kontrol edebilirsin:

```bash
wpctl status
wpctl set-default <ANALOG_SINK_ID>
```

Gerekirse systemd servisine cihaz adini acikca veren su override eklenebilir:

```ini
Environment=THIEF_AUDIO_DEVICE=plughw:Headphones,0
```

Kart adi model veya imaja gore farkliysa `aplay -l` ciktisindaki analog kart
adini kullan. Eski USB ses karti kurulumu gerekirse `THIEF_AUDIO_DEVICE=auto-usb`
hala desteklenir.

Harici dosya verilmezse server sentetik muzik ve efektlerle calisir. Kendi
dosyalarini kullanmak icin `music_file`, `hit_sound_file`,
`start_sound_file`, `success_sound_file` ve `end_sound_file` alanlari
server klasorune gore bagil dosya yolu olarak ayarlanabilir. Kisa efektler
icin WAV, muzik icin OGG onerilir.

## Fotoğraf Galerisi Güvenliği

- USB kamerayı Pi 4'e bağla ve `v4l2-ctl --list-devices` ile `/dev/video0` yolunu doğrula.
- `/etc/police-game/photos.env` dosyası `root:root` ve `0600` olmalı; içinde en az altı karakterli `THIEF_PHOTO_ADMIN_PIN` bulunmalı.
- PIN değiştirildikten sonra `sudo systemctl restart thief-server` çalıştırılmalı.
- `thief_server/photo_sessions/` klasörü düzenli yedeklenmeli ve yetkisiz ağ paylaşımlarına açılmamalı.
- Etkinlik sonunda gereksiz fotoğraflar operatör galerisinden silinmeli; aktif oturum silinemez.
- Fotoğraflı oyun başlamadan önce dashboarddaki onay kutusu yalnız gerçekten gerekli izin alındıysa işaretlenmeli.
