Interaktif Hırsız Oyunu — Pygame Teknik Dokümanı (v2)
1) Kapsam

Kurulum:

5 ekran → 5 ayrı Raspberry Pi Zero 2 W (client)

Her ekranda cam + piezo → Arduino → Pi Zero (hit input)

Tüm skorlar → Raspberry Pi 5 (server)

Oyun:

Hırsız sprite’ı sağdan sola sürekli koşar.

Ekranda sabit “hedef band” vardır.

Hit geldiği anda hırsız band içindeyse: düşer +1 puan.

Her skor artışı Pi 5’e event olarak gönderilir.

2) Donanım ve Bağlantılar
2.1 Client (her ekran)

Raspberry Pi Zero 2 W

Arduino (piezo okuma + debounce)

Bağlantı: Arduino USB → Pi (Serial)

Arduino görevi:

Piezo analog okuyup “tek hit” üretmek

Debounce/refractory: 150–300 ms

Serial output: HIT\n

2.2 Server (merkez)

Raspberry Pi 5

Aynı LAN (router/AP)

3) Yazılım Mimarisi
3.1 Pi Zero (Client) bileşenleri

Pygame render + game loop (30 FPS)

HitInput (serial okuyucu thread)

GameState (RUN/FALL/RESET/COOLDOWN)

Animator (run/fall frame oynatma)

NetClient (Pi 5’e HTTP event post)

Config (screen_id, speed, band, vb.)

3.2 Pi 5 (Server) bileşenleri

HTTP API: POST /event

Idempotency (event_id tekrarlarını ignore)

Skor tutma: ekran bazlı + toplam

Opsiyonel dashboard: GET /score

4) Asset Gereksinimleri (Pygame’e uygun)
4.1 Zorunlu

Hırsız animasyonları

thief_run/frame_0001.png ...

thief_fall/frame_0001.png ...

Target band (tek PNG veya çizim)

ui/band.png (veya pygame ile rect çizilir)

Arka plan

bg/bg.png (tek görsel)

(opsiyonel parallax: bg_far.png, bg_mid.png, bg_near.png)

Gölge (önerilir)

misc/shadow.png

4.2 Önerilen efektler

Hit effect: effects/hit/frame_0001.png...

Miss icon: effects/miss.png

UI skor panel: ui/score_panel.png

Performans notu:

Video kullanma (Pi Zero’da risk)

PNG’leri makul boyutta tut (256–512 px karakter yüksekliği)

Run: 8–16 frame, Fall: 6–12 frame

Animasyon FPS: 12–15

5) Oyun Mantığı
5.1 Hedef Band

Band = x_min ile x_max arası dikey bölge

Hit geldiği anda:

thief_rect.centerx band aralığında mı?

Band ayarı:

Ortaya koy: band_center = width/2

Genişlik: 80–140 px (zorluk)

5.2 State Machine

RUN: hırsız hareket + run animasyonu

FALL: fall animasyonu, bitince reset

COOLDOWN: hit spam engeli (200 ms)

RESET: hırsız sağdan spawn → RUN

5.3 Skor

Başarı: +1

Fail: +0 (opsiyonel miss efekt/ses)

6) Network: Event gönderimi (Pi Zero → Pi 5)
6.1 Endpoint

POST http://<PI5_IP>:8000/event

Payload:

{
  "event_id": "uuid",
  "screen_id": 3,
  "points": 1,
  "ts_ms": 1730000000000
}

6.2 Idempotency

Client her başarıya unique event_id üretir.

Server aynı event_id tekrar gelirse ignore eder.

6.3 Offline davranışı (önerilen)

Server’a gönderim başarısızsa event’i local kuyruğa yaz (dosya)

Bağlantı gelince sırayla gönder

7) Client Proje Yapısı (önerilen)
thief_client/
  main.py
  config.json
  requirements.txt
  assets/
    thief_run/
    thief_fall/
    bg/
      bg.png
    ui/
      band.png
      score_panel.png
    effects/
      hit/
      miss.png
    misc/
      shadow.png
  lib/
    config.py
    animation.py
    hit_input.py
    net_client.py
    game.py

8) config.json (ekran bazlı ayar)
{
  "screen_id": 1,
  "server_url": "http://192.168.1.10:8000/event",
  "fps": 30,
  "thief_speed_px_s": 360,
  "spawn_x": 2100,
  "reset_x": -200,
  "band_x_min": 900,
  "band_x_max": 1020,
  "hit_cooldown_ms": 200,
  "fullscreen": true
}

9) Client Çalışma Akışı (Pygame)
9.1 Başlatma

config oku

pygame init

fullscreen window oluştur

tüm assetleri preload et

serial okuyucu thread başlat (hit queue)

ana loop başlat

9.2 Ana loop (30 FPS)

Her frame:

pygame event pump (QUIT, ESC)

hit queue kontrol

hit varsa check_band()

state’e göre update (pos + anim)

draw (bg → band → shadow → thief → ui)

pygame.display.flip()

10) Hit Input Tasarımı
10.1 Önerilen model: Thread + Queue

Serial dinleme thread’i HIT gördüğünde queue.put("HIT")

Ana thread queue.get_nowait() ile alır

Neden?

Serial I/O bloklar; oyun döngüsü asla bloklanmamalı.

11) Performans Kuralları (Pi Zero 2 W için)

FPS: 30 sabit

Convert:

surface = pygame.image.load(...).convert_alpha()

Her frame yeni Surface/Rect üretme, reuse et

Asset boyutlarını küçült (özellikle alpha)

Parallax varsa 2–3 layer yeter

12) Dağıtım (5 cihaz)
12.1 Ağ

Pi 5 statik IP: 192.168.1.10 önerilir

Pi Zero’lar DHCP reservation veya statik:

192.168.1.21 … 192.168.1.25

12.2 Tek imaj stratejisi

1 Pi’yi kur → SD imaj al → 5 SD’ye yaz

Sadece config.json içinde screen_id değiştir

12.3 Otomatik başlatma

systemd service ile oyun boot’ta başlasın

crash olursa restart

13) Test Planı (etkinlik öncesi zorunlu)
13.1 Fonksiyon

Band içi hit: her seferinde +1

Band dışı: +0

Cooldown: art arda vuruşlar çift saymıyor

13.2 Dayanıklılık

2 saat aralıksız çalıştır

Arduino çek-tak testi

Wi-Fi kopma/geri gelme testi

13.3 5 ekran entegrasyon

Server toplam skor doğru mu?

Bir client kapanınca diğerleri devam ediyor mu?

14) Minimum teslim edilecekler (MVP)

Hırsız run + fall

Target band

Background

Local score

Pi 5’e event gönderimi + total skor