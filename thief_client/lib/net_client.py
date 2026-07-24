"""
Net Client modülü - Pi 5 server'a HTTP event gönderimi + spawn/piezo polling
Offline durumda event'leri yerel kuyruğa yazar
"""
import threading
import queue
import json
import time
import uuid
import os
import tempfile
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
        return cls(
            event_id=str(uuid.uuid4()),
            screen_id=screen_id,
            points=points,
            ts_ms=int(time.time() * 1000),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NetClient:
    """Pi 5 server'a event gönderen ve spawn/piezo polling yapan client"""

    def __init__(
        self,
        server_url: str,
        server_base_url: str,
        screen_id: int,
        poll_interval_ms: int = 500,
        queue_file: str = "event_queue.json",
        debug: bool = False,
    ):
        """
        Args:
            server_url: Server event endpoint URL (örn: http://192.168.1.10:8078/event)
            server_base_url: Server base URL (örn: http://192.168.1.10:8078)
            screen_id: Bu ekranın ID'si
            poll_interval_ms: Spawn polling aralığı (ms)
            queue_file: Offline event'ler için dosya yolu
            debug: Debug modu
        """
        self.server_url = server_url
        self.server_base_url = server_base_url.rstrip("/")
        self.screen_id = screen_id
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.queue_file = queue_file
        self.debug = debug
        self._offline_lock = threading.RLock()
        self._offline_event_ids = set()
        self._stop_event = threading.Event()

        # Gönderim kuyruğu
        self.send_queue: queue.Queue = queue.Queue()

        # Spawn kuyruğu (server'dan gelen spawn komutları)
        self.spawn_queue: queue.Queue = queue.Queue()

        # Piezo config kuyruğu (server'dan gelen ayar değişiklikleri)
        self.piezo_config_queue: queue.Queue = queue.Queue()

        # Server skor reset bildirimi
        self.score_reset_queue: queue.Queue = queue.Queue()
        self.last_score_version: Optional[int] = None

        # Thread kontrolü
        self.running = False
        self.send_thread: Optional[threading.Thread] = None
        self.poll_thread: Optional[threading.Thread] = None

        # Durum
        self.connected = False
        self.last_error: Optional[str] = None
        self.events_sent = 0
        self.events_failed = 0
        self.spawns_received = 0
        self.server_game_active = False

        # Offline queue'yu yükle
        self._load_offline_queue()

    def start(self):
        """Thread'leri başlat"""
        if not REQUESTS_AVAILABLE:
            print("[NetClient] requests kütüphanesi yok, gönderim devre dışı")
            return

        self.running = True
        self._stop_event.clear()

        # Skor gönderim thread'i
        self.send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self.send_thread.start()

        # Spawn + piezo polling thread'i
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()

        if self.debug:
            print(f"[NetClient] Thread'ler başlatıldı: {self.server_base_url}")

    def stop(self):
        """Thread'leri durdur ve offline queue'yu kaydet"""
        self.running = False
        self._stop_event.set()

        if self.send_thread and self.send_thread.is_alive():
            self.send_thread.join(timeout=6.0)
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=2.0)

        self._save_offline_queue()

        if self.debug:
            print("[NetClient] Thread'ler durduruldu")

    def send_score(self, points: int = 1):
        """Skor eventi kuyruğa ekle"""
        event = ScoreEvent.create(self.screen_id, points)
        self.send_queue.put(event)

        if self.debug:
            print(f"[NetClient] Event kuyruğa eklendi: {event.event_id[:8]}...")

    def get_spawn(self) -> bool:
        """
        Spawn kuyruğundan spawn komutu var mı kontrol et (non-blocking)

        Returns:
            True: Spawn var
            False: Spawn yok
        """
        try:
            self.spawn_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def get_piezo_config(self) -> Optional[Dict]:
        """
        Piezo config kuyruğundan yeni ayar var mı kontrol et (non-blocking)

        Returns:
            dict: Yeni ayarlar {"threshold": X, "refractory_ms": Y} veya None
        """
        try:
            return self.piezo_config_queue.get_nowait()
        except queue.Empty:
            return None

    def consume_score_reset(self) -> bool:
        """Server'da skor sifirlandiysa True dondurur."""
        try:
            self.score_reset_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    # ============== Send Loop ==============

    def _send_loop(self):
        """Skor gönderim döngüsü"""
        retry_delay = 1.0
        max_retry_delay = 30.0

        while self.running:
            try:
                event = self.send_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            success = self._send_event(event)

            if success:
                self.events_sent += 1
                self.connected = True
                self._remove_from_offline_queue(event.event_id)
                retry_delay = 1.0
            else:
                self.events_failed += 1
                self.connected = False
                self._add_to_offline_queue(event)
                interrupted = self._stop_event.wait(
                    min(retry_delay, max_retry_delay)
                )
                if not interrupted:
                    # Aynı event_id ile çalışma sırasında yeniden dene.
                    # Server tarafındaki idempotency olası tekrarları güvenli kılar.
                    self.send_queue.put(event)
                retry_delay = min(retry_delay * 2, max_retry_delay)

    def _send_event(self, event: ScoreEvent) -> bool:
        """Tek bir event'i gönder"""
        try:
            response = requests.post(
                self.server_url,
                json=event.to_dict(),
                timeout=5.0,
                headers={"Content-Type": "application/json"},
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

    # ============== Poll Loop ==============

    def _poll_loop(self):
        """Spawn + piezo config polling döngüsü"""
        while self.running:
            try:
                # Spawn polling
                self._poll_spawn()

                # Piezo config polling
                self._poll_piezo_config()

            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Poll hatası: {e}")

            if self._stop_event.wait(self.poll_interval_s):
                break

    def _poll_spawn(self):
        """Server'dan spawn komutu sorgula"""
        try:
            url = f"{self.server_base_url}/spawn/poll?screen_id={self.screen_id}"
            response = requests.get(url, timeout=3.0)

            if response.status_code == 200:
                data = response.json()
                self.connected = True
                self.server_game_active = data.get("game_active", False)
                if not self.server_game_active:
                    self._clear_queue(self.spawn_queue)
                score_version = data.get("score_version")
                if score_version is not None:
                    if self.last_score_version is None:
                        self.last_score_version = score_version
                    elif score_version != self.last_score_version:
                        self.last_score_version = score_version
                        self.score_reset_queue.put(score_version)

                if data.get("spawn"):
                    self.spawn_queue.put(data)
                    self.spawns_received += 1
                    if self.debug:
                        print(f"[NetClient] Spawn komutu alındı! (#{self.spawns_received})")

        except requests.exceptions.RequestException:
            pass  # Sessizce devam et

    def _poll_piezo_config(self):
        """Server'dan piezo config değişikliği sorgula"""
        try:
            url = f"{self.server_base_url}/api/piezo/config/poll?screen_id={self.screen_id}"
            response = requests.get(url, timeout=3.0)

            if response.status_code == 200:
                data = response.json()

                if data.get("changed"):
                    config = {
                        "threshold": data.get("threshold"),
                        "refractory_ms": data.get("refractory_ms"),
                    }
                    self.piezo_config_queue.put(config)
                    if self.debug:
                        print(f"[NetClient] Piezo config güncellendi: {config}")

        except requests.exceptions.RequestException:
            pass  # Sessizce devam et

    # ============== Offline Queue ==============

    @staticmethod
    def _clear_queue(target_queue: queue.Queue):
        while True:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                return

    def _read_offline_events_unlocked(self) -> list:
        if not os.path.exists(self.queue_file):
            return []

        with open(self.queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Offline queue JSON list olmalı")
        return data

    def _write_offline_events_unlocked(self, events: list):
        # event_id bazında sıralamayı koruyarak duplicate kayıtları temizle.
        unique_events = {}
        for event in events:
            event_id = event.get("event_id")
            if event_id:
                unique_events[event_id] = event
        events = list(unique_events.values())

        if not events:
            if os.path.exists(self.queue_file):
                os.remove(self.queue_file)
            self._offline_event_ids.clear()
            return

        directory = os.path.dirname(os.path.abspath(self.queue_file))
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=directory,
            prefix=".event_queue_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.queue_file)
        except BaseException:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        self._offline_event_ids = set(unique_events)

    def _load_offline_queue(self):
        with self._offline_lock:
            try:
                events_data = self._read_offline_events_unlocked()
                for data in events_data:
                    event = ScoreEvent(**data)
                    if event.event_id in self._offline_event_ids:
                        continue
                    self._offline_event_ids.add(event.event_id)
                    self.send_queue.put(event)

                if self.debug and events_data:
                    print(f"[NetClient] {len(events_data)} offline event yüklendi")

            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Offline queue yükleme hatası: {e}")

    def _save_offline_queue(self):
        with self._offline_lock:
            try:
                events = self._read_offline_events_unlocked()
                while True:
                    try:
                        event = self.send_queue.get_nowait()
                        events.append(event.to_dict())
                    except queue.Empty:
                        break

                self._write_offline_events_unlocked(events)

                if self.debug and events:
                    print(f"[NetClient] {len(events)} event dosyaya kaydedildi")

            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Offline queue kaydetme hatası: {e}")

    def _add_to_offline_queue(self, event: ScoreEvent):
        with self._offline_lock:
            if event.event_id in self._offline_event_ids:
                return
            try:
                events = self._read_offline_events_unlocked()
                events.append(event.to_dict())
                self._write_offline_events_unlocked(events)
            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Offline queue ekleme hatası: {e}")

    def _remove_from_offline_queue(self, event_id: str):
        with self._offline_lock:
            if event_id not in self._offline_event_ids:
                return
            try:
                events = [
                    event
                    for event in self._read_offline_events_unlocked()
                    if event.get("event_id") != event_id
                ]
                self._write_offline_events_unlocked(events)
            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Offline queue temizleme hatası: {e}")

    # ============== Status ==============

    def get_status(self) -> Dict[str, Any]:
        """Client durumunu döndür"""
        return {
            "connected": self.connected,
            "game_active": self.server_game_active,
            "events_sent": self.events_sent,
            "events_failed": self.events_failed,
            "spawns_received": self.spawns_received,
            "score_version": self.last_score_version,
            "queue_size": self.send_queue.qsize(),
            "last_error": self.last_error,
        }
