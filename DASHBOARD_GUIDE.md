# Dashboard Kullanım Kılavuzu

Bu kılavuz Pi 4 üzerinde çalışan Hırsız Oyunu kontrol panelini sahada kullanmak için hazırlanmıştır.

## Adresler

Pi 4 varsayılan IP adresi örneği:

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

- **Kısa Tur**: 2 çocuk, 8 ekran, 30 dakika, kolay mod
- **Standart**: 5 çocuk, 8 ekran, 35 dakika, normal mod
- **Yoğun Mod**: 10 çocuk, 8 ekran, 40 dakika, zor mod

Bir moda tıklayınca dashboard çocuk sayısı, ekran sayısı, süre ve zorluk alanlarını otomatik doldurur ve oyunu başlatır.

## Manuel Oyun Başlatma

**Oyun Kontrolü** bölümündeki alanlar:

- **Çocuk**: Oyuna katılan çocuk sayısı
- **Ekran**: Sabit ekran sayısı (8)
- **Süre**: Oyun süresi, dakika cinsinden
- **Zorluk**: Kolay, normal veya zor

Manuel başlatmak için:

1. Çocuk sayısını girin.
2. Ekran sayısını 8 olarak bırakın.
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

## Server Ses Kontrolü

Ses, Pi 4'ün dahili 3.5 mm analog jakına bağlanan aktif hoparlörden çıkar.
Client cihazlarında hoparlör bulunması gerekmez.

Dashboard içindeki **Server Sesi** kartında:

- Pi 4 analog jak ve mixer durumu görüntülenir.
- Genel, müzik ve efekt seviyeleri ayarlanır.
- Vuruş efekti test edilir.
- Müzik test amaçlı açılıp kapatılır.

Yeni ve geçerli bir skor eventi geldiğinde vuruş sesi çalar. Aynı `event_id`
tekrar gönderildiğinde ses ikinci kez çalmaz. Oyun başlayınca müzik başlar;
oyun manuel, süre dolarak veya hedef tamamlanarak bittiğinde müzik durur.

Analog jak görünmüyorsa Pi 4 üzerinde:

```bash
aplay -l
speaker-test -c 2 -t wav
journalctl -u thief-server -f
```

komutlarıyla cihaz ve server logları kontrol edilmelidir.

## Sahada Hızlı Debug

Server durumunu kontrol etmek için Pi 4 üzerinde:

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

Pi 4 IP adresi değişmiş olabilir.

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

Her Pi Zero’da `screen_id` benzersiz olmalıdır. Sabit 8 ekran için değerler `1` ile `8` arasında olmalıdır.

## Etkinlik Öncesi Kontrol Listesi

- Pi 4 açık ve dashboard erişilebilir.
- 8 Pi Zero açık.
- Her client farklı `screen_id` kullanıyor.
- Her client server’a `8078` portundan ulaşabiliyor.
- Dashboarddan kısa tur başlatılabiliyor.
- En az bir vuruş dashboard skorunu artırıyor.
- Skor sıfırlama client ekranlarına yansıyor.
- Seyir ekranı ayrı ekranda açık.
## USB Kamera ve Oturum Fotoğrafları

Kamera Pi 4 server'a USB üzerinden bağlanır; client ekranlarında kamera gerekmez. Dashboard üstündeki **Fotoğraflar** bağlantısı `/photos` operatör sayfasını açar.

İlk kurulumda `setup_server.sh`, `fswebcam` ve `v4l-utils` paketlerini yükler. Ayrıca sekiz haneli bir operatör PIN'i üretip yalnız root tarafından okunabilen `/etc/police-game/photos.env` dosyasına yazar. PIN repoya veya dashboard HTML'ine kaydedilmez. Galeri oturumu 8 saat sonra sona erer; tüm görüntüleme, indirme, satış, kamera testi ve silme API'leri giriş gerektirir.

Oyun öncesi akış:

1. Aynı tarayıcıda **Fotoğraflar** sayfasını açıp operatör PIN'iyle giriş yapın.
2. Kontrol paneline dönüp isteğe bağlı **Oturum adı** girin.
3. **Ekran kotası bitince fotoğraf çek** seçeneğini açık bırakın.
4. Fotoğraf çekimi için gerekli veli/katılımcı onayını aldıktan sonra onay kutusunu işaretleyin.
5. Oyunu başlatın.

Her ekran kendi hırsız kotasını ilk kez tamamladığında skor isteğini bekletmeden kamera kuyruğuna tek çekim eklenir. Varsayılan 350 ms gecikme çocukların tepki anını yakalar. Kamera veya disk hatası oyunu durdurmaz; hata ilgili oturumun galeri kaydında görünür.

Oyun sonunda `/photos` sayfasından:

- Oturum adına veya müşteri adına göre arama yapılabilir.
- Fotoğraflar tek tek ya da ZIP olarak indirilebilir.
- **Yazdır** ile tarayıcının bağlı yazıcısına A4 yatay baskı hazırlanabilir.
- Müşteri adı, satış tutarı ve **Satıldı** durumu kaydedilebilir.
- Yanlış veya saklama süresi dolmuş oturumlar kalıcı olarak silinebilir. Aktif veya kamera kuyruğunda çekimi bulunan oturum silinemez.

Fotoğraflar varsayılan olarak `/home/pi/thief_server/photo_sessions/` altında oturum klasörlerinde saklanır. Bu klasör Git'e alınmaz. Satış ve saklama politikası uygulanırken fotoğraf onayı, erişim yetkisi ve silme süresi işletmenin geçerli kurallarına göre yönetilmelidir.

Kamera kontrolü:

```bash
v4l2-ctl --list-devices
fswebcam --device /dev/video0 --resolution 1920x1080 --no-banner test.jpg
sudo systemctl restart thief-server
```

Kamera farklı bir aygıtsa `thief_server/config.json` içindeki `camera.device` değiştirilir. Çözünürlük, JPEG kalitesi, ısınma frame sayısı ve tepki gecikmesi aynı `camera` bölümünden ayarlanabilir.
