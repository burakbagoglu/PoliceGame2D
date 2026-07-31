# Sahne Editörü Kullanım Kılavuzu

Sahne editörü Pi 4 server üzerinde çalışır:

```text
http://SERVER_IP:8078/scene-editor
```

Dashboard üzerindeki **Sahne Editörü** düğmesi de aynı sayfayı açar.

## Temel Akış

1. Sol taraftan düzenlenecek sahneyi seçin.
2. Canvas üzerindeki öğeyi sürükleyin veya sağ alt köşesinden boyutlandırın.
3. Sağ panelden konum, boyut, renk, dönüş, katman ve efekt ayarlarını yapın.
4. **Taslağı kaydet** düğmesine basın.
5. Test edilecek client numarasını yazıp **Önizle** düğmesine basın.
6. **Görüntü al** ile o clientın gerçek Pygame çıktısını editörde kontrol edin.
7. Sonuç doğruysa **Yayınla** düğmesine basın.

Taslak önizlemesi yalnızca seçilen clientı etkiler. Üst çubuktaki **Kapat**
düğmesi clientı normal yayın akışına döndürür.

## Sahneler

- `waiting`: Oyun bekleme ekranı
- `intro`: HIRSIZLARI VUR başlangıcı
- `countdown`: 3, 2, 1 ve BAŞLA
- `gameplay`: Oyun üzerindeki skor ve diğer katmanlar
- `jail`: Yalnızca kendi hırsız kotasını dolduran ekranda gösterilen hapishane sahnesi
- `win`: Hedef tamamlandığında gösterilen kazanma sahnesi
- `lose`: Süre dolduğunda veya oyun hedeften önce bitirildiğinde gösterilen sahne

Kazanma ve kaybetme sahneleri varsayılan olarak 8 saniye gösterilir.
Bu süre server `config.json` dosyasındaki `result_scene_seconds` değeriyle
değiştirilebilir.

## Mevcut Oyun Arka Planı

Editör, clientlarda zaten bulunan `thief_client/assets/bg/bg.png` dosyasını otomatik
olarak önizler. Aynı arka planı tekrar yüklemeniz gerekmez. Yalnızca farklı bir
arka plan kullanmak istediğinizde yeni görseli asset olarak yükleyin.

## Öğeler

- **Yazı:** Normal ve dinamik metin
- **Kutu:** Renkli, kenarlıklı, yuvarlatılmış alan
- **Skor:** Server toplam skoruna bağlı gösterge
- **Konfeti:** Miktarı ayarlanabilir partikül katmanı
- **Sprite:** Asset alanına yüklenen PNG, JPG, WebP veya BMP

Bir asseti sahneye eklemek için yükledikten sonra sol paneldeki küçük görseline
tıklayın.

## Dinamik Metinler

Metinlerde aşağıdaki değişkenler kullanılabilir:

```text
{score}
{combo}
{countdown}
{target_score}
{screen_score}
{screen_target}
{screen_remaining}
{remaining_time}
{screen_id}
```

Örnek:

```text
SKOR: {score} / {target_score}
```

## Efektler

Desteklenen efektler:

- Pulse
- Büyüme
- Fade
- Shake
- Glow
- Yanıp sönme
- Süzülme

Efekt hızı sağ panelden değiştirilebilir. Konfeti ayrı bir öğe olarak eklenir ve
partikül miktarı yine sağ panelden ayarlanır.

## Klavye Kısayolları

- `Delete`: Seçili öğeyi sil
- `Ctrl+Z`: Geri al
- `Ctrl+Y`: İleri al
- `Ctrl+D`: Seçili öğeyi çoğalt
- `Shift` basılı sürükleme: 10 piksel snap yerine hassas taşıma

## Gerçek Client Görüntüsü

**Görüntü al** komutu sürekli yayın açmaz. Seçilen client sahneyi çizdikten sonra
oyun alanını bir kez en fazla 960×540 boyutuna küçültür ve PNG olarak servera
gönderir. Görsel sıkıştırma ve upload yalnızca istek geldiğinde çalışır; spawn ve
piezo polling ayrı threadlerde devam eder. Üstteki **Tasarım / Client görüntüsü**
düğmeleriyle editör çizimi ve gerçek çıktı arasında geçiş yapılabilir.

Client 18 saniye içinde yanıt vermezse editör zaman aşımı gösterir. Bu durumda
clientın açık, servera bağlı ve güncel kodu çalıştırıyor olduğunu kontrol edin.

## Responsive Sabitleme

Seçili öğenin sağ panelinde yatay ve dikey sabitleme ayarları bulunur:

- **Oranlı ölçekle:** Eski davranışı korur; konum ve boyut ekranla ölçeklenir.
- **Sola / sağa / üste / alta sabitle:** Öğenin ilgili kenar mesafesini korur.
- **Ortala:** Öğeyi ekran merkezine göre konumlandırır.
- **İki yana uzat:** İki kenar boşluğunu koruyup öğenin boyutunu esnetir.

Böylece farklı çözünürlük veya en-boy oranlarında skor, başlık ve tam ekran
katmanları doğru yerde tutulabilir. Eski sahneler otomatik olarak **Oranlı
ölçekle** moduna geçirilir; mevcut tasarım bozulmaz.
## Sahne Motoru v4

### Çoklu seçim, grup ve prefab

- Canvas veya katman listesinde `Shift` ile birden fazla öğe seçilir.
- **Grupla** seçili öğelere ortak grup kimliği verir; gruptaki bir öğeye normal
tıklamak bütün grubu seçer. `Alt` ile yalnızca tek öğe seçilebilir.
- **Ayır** grup bağını kaldırır.
- **Seçimden oluştur** seçili öğeleri yeniden kullanılabilir prefab olarak saklar.
Prefab listesindeki **Yükle** aynı bileşen grubunu geçerli sahneye ekler.

### Timeline ve keyframe

Sağ panelde timeline sürgüsü, oynatma ve keyframe kontrolleri bulunur. Öğeyi
istenen konuma getirin, zamanı seçin ve **Keyframe ekle** düğmesine basın.
Konum, boyut, dönüş ve opaklık kaydedilir; aradaki değerler clientta ease-out
interpolasyonuyla üretilir. Sahne süresi ve timeline döngüsü sahne panelindedir.

### Sahne geçişleri

Her sahne için `fade`, soldan/sağdan kayma veya `zoom` giriş geçişi ve geçiş
süresi seçilebilir. Geçişler Pygame renderer tarafından sahne ilk açıldığında
uygulanır.

### Olay ve koşullar

Özel bir sahneye skor, kalan süre, isabet, kombo, kazanma, kaybetme veya oyun
aktif koşulu bağlanabilir. Kurallar öncelik sırasıyla değerlendirilir ve eşleşen
ilk sahne clientta gösterilir. Taslak client önizlemesi kurallardan izole edilir.

### Merkezi ses timeline

WAV, OGG veya MP3 dosyası asset alanına yüklenebilir. Ses cue için dosya veya
hazır efekt, başlangıç saniyesi, ses seviyesi ve loop seçilir. Ses clienttan
değil, Pi 4 serverın 3.5 mm analog jakından çalar. Çok sayıda client aynı anda poll
etse bile her cue serverda tek kez tetiklenir; sahne değişince loop kanalları
kapatılır.

### Sprite-sheet

Bir sprite seçildiğinde sütun, satır, ilk/son frame, FPS ve loop ayarları görünür.
Frame kırpma clientta yapılır ve yüzey cache'i kullanılır. Pi Zero için büyük
sheet dosyalarını ve yüksek FPS değerlerini ölçülü kullanın.

### Grid, hizalama, kilit ve gizleme

- Grid görünürlüğü ve 5/10/20/40 piksel snap sahne üstündeki araçlardan ayarlanır.
- Sürüklerken canvas merkezi, kenarlar ve diğer öğeler için mavi hizalama çizgileri çıkar.
- `Shift` snap'i geçici kapatıp hassas taşıma yapar.
- Kilitli öğeler sürüklenemez veya toplu silinemez.
- Gizli öğeler editör canvasında ve gerçek client rendererında çizilmez.

### Özel sahneler ve performans bütçesi

Sahne listesindeki araçlarla yeni sahne oluşturulabilir, mevcut sahne
kopyalanabilir veya silinebilir. En az bir sahne her zaman korunur. Pi Zero
performans kartı görünür öğe, sprite, sprite-sheet, glow, konfeti ve keyframe
maliyetlerinden bir bütçe puanı çıkarır; sınır aşımlarında uyarı verir. Bu değer
tahmindir, son karar gerçek cihaz FPS ölçümüyle verilmelidir.
## Sürüm Geri Alma

Her yayın yeni bir sürüm oluşturur. Sağ paneldeki **Sürüm Geçmişi** alanından
eski sürüm taslağa alınabilir. Bu işlem ekranları hemen değiştirmez; geri alınan
taslağın ayrıca yayınlanması gerekir.

## Bağlantı ve Performans

Clientlar sahne sürümünü ayrı bir arka plan thread'inde kontrol eder. Asset
indirme işlemleri spawn ve piezo polling akışını durdurmaz. Dosyalar checksum ile
doğrulanıp `thief_client/scene_cache` klasöründe saklanır.

Server bağlantısı kesilirse client son indirdiği sahneyle çalışmaya devam eder.
Henüz hiçbir sahne belgesi alınmadıysa kod içindeki mevcut sabit tasarım yedek
olarak kullanılır.
## Otomatik kayıt ve elektrik kesintisi kurtarma

- Değişiklikler 1,4 saniye sonra sunucu taslağına otomatik kaydedilir.
- Aynı taslak iki sekmede açılırsa revizyon kontrolü eski sekmenin yeni çalışmayı ezmesini engeller.
- Tarayıcı ayrıca kaydedilmemiş belgeyi `localStorage` içinde tutar. Elektrik kesintisi veya sekme kapanması sonrasında sunucu revizyonu değişmemişse çalışma otomatik geri yüklenir.
- Yayınlamadan önce eksik asset ve Pi performans denetimi çalışır; uyarılar kullanıcıya gösterilir.

## Asset optimizasyonu

Yüklenen görseller sunucuda analiz edilir. 2048 pikselden büyük kenarlar küçültülür; PNG, JPEG ve WebP dosyaları kendi formatlarında optimize edilir. Asset listesi çözünürlük, dosya boyutu ve seslerde mümkün olduğunda süre bilgisini gösterir. Bu işlem için server `requirements.txt` içindeki Pillow bağımlılığı kurulmalıdır.

## İstemci telemetrisi ve piezo kalibrasyonu

Kontrol panelindeki **İstemci Sağlığı ve Piezo Kalibrasyonu** bölümü her ekranın FPS, RAM, CPU sıcaklığı, ağ/seri bağlantısı, aktif sahnesi ve kuyruk durumunu gösterir. Heartbeat 5 saniyede bir gönderilir; 15 saniye veri gelmeyen istemci çevrimdışı sayılır.

Canlı piezo grafiği için Arduino seri porttan `PIEZO:123` veya `RAW:123` biçiminde örnek göndermelidir. **Gürültüye göre eşik öner** düğmesi yalnızca threshold slider'ını değiştirir; ayar, **Uygula** düğmesine basılana kadar clientlara gönderilmez.

## Gelişmiş merkezi ses cue'ları

Ses timeline'ında yüklenen dosyanın dalga formu tarayıcıda gösterilir. Cue seçilerek başlangıç zamanı, ses seviyesi, fade-in/fade-out, maksimum çalma süresi, loop ve stereo pan değerleri güncellenebilir. Ses yine clientlardan değil Pi 4 serverın 3.5 mm analog jakından çıkar.
## Photoshop benzeri çalışma alanı

- **V / Seç:** Öğe seçer; öğeyi sürükleyerek taşır. Seçim kutusundaki 8 tutamaçtan boyutlandırılır, üstteki yuvarlak tutamaçtan döndürülür.
- `Shift` ile oran korunur; `Alt` ile merkezden boyutlandırılır. Çoklu seçim aynı transform kutusuyla birlikte ölçeklenebilir.
- **Boş alanda sürükleme:** Seçim dikdörtgeni oluşturur. Shift ile mevcut seçime ekler.
- **H / El:** Çalışma tuvalini sürükler.
- **Space + sürükleme veya orta mouse:** Aktif araçtan bağımsız olarak tuvali geçici taşır.
- **Mouse tekerleği:** İmlecin bulunduğu noktayı merkez alarak %10-%600 arasında zoom yapar.
- **Sığdır / Ctrl+0:** Bütün tuvali çalışma alanına sığdırır.
- **Ctrl+1:** %100 görünüm ve ortalama.
- **Ok tuşları:** Seçili öğeleri 1 px; Shift+Ok 10 px taşır.
- Zoom sırasında seçim çizgileri, resize tutamaçları ve akıllı hizalama çizgileri ekranda sabit kalınlıkta görünür.

## Sahne Motoru v6

### Gelişmiş transform ve yerleşim

- Tekli veya çoklu seçimde sekiz yönlü resize, döndürme ve ortak bounding box bulunur.
- **Hizala** menüsü sol/orta/sağ, üst/orta/alt hizalama ile yatay/dikey dağıtma yapar.
- Katman sırası listede sürükle-bırakla değiştirilebilir; sağ tık menüsünden kopyalama, öne/arkaya gönderme, kilitleme, gizleme ve silme yapılır.
- Üst ve sol cetvele tıklayarak kalıcı kılavuz eklenir. Kılavuzlar kilitlenebilir veya topluca temizlenebilir.

### Katman klasörleri ve kırpma maskesi

- **Klasör＋** seçili katmanları yeni klasöre alır. Klasör gizlenebilir, daraltılabilir veya silinebilir.
- Klasör adına çift tıklayarak ad, ortak opaklık ve blend modu düzenlenir.
- Klasördeki **M** düğmesi seçili öğenin dikdörtgen sınırını kırpma maskesi yapar. Aynı klasördeki diğer öğeler bu sınırın dışında çizilmez.
- Öğe bazında `normal`, `add`, `multiply` ve `screen` blend modları; gölge, parlaklık, kontrast, doygunluk ve blur filtreleri hem editör hem Pygame renderer tarafından uygulanır.

### Gelişmiş timeline

Timeline track görünümü bütün öğeleri ve keyframe noktalarını birlikte gösterir. Noktalar sürüklenerek zamanları değiştirilebilir. Seçili keyframe için linear/ease-in/ease-out/ease-in-out uygulanabilir; keyframe dizisi kopyalanabilir, başka öğeye yapıştırılabilir veya sahne süresine göre ters çevrilebilir.

### Oyun alanı araçları

- **Vuruş alanı**, piezonun hırsızı düşürebileceği gerçek oyun bölgesini tanımlar.
- **Hareket yolu**, hırsızın izleyeceği çok noktalı rotayı tanımlar. Yola çift tıklamak yeni nokta ekler; noktalar inspector içinden sayısal olarak da düzenlenebilir.
- Yayınlanan koordinatlar her clientın oynanabilir alanına ölçeklenir. Clientta hoparlör gerekmez; ses timelineı serverdaki Pi 4 analog jakından çalar.

### Simülasyon, asset kütüphanesi ve profiller

- Canlı simülasyonda skor, kombo, kalan süre, isabet, oyun aktif, kazanma ve kaybetme değerleri verilerek kuralın açacağı sahne bulunabilir.
- Assetler ada/etikete göre aranabilir, kullanılmayanlar filtrelenebilir, sağ tıkla etiketlenebilir ve doğrudan canvas üzerine sürüklenebilir.
- Oyun profilleri çocuk, ekran, süre ve zorluk ayarlarını tek preset altında saklar. Yayınlandıktan sonra kontrol panelindeki **Profil** alanından seçilebilir.

### Güvenli yayın ve yedek

- **Dışa aktar** taslağın JSON yedeğini indirir; **İçe aktar** yedeği yalnızca taslağa yükler.
- Yayın öncesinde eklenen, silinen ve değişen sahneler ile öğe sayısı farkı gösterilir.
- Asset denetimi, performans uyarıları, revizyon çakışma koruması, otomatik kayıt ve sürüm geçmişi çalışmaya devam eder.
## Sabit 8 ekran ve bağımsız kota

- Oyun oturumu her zaman sekiz ekranla açılır; çocuk sayısı az olsa da hiçbir ekran devreden çıkarılmaz.
- Her ekranın hedefi diğerlerinden bağımsızdır. Varsayılan hesap `çocuk × 6 × zorluk × süre/35` biçimindedir ve ekran başına en az 12 hırsız uygulanır.
- Ayarlar server `config.json` içindeki `hits_per_child_per_screen` ve `minimum_hits_per_screen` değerleriyle değiştirilebilir.
- Bir ekran hedefini doldurunca spawn kuyruğu temizlenir, yeni vuruş kabul edilmez ve o client `jail` sahnesine geçer. Kalan ekranlar süre veya kendi kotaları bitene kadar devam eder.
- Oyun ancak sekiz ekranın tamamı kotasını doldurursa kazanılmış sayılır; süre dolarsa tamamlanmayan ekranlar nedeniyle kaybetme sahnesi açılır.
- Kontrol panelindeki sekiz ekran kartı kendi `skor / hedef` değerini ve tamamlanma durumunu canlı gösterir.
- Editörde `screen_complete` olayıyla özel sahne kuralları tanımlanabilir; canlı simülasyondaki **Ekran tamamlandı** seçeneğiyle test edilir.
## Pi Zero 2 W istemci performans profili

Pi Zero 2 W clientlarda varsayılan profil `pi_zero_2w` olarak gelir. Fiziksel HDMI çıkışı 1920×1080 kalabilir; oyun içeride 1280×720 çizilir ve yalnızca son aşamada ekrana büyütülür. Bu, sahne düzeninin koordinatlarını değiştirmeden işlenen piksel sayısını yaklaşık yüzde 56 azaltır.

`thief_client/config.json` içindeki temel seçenekler:

- `performance_profile`: `pi_zero_2w`, `balanced` veya `high`.
- `render_width` / `render_height`: iç render çözünürlüğü. Pi Zero için 1280×720 önerilir.
- `adaptive_quality`: FPS, P95 kare süresi ve CPU sıcaklığına göre efekt kalitesini otomatik ayarlar.
- `min_fps`: kalite yöneticisinin korumaya çalıştığı alt FPS sınırı; Pi Zero profili için 24'tür.

Düşük kalite profili ağır blur ve gölge bulanıklığını kapatır, konfeti/parçacık sayılarını sınırlar ve sahne yüzeylerini 48 MB'lık LRU önbellekte tutar. Yük altında kalite kademeli olarak `low` → `minimal` düşer; sistem 30 saniye sağlıklı kaldığında tekrar yükselir.

Client, spawn durumu, piezo ayarı ve seyrek telemetriyi `/api/client/poll` üzerinden tek keep-alive isteğinde alır. Eski server sürümleriyle karşılaşırsa otomatik olarak ayrı endpointlere döner. Ağ kesintisindeki skor olayları RAM'de biriktirilip iki saniyelik gruplar halinde atomik yazıldığı için SD kart yazma yükü azalır; kapanışta bekleyen olaylar zorla diske alınır.

Kontrol panelindeki istemci kartları FPS yanında P95 kare süresini, aktif kalite kademesini, performans profilini ve gerçek iç render çözünürlüğünü gösterir. Pi Zero için P95 değeri sürekli 41,7 ms üstündeyse sahnedeki blur, büyük gölge, yüksek adetli konfeti ve çok büyük sprite'lar azaltılmalıdır.
