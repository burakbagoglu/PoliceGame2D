"""
Net Client modülü - Pi 5 server'a HTTP event gönderimi
Offline durumda event'leri yerel kuyruğa yazar
"""
import threading
import queue
import json
import time
import uuid
import os
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[UYARI] requests yüklü değil, network gönderimi devre dışı")


@dataclass
class ScoreEvent:
    """Skor eventi veri yapısı"""
    event_id: str
    screen_id: int
    points: int
    ts_ms: int
    
    @classmethod
    def create(cls, screen_id: int, points: int = 1) -> "ScoreEvent":
        """Yeni event oluştur"""
        return cls(
            event_id=str(uuid.uuid4()),
            screen_id=screen_id,
            points=points,
            ts_ms=int(time.time() * 1000)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Dict'e dönüştür"""
        return asdict(self)


class NetClient:
    """Pi 5 server'a event gönderen client"""
    
    def __init__(
        self,
        server_url: str,
        screen_id: int,
        queue_file: str = "event_queue.json",
        debug: bool = False
    ):
        """
        Args:
            server_url: Server endpoint URL (örn: http://192.168.1.10:8000/event)
            screen_id: Bu ekranın ID'si (1-5)
            queue_file: Offline event'ler için dosya yolu
            debug: Debug modu
        """
        self.server_url = server_url
        self.screen_id = screen_id
        self.queue_file = queue_file
        self.debug = debug
        
        # Gönderim kuyruğu
        self.send_queue: queue.Queue = queue.Queue()
        
        # Thread kontrolü
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        # Durum
        self.connected = False
        self.last_error: Optional[str] = None
        self.events_sent = 0
        self.events_failed = 0
        
        # Offline queue'yu yükle
        self._load_offline_queue()
    
    def start(self):
        """Gönderim thread'ini başlat"""
        if not REQUESTS_AVAILABLE:
            print("[NetClient] requests kütüphanesi yok, gönderim devre dışı")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()
        
        if self.debug:
            print(f"[NetClient] Thread başlatıldı: {self.server_url}")
    
    def stop(self):
        """Gönderim thread'ini durdur ve offline queue'yu kaydet"""
        self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        self._save_offline_queue()
        
        if self.debug:
            print("[NetClient] Thread durduruldu")
    
    def send_score(self, points: int = 1):
        """
        Skor eventi gönder
        
        Args:
            points: Kazanılan puan (varsayılan 1)
        """
        event = ScoreEvent.create(self.screen_id, points)
        self.send_queue.put(event)
        
        if self.debug:
            print(f"[NetClient] Event kuyruğa eklendi: {event.event_id[:8]}...")
    
    def _send_loop(self):
        """Gönderim döngüsü (ayrı thread'de çalışır)"""
        retry_delay = 1.0
        max_retry_delay = 30.0
        
        while self.running:
            try:
                # Queue'dan event al (1 saniye timeout)
                event = self.send_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            
            # Göndermeyi dene
            success = self._send_event(event)
            
            if success:
                self.events_sent += 1
                self.connected = True
                retry_delay = 1.0  # Reset delay
            else:
                self.events_failed += 1
                self.connected = False
                
                # Offline queue'ya ekle
                self._add_to_offline_queue(event)
                
                # Exponential backoff
                time.sleep(min(retry_delay, max_retry_delay))
                retry_delay *= 2
    
    def _send_event(self, event: ScoreEvent) -> bool:
        """
        Tek bir event'i gönder
        
        Returns:
            True: Başarılı
            False: Başarısız
        """
        try:
            response = requests.post(
                self.server_url,
                json=event.to_dict(),
                timeout=5.0,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code in (200, 201, 204):
                if self.debug:
                    print(f"[NetClient] Event gönderildi: {event.event_id[:8]}...")
                return True
            else:
                self.last_error = f"HTTP {response.status_code}"
                if self.debug:
                    print(f"[NetClient] Gönderim hatası: {self.last_error}")
                return False
        
        except requests.exceptions.RequestException as e:
            self.last_error = str(e)
            if self.debug:
                print(f"[NetClient] Bağlantı hatası: {e}")
            return False
    
    def _load_offline_queue(self):
        """Offline queue'yu dosyadan yükle"""
        if not os.path.exists(self.queue_file):
            return
        
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                events_data = json.load(f)
            
            for data in events_data:
                event = ScoreEvent(**data)
                self.send_queue.put(event)
            
            # Dosyayı temizle
            os.remove(self.queue_file)
            
            if self.debug:
                print(f"[NetClient] {len(events_data)} offline event yüklendi")
        
        except Exception as e:
            if self.debug:
                print(f"[NetClient] Offline queue yükleme hatası: {e}")
    
    def _save_offline_queue(self):
        """Kalan event'leri dosyaya kaydet"""
        events = []
        
        while not self.send_queue.empty():
            try:
                event = self.send_queue.get_nowait()
                events.append(event.to_dict())
            except queue.Empty:
                break
        
        if not events:
            return
        
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2)
            
            if self.debug:
                print(f"[NetClient] {len(events)} event dosyaya kaydedildi")
        
        except Exception as e:
            if self.debug:
                print(f"[NetClient] Offline queue kaydetme hatası: {e}")
    
    def _add_to_offline_queue(self, event: ScoreEvent):
        """Event'i offline queue dosyasına ekle"""
        events = []
        
        # Mevcut event'leri oku
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, "r", encoding="utf-8") as f:
                    events = json.load(f)
            except:
                pass
        
        # Yeni event'i ekle
        events.append(event.to_dict())
        
        # Kaydet
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2)
        except Exception as e:
            if self.debug:
                print(f"[NetClient] Offline queue ekleme hatası: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Client durumunu döndür"""
        return {
            "connected": self.connected,
            "events_sent": self.events_sent,
            "events_failed": self.events_failed,
            "queue_size": self.send_queue.qsize(),
            "last_error": self.last_error
        }
