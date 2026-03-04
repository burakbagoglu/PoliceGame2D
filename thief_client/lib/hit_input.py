"""
Hit Input modülü - Arduino'dan serial port üzerinden "HIT" sinyali okuma
Thread + Queue modeli ile ana loop'u bloklamadan çalışır.
Arduino'ya piezo threshold/refractory ayarları gönderebilir.
"""
import threading
import queue
import time
from typing import Optional

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("[UYARI] pyserial yüklü değil, simülasyon modunda çalışacak")


class HitInput:
    """Arduino'dan hit sinyali okuyan ve ayar gönderen sınıf"""

    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 9600, debug: bool = False):
        """
        Args:
            port: Serial port (örn: /dev/ttyUSB0, COM3)
            baud: Baud rate
            debug: Debug modunda mı çalışacak
        """
        self.port = port
        self.baud = baud
        self.debug = debug

        self.hit_queue: queue.Queue = queue.Queue()
        self.config_ack_queue: queue.Queue = queue.Queue()
        self.serial_conn: Optional[serial.Serial] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # Bağlantı durumu
        self.connected = False
        self.last_error: Optional[str] = None

    def start(self):
        """Serial okuma thread'ini başlat"""
        if not SERIAL_AVAILABLE:
            print("[HitInput] Serial kütüphanesi yok, simülasyon modunda")
            self.connected = False
            return

        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

        if self.debug:
            print(f"[HitInput] Thread başlatıldı: {self.port}")

    def stop(self):
        """Serial okuma thread'ini durdur"""
        self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()

        if self.debug:
            print("[HitInput] Thread durduruldu")

    def _connect(self) -> bool:
        """Serial bağlantısını kur"""
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=0.1  # 100ms timeout
            )
            self.connected = True
            self.last_error = None

            if self.debug:
                print(f"[HitInput] Bağlantı kuruldu: {self.port}")

            return True
        except serial.SerialException as e:
            self.connected = False
            self.last_error = str(e)

            if self.debug:
                print(f"[HitInput] Bağlantı hatası: {e}")

            return False

    def _read_loop(self):
        """
        Serial okuma döngüsü (ayrı thread'de çalışır)
        Arduino'dan gelen mesajları işler:
        - "HIT\n" → hit queue'ya ekle
        - "OK:T=X\n" / "OK:R=X\n" → config ack queue'ya ekle
        """
        reconnect_delay = 1.0

        while self.running:
            if not self.connected or not self.serial_conn or not self.serial_conn.is_open:
                if not self._connect():
                    time.sleep(reconnect_delay)
                    continue

            try:
                line = self.serial_conn.readline()

                if line:
                    text = line.decode("utf-8", errors="ignore").strip()

                    if text == "HIT":
                        self.hit_queue.put("HIT")
                        if self.debug:
                            print("[HitInput] HIT algılandı!")

                    elif text.startswith("OK:"):
                        self.config_ack_queue.put(text)
                        if self.debug:
                            print(f"[HitInput] Config ACK: {text}")

                    elif text.startswith("ERR:"):
                        if self.debug:
                            print(f"[HitInput] Arduino hata: {text}")

                    elif text.startswith("STATUS:"):
                        if self.debug:
                            print(f"[HitInput] Arduino durum: {text}")

                    elif text == "READY":
                        if self.debug:
                            print("[HitInput] Arduino hazır")

            except serial.SerialException as e:
                self.connected = False
                self.last_error = str(e)

                if self.debug:
                    print(f"[HitInput] Okuma hatası: {e}")

                time.sleep(reconnect_delay)

            except Exception as e:
                if self.debug:
                    print(f"[HitInput] Beklenmeyen hata: {e}")

    def get_hit(self) -> bool:
        """
        Queue'dan hit var mı kontrol et (non-blocking)

        Returns:
            True: Hit var
            False: Hit yok
        """
        try:
            self.hit_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def clear_queue(self):
        """Queue'yu temizle"""
        while not self.hit_queue.empty():
            try:
                self.hit_queue.get_nowait()
            except queue.Empty:
                break

    def simulate_hit(self):
        """Test için hit simüle et"""
        self.hit_queue.put("HIT")
        if self.debug:
            print("[HitInput] Simüle hit eklendi")

    def send_config(self, threshold: int, refractory_ms: int) -> bool:
        """
        Arduino'ya piezo ayarlarını gönder.

        Args:
            threshold: Algılama eşiği (0-1023)
            refractory_ms: Hit sonrası bekleme süresi (ms)

        Returns:
            True: Komutlar gönderildi (ACK beklenmez)
            False: Serial bağlantı yok
        """
        if not self.connected or not self.serial_conn or not self.serial_conn.is_open:
            if self.debug:
                print("[HitInput] Config gönderilemedi - bağlantı yok")
            return False

        try:
            # Threshold gönder
            cmd_t = f"T:{threshold}\n"
            self.serial_conn.write(cmd_t.encode("utf-8"))

            # Kısa bekleme (Arduino'nun işlemesi için)
            time.sleep(0.05)

            # Refractory gönder
            cmd_r = f"R:{refractory_ms}\n"
            self.serial_conn.write(cmd_r.encode("utf-8"))

            if self.debug:
                print(f"[HitInput] Config gönderildi: T={threshold}, R={refractory_ms}")

            return True

        except Exception as e:
            if self.debug:
                print(f"[HitInput] Config gönderim hatası: {e}")
            return False


class KeyboardHitInput:
    """
    Klavye ile hit simülasyonu (geliştirme/test için)
    Space tuşuna basılınca hit algılar
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.hit_queue: queue.Queue = queue.Queue()
        self.connected = True  # Her zaman "bağlı"

    def start(self):
        if self.debug:
            print("[KeyboardHitInput] Başlatıldı (SPACE tuşu ile hit)")

    def stop(self):
        pass

    def process_event(self, event):
        """Pygame event'ini işle"""
        import pygame

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE:
                self.hit_queue.put("HIT")
                if self.debug:
                    print("[KeyboardHitInput] SPACE - HIT!")

    def get_hit(self) -> bool:
        try:
            self.hit_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def clear_queue(self):
        while not self.hit_queue.empty():
            try:
                self.hit_queue.get_nowait()
            except queue.Empty:
                break

    def simulate_hit(self):
        self.hit_queue.put("HIT")

    def send_config(self, threshold: int, refractory_ms: int) -> bool:
        """Klavye modunda config gönderimi simülasyonu"""
        if self.debug:
            print(f"[KeyboardHitInput] Config simüle: T={threshold}, R={refractory_ms}")
        return True
