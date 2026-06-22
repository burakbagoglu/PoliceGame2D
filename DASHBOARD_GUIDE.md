# Dashboard Kullanım Kılavuzu

Bu kılavuz Pi 5 üzerinde çalışan Hırsız Oyunu kontrol panelini sahada kullanmak için hazırlanmıştır.

## Adresler

Pi 5 varsayılan IP adresi örneği:

```text
192.168.1.10
```

Dashboard:

```text
http://192.168.1.10:8078/dashboard
```

Seyir ekranı:

```text
http://192.168.1.10:8078/screen
```

Health kontrol:

```text
http://192.168.1.10:8078/health
```

## Hızlı Başlangıç

Dashboard üst kısmındaki **Hızlı Başlangıç** alanı oyunu tek tıkla başlatmak içindir.

Mevcut modlar:

- **Kısa Tur**: 2 çocuk, 5 ekran, 6 dakika, kolay mod
- **Standart**: 5 çocuk, 12 ekran, 20 dakika, normal mod
- **Yoğun Mod**: 10 çocuk, 12 ekran, 30 dakika, zor mod

Bir moda tıklayınca dashboard çocuk sayısı, ekran sayısı, süre ve zorluk alanlarını otomatik doldurur ve oyunu başlatır.

## Manuel Oyun Başlatma

**Oyun Kontrolü** bölümündeki alanlar:

- **Çocuk**: Oyuna katılan çocuk sayısı
- **Ekran**: Aktif ekran sayısı
- **Süre**: Oyun süresi, dakika cinsinden
- **Zorluk**: Kolay, normal veya zor

Manuel başlatmak için:

1. Çocuk sayısını girin.
2. Ekran sayısını girin.
3. Süreyi seçin.
4. Zorluğu seçin.
5. **Oyunu Başlat** butonuna basın.

Oyun başladığında hedef skor otomatik hesaplanır.

## Skor Alanı

Dashboard üstündeki skor alanı:

- **Toplam Skor**: Tüm ekranlardan gelen toplam puan
- **Hedef**: Oyunun ulaşmaya çalıştığı skor
- **Progress bar**: Hedefe göre ilerleme yüzdesi

Skorlar client ekranlarından gelen başarılı vuruş eventleri ile artar.

## Oyun Durumu

**Oyun Durumu** kartında:

- **Durum**: Oyunun aktif veya pasif olduğunu gösterir.
- **Faz**: Oyunun mevcut temposu.
  - `WARMUP`: Başlangıç, daha sakin
  - `NORMAL`: Orta tempo
  - `INTENSE`: Son bölüm, daha yoğun
- **Geçen Süre**: Oyun başladığından beri geçen süre
- **Spawn Aralığı**: Hırsız çıkış sıklığı
- **Aciliyet**: Oyuncular hedefin gerisinde mi, ilerisinde mi
- **Toplam Spawn**: Server tarafından üretilen toplam hırsız çıkışı

## Oyunu Bitirme

Oyunu manuel bitirmek için:

```text
Oyunu Bitir
```

butonuna basın.

Bu işlem aktif spawn sürecini durdurur. Mevcut skor server tarafında kalır.

## Skorları Sıfırlama

Skorları sıfırlamak için:

```text
Skorları Sıfırla
```

butonuna basın.

Bu işlem:

- Toplam skoru sıfırlar.
- Ekran bazlı skorları sıfırlar.
- Client ekranlarına reset sinyali gönderir.
- Client üzerindeki lokal skorlar bir sonraki poll sonrası 0 olur.

## Ekran Skorları

**Ekran Skorları** bölümünde her ekranın puanı ayrı görünür.

Örnek:

```text
Ekran 1: 5
Ekran 2: 3
Ekran 3: 0
```

Bir ekran sürekli 0 kalıyorsa:

- O Pi Zero açık mı?
- `screen_id` doğru mu?
- Server IP ayarı doğru mu?
- Arduino hit gönderiyor mu?

kontrol edilmelidir.

## Son Olaylar

**Son Olaylar** bölümü son vuruş eventlerini gösterir.

Her kayıt şunları içerir:

- Hangi ekrandan geldiği
- Kaç puan geldiği
- Saat bilgisi

Bu bölüm, vuruşların server’a ulaşıp ulaşmadığını hızlı anlamak için kullanılır.

## Piezo Ayarları

Piezo ayarları Arduino hit hassasiyetini düzenlemek için kullanılır.

Alanlar:

- **Threshold**: Vuruş algılama eşiği
- **Refractory**: İki vuruş arasında bekleme süresi, milisaniye

Genel öneri:

- Yanlış vuruş çoksa threshold artırılır.
- Vuruş algılanmıyorsa threshold düşürülür.
- Çift sayma varsa refractory artırılır.

Ayarı değiştirdikten sonra:

```text
Uygula
```

butonuna basın.

Client’lar bu ayarı polling ile alır ve Arduino’ya iletir.

## Seyir Ekranı

Seyir ekranı oyunculara veya izleyicilere gösterilecek sade skor ekranıdır.

Adres:

```text
http://192.168.1.10:8078/screen
```

Bu ekranda:

- Toplam skor
- Hedef skor
- İlerleme
- Faz
- Son vuruş
- Polis arabası animasyonu

görünür.

## Sahada Hızlı Debug

Server durumunu kontrol etmek için Pi 5 üzerinde:

```bash
sudo systemctl status thief-server
journalctl -u thief-server -f
curl http://localhost:8078/health
```

Client durumunu kontrol etmek için Pi Zero üzerinde:

```bash
sudo systemctl status thief-game
journalctl -u thief-game -f
curl http://192.168.1.10:8078/health
```

Arduino port kontrolü:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
groups
```

`pi` kullanıcısı `dialout` grubunda olmalıdır.

## Yaygın Sorunlar

### Dashboard açılmıyor

Kontrol:

```bash
sudo systemctl status thief-server
curl http://localhost:8078/health
```

Pi 5 IP adresi değişmiş olabilir.

### Client skor göndermiyor

Kontrol:

```bash
cat /home/pi/thief_client/config.json
curl http://192.168.1.10:8078/health
journalctl -u thief-game -f
```

`server_url` ve `server_base_url` doğru olmalıdır.

### Vuruş algılanmıyor

Kontrol:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

Config içindeki `serial_port` gerçek port ile aynı olmalıdır.

### Aynı ekran iki kez görünüyor gibi davranıyor

Her Pi Zero’da `screen_id` benzersiz olmalıdır. 12 ekran için değerler `1` ile `12` arasında olmalıdır.

## Etkinlik Öncesi Kontrol Listesi

- Pi 5 açık ve dashboard erişilebilir.
- 12 Pi Zero açık.
- Her client farklı `screen_id` kullanıyor.
- Her client server’a `8078` portundan ulaşabiliyor.
- Dashboarddan kısa tur başlatılabiliyor.
- En az bir vuruş dashboard skorunu artırıyor.
- Skor sıfırlama client ekranlarına yansıyor.
- Seyir ekranı ayrı ekranda açık.
