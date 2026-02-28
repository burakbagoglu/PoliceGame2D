"""
Hit Input modülü - Piezo sensörden GPIO üzerinden vuruş algılama
3 pinli dijital piezo modülü (VCC, GND, S) doğrudan Pi GPIO'ya bağlanır
Arduino'ya gerek yoktur.

Donanım Bağlantısı (Piezo Modül → Pi Zero 2 W):
  VCC → 3.3V (Pin 1)
  GND → GND  (Pin 6)
  S   → GPIO 17 (Pin 11)  [config ile değiştirilebilir]
"""
import threading
import queue
import time
from typing import Optional

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[UYARI] RPi.GPIO yüklü değil, simülasyon modunda çalışacak")


class HitInput:
    """Piezo sensörden GPIO ile hit algılayan sınıf"""
    
    def __init__(self, gpio_pin: int = 17, debounce_ms: int = 200, debug: bool = False):
        """
        Args:
            gpio_pin: Piezo sensörün bağlı olduğu GPIO pin numarası (BCM)
            debounce_ms: Debounce süresi (ms) - art arda vuruşları engeller
            debug: Debug modunda mı çalışacak
        """
        self.gpio_pin = gpio_pin
        self.debounce_ms = debounce_ms
        self.debug = debug
        
        self.hit_queue: queue.Queue = queue.Queue()
        self.running = False
        
        # Bağlantı durumu
        self.connected = False
        self.last_error: Optional[str] = None
        
        # Debounce için son hit zamanı
        self._last_hit_time: float = 0
    
    def start(self):
        """GPIO pin'ini yapılandır ve interrupt'ı başlat"""
        if not GPIO_AVAILABLE:
            print("[HitInput] RPi.GPIO kütüphanesi yok, simülasyon modunda")
            self.connected = False
            return
        
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            
            # Falling edge: sensör vuruş algıladığında HIGH→LOW geçişi
            # Rising edge: sensör vuruş algıladığında LOW→HIGH geçişi
            # Modüle göre ikisinden birini kullanabiliriz, BOTH ile her iki durumu da yakalarız
            GPIO.add_event_detect(
                self.gpio_pin,
                GPIO.RISING,
                callback=self._gpio_callback,
                bouncetime=self.debounce_ms
            )
            
            self.connected = True
            self.running = True
            self.last_error = None
            
            if self.debug:
                print(f"[HitInput] GPIO {self.gpio_pin} yapılandırıldı (debounce: {self.debounce_ms}ms)")
        
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            
            if self.debug:
                print(f"[HitInput] GPIO yapılandırma hatası: {e}")
    
    def stop(self):
        """GPIO temizliği yap"""
        self.running = False
        
        if GPIO_AVAILABLE:
            try:
                GPIO.remove_event_detect(self.gpio_pin)
                GPIO.cleanup(self.gpio_pin)
            except Exception:
                pass
        
        if self.debug:
            print("[HitInput] GPIO temizlendi")
    
    def _gpio_callback(self, channel):
        """
        GPIO interrupt callback (ayrı thread'de çalışır)
        Piezo modül vuruş algıladığında tetiklenir
        """
        if not self.running:
            return
        
        current_time = time.time()
        elapsed_ms = (current_time - self._last_hit_time) * 1000
        
        # Yazılımsal debounce (donanım bouncetime'a ek güvenlik)
        if elapsed_ms >= self.debounce_ms:
            self._last_hit_time = current_time
            self.hit_queue.put("HIT")
            
            if self.debug:
                print("[HitInput] HIT algılandı! (GPIO)")
    
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
        Debug modunda veya GPIO bağlantısı olmadığında kullanılır
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
