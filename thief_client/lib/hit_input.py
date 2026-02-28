"""
Hit Input modülü - Arduino'dan serial port üzerinden "HIT" sinyali okuma
Thread + Queue modeli ile ana loop'u bloklamadan çalışır
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
    """Arduino'dan hit sinyali okuyan sınıf"""
    
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
        Arduino'dan "HIT\n" geldiğinde queue'ya ekler
        """
        reconnect_delay = 1.0  # Yeniden bağlanma bekleme süresi
        
        while self.running:
            # Bağlantı yoksa bağlan
            if not self.connected or not self.serial_conn or not self.serial_conn.is_open:
                if not self._connect():
                    time.sleep(reconnect_delay)
                    continue
            
            try:
                # Satır oku
                line = self.serial_conn.readline()
                
                if line:
                    # Decode et ve temizle
                    text = line.decode("utf-8", errors="ignore").strip()
                    
                    if text == "HIT":
                        self.hit_queue.put("HIT")
                        
                        if self.debug:
                            print("[HitInput] HIT algılandı!")
            
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
        """
        Test için hit simüle et
        Debug modunda veya serial bağlantısı olmadığında kullanılır
        """
        self.hit_queue.put("HIT")
        
        if self.debug:
            print("[HitInput] Simüle hit eklendi")


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
        """Başlatma (klavye için gerekli değil)"""
        if self.debug:
            print("[KeyboardHitInput] Başlatıldı (SPACE tuşu ile hit)")
    
    def stop(self):
        """Durdurma"""
        pass
    
    def process_event(self, event):
        """
        Pygame event'ini işle
        
        Args:
            event: pygame.event
        """
        import pygame
        
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE:
                self.hit_queue.put("HIT")
                
                if self.debug:
                    print("[KeyboardHitInput] SPACE - HIT!")
    
    def get_hit(self) -> bool:
        """Hit var mı kontrol et"""
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
