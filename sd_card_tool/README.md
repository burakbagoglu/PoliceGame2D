# Polis Oyunu SD Kart Hazirlama

Windows bilgisayardan Pi Zero 2 W client SD kartlarini hazirlayan PySide6 aracidir.

Arac su islemleri yapar:

- Raspberry Pi OS Lite 32-bit imajini resmi Raspberry Pi Imager ile indirir/yazar ve dogrular.
- Yalniz USB, SD veya MMC olarak gorunen; Windows sistem/boot diski olmayan hedefleri listeler.
- Yazmadan once secilen fiziksel diski iki kez gosterir ve `SIL N` onayi ister.
- Wi-Fi, hostname, Linux kullanicisi, SSH, ekran numarasi, Pi 4 adresi ve Arduino portunu ayarlar.
- Client kaynaklarini SD imajindaki ilk acilis scriptine gomdugu icin proje kurulumu GitHub'a bagli degildir.
- Pi Zero ilk acilista paketleri kurar, `thief-game.service` servisini etkinlestirir ve yeniden baslar.
- Elektrik kesintisi veya crash sonrasinda systemd client'i otomatik acar.

## Gereksinimler

1. Windows 10 veya 11.
2. Python 3.11+ (`py` launcher ile).
3. Raspberry Pi Imager 2.0 veya yenisi: https://www.raspberrypi.com/software/
4. SD kart okuyucu ve Pi Zero'nun ilk acilista internete cikabilen 2.4 GHz Wi-Fi agi.

Client kaynak kodu karta gomuludur; ancak `python3-pygame`, serial ve diger sistem paketleri ilk
acilista Raspberry Pi OS depolarindan kuruldugu icin ilk kurulumda internet gerekir. Tamamen offline
kurulum istenirse gerekli paketlerin yuklu oldugu yerel bir "golden image" OS imaji alanindan secilebilir.

## Calistirma

`sd_card_tool/start_windows.bat` dosyasina cift tiklayin.

Ilk calistirmada proje kokunde `.venv` olusturulur ve PySide6 kurulur. Sonra Windows UAC penceresi
acilir; SD karta ham yazma yapabilmesi icin izin verin. Uygulama Wi-Fi veya Linux sifrelerini kalici
olarak kaydetmez.

## Alanlar

- **OS imaji:** Varsayilan resmi Raspberry Pi OS Lite 32-bit `latest` adresidir. Istenirse yerel
  `.img`, `.zip`, `.xz`, `.gz` veya `.zst` golden image secilebilir.
- **SD kart:** Yalniz guvenli cikarilabilir diskler. Kart takildiktan sonra `Yenile` kullanin.
- **Raspberry Pi Imager:** Otomatik bulunamazsa kurulu `rpi-imager.exe` veya `imager.exe` dosyasini secin.
- **Ekran numarasi:** 1-8. Hostname otomatik `polis-ekran-N` olur.
- **Pi 4 server:** IP/hostname; port yazilmazsa 8078 eklenir.
- **Arduino seri port/baud:** Varsayilan `/dev/ttyUSB0` ve 9600.
- **Render:** Pi Zero icin 1280x720, 30 FPS ve adaptif kalite onerilir.
- **Wi-Fi:** Pi Zero 2 W icin 2.4 GHz SSID ve parola.

## Ilk acilis

Kart Pi Zero'ya takildiktan sonra ilk acilis 5-15 dakika surebilir. Paket kurulumu tamamlaninca cihaz
yeniden baslar ve oyun otomatik acilir. Sorun olursa Pi'ye ekran/klavye veya SSH ile baglanip:

```bash
sudo cat /var/log/polisoyunu-firstboot.log
sudo systemctl status thief-game
sudo journalctl -u thief-game -n 100 --no-pager
```

Kurulum basariliysa `/var/lib/polisoyunu/provisioned.json` olusur.

## Guvenlik

Wi-Fi ve Linux sifresi ilk kurulum tamamlanana kadar boot bolumundeki `firstrun.sh` icinde gecici olarak bulunur; basarili kurulum sonunda script silinir. Kurulum hata verirse karti fiziksel olarak guvende tutun.

Uygulama `--enable-writing-system-drives` secenegini hic kullanmaz. Resmi Imager da hedefi yeniden
cikarilabilir disk listesinde arar ve sistem diskini reddeder. Yine de disk boyutu, model ve seri
numarasini kontrol etmeden onay vermeyin; yazma islemi hedef karttaki tum verileri geri donulemez
sekilde siler.