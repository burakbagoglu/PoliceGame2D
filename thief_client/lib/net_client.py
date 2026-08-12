"""
Net Client modülü - Pi 4 server'a HTTP event gönderimi + spawn/piezo polling
Offline durumda event'leri yerel kuyruğa yazar
"""
import threading
import queue
import json
import time
import uuid
import os
import tempfile
import hashlib
import base64
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
    """Pi 4 server'a event gönderen ve spawn/piezo polling yapan client"""

    def __init__(
        self,
        server_url: str,
        server_base_url: str,
        screen_id: int,
        poll_interval_ms: int = 500,
        queue_file: str = "event_queue.json",
        scene_cache_dir: Optional[str] = None,
        debug: bool = False,
        telemetry_provider=None,
        settings_revision: int = 0,
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
        self.scene_cache_dir = os.path.abspath(scene_cache_dir or "scene_cache")
        self.debug = debug
        self._offline_lock = threading.RLock()
        self._offline_event_ids = set()
        self._offline_events: Dict[str, dict] = {}
        self._offline_dirty = False
        self._last_offline_flush = 0.0
        self.offline_flush_interval_s = 2.0
        self._stop_event = threading.Event()
        self.telemetry_provider = telemetry_provider
        self.settings_revision = max(0, int(settings_revision or 0))
        self._started_at = time.monotonic()
        self._last_heartbeat = 0.0
        self.heartbeat_interval_s = 5.0
        self._combined_poll_supported: Optional[bool] = None
        self._send_http = requests.Session() if REQUESTS_AVAILABLE else None
        self._poll_http = requests.Session() if REQUESTS_AVAILABLE else None
        self._scene_http = requests.Session() if REQUESTS_AVAILABLE else None

        # Gönderim kuyruğu
        self.send_queue: queue.Queue = queue.Queue()

        # Spawn kuyruğu (server'dan gelen spawn komutları)
        self.spawn_queue: queue.Queue = queue.Queue(maxsize=1)

        # Piezo config kuyruğu (server'dan gelen ayar değişiklikleri)
        self.piezo_config_queue: queue.Queue = queue.Queue()

        # Dashboard'dan gelen sınırlı operasyon komutları
        self.command_queue: queue.Queue = queue.Queue(maxsize=2)

        # Dashboard'dan gelen kalıcı, allowlist ile sınırlı client ayarları
        self.remote_config_queue: queue.Queue = queue.Queue(maxsize=1)

        # Server skor reset bildirimi
        self.score_reset_queue: queue.Queue = queue.Queue()
        self.last_score_version: Optional[int] = None

        # Server sahne editörü yayınları
        self.scene_config_queue: queue.Queue = queue.Queue(maxsize=2)
        self.scene_screenshot_request_queue: queue.Queue = queue.Queue(maxsize=2)
        self.scene_screenshot_upload_queue: queue.Queue = queue.Queue(maxsize=2)
        self.scene_version = ""
        self.scene_preview = False
        self.scene_preview_scene: Optional[str] = None
        self._last_screenshot_token = ""
        self._last_scene_poll = 0.0

        # Thread kontrolü
        self.running = False
        self.send_thread: Optional[threading.Thread] = None
        self.poll_thread: Optional[threading.Thread] = None
        self.scene_thread: Optional[threading.Thread] = None

        # Durum
        self.connected = False
        self.last_error: Optional[str] = None
        self.events_sent = 0
        self.events_failed = 0
        self.spawns_received = 0
        self.server_game_active = False
        self.server_scene = "waiting"
        self.server_total_score = 0
        self.server_target_score = 0
        self.server_screen_score = 0
        self.server_screen_target = 0
        self.server_screen_remaining = 0
        self.server_screen_complete = False
        self.server_remaining_seconds = 0
        self.countdown_active = False
        self.countdown_message: Optional[str] = None
        self.countdown_remaining_ms = 0

        # Offline queue'yu yükle
        self._load_offline_queue()

    def set_telemetry_provider(self, provider):
        """Pygame ana nesnesinden kilitsiz, hızlı bir sağlık özeti sağlayan callback ata."""
        self.telemetry_provider = provider
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

        # Asset indirme spawn/piezo polling'i geciktirmesin.
        self.scene_thread = threading.Thread(target=self._scene_loop, daemon=True)
        self.scene_thread.start()

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
        if self.scene_thread and self.scene_thread.is_alive():
            self.scene_thread.join(timeout=12.0)

        self._save_offline_queue()
        for session in (self._send_http, self._poll_http, self._scene_http):
            if session is not None:
                session.close()

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

    def consume_command(self) -> Optional[Dict[str, Any]]:
        """Dashboard'dan gelen son operasyon komutunu ana threade aktar."""
        try:
            latest = self.command_queue.get_nowait()
            while True:
                try:
                    latest = self.command_queue.get_nowait()
                except queue.Empty:
                    return latest
        except queue.Empty:
            return None

    def consume_remote_config(self) -> Optional[Dict[str, Any]]:
        """Dashboard'dan gelen son kalıcı ayar belgesini ana threade aktar."""
        try:
            latest = self.remote_config_queue.get_nowait()
            while True:
                try:
                    latest = self.remote_config_queue.get_nowait()
                except queue.Empty:
                    return latest
        except queue.Empty:
            return None

    def consume_score_reset(self) -> bool:
        """Server'da skor sifirlandiysa True dondurur."""
        try:
            self.score_reset_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def consume_scene_config(self) -> Optional[Dict[str, Any]]:
        """Yeni yayınlanan veya bu ekrana özel önizleme sahnesini tüket."""
        try:
            latest = self.scene_config_queue.get_nowait()
            while True:
                try:
                    latest = self.scene_config_queue.get_nowait()
                except queue.Empty:
                    return latest
        except queue.Empty:
            return None

    def consume_scene_screenshot_request(self) -> Optional[str]:
        """Ana Pygame threadinin işleyeceği son ekran görüntüsü isteğini döndür."""
        try:
            latest = self.scene_screenshot_request_queue.get_nowait()
            while True:
                try:
                    latest = self.scene_screenshot_request_queue.get_nowait()
                except queue.Empty:
                    return latest
        except queue.Empty:
            return None

    def submit_scene_screenshot(self, request_token: str, png_content: bytes) -> bool:
        """Pygame threadinden gelen PNG'yi ağ threadine devret."""
        if not request_token or not png_content or len(png_content) > 4 * 1024 * 1024:
            return False
        if self.scene_screenshot_upload_queue.full():
            self._clear_queue(self.scene_screenshot_upload_queue)
        self.scene_screenshot_upload_queue.put((request_token, png_content, 0))
        return True
    # ============== Send Loop ==============

    def _send_loop(self):
        """Skor gönderim döngüsü"""
        retry_delay = 1.0
        max_retry_delay = 30.0

        while self.running:
            try:
                event = self.send_queue.get(timeout=1.0)
            except queue.Empty:
                self._flush_offline_events()
                continue

            success = self._send_event(event)

            if success:
                self.events_sent += 1
                self.connected = True
                self._remove_from_offline_queue(event.event_id)
                self._flush_offline_events()
                retry_delay = 1.0
            else:
                self.events_failed += 1
                self.connected = False
                self._add_to_offline_queue(event)
                self._flush_offline_events()
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
            response = self._send_http.post(
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
                # Yeni serverda spawn + piezo + seyrek heartbeat tek keep-alive çağrısıdır.
                if not self._poll_combined():
                    self._poll_spawn()
                    self._poll_piezo_config()
                    if time.monotonic() - self._last_heartbeat >= self.heartbeat_interval_s:
                        self._send_heartbeat()

            except Exception as e:
                if self.debug:
                    print(f"[NetClient] Poll hatası: {e}")

            if self._stop_event.wait(self.poll_interval_s):
                break

    def _scene_loop(self):
        """Sahne/asset güncellemelerini oyun polling'inden bağımsız tut."""
        while self.running:
            self._upload_scene_screenshot()
            self._poll_scene_config()
            self._last_scene_poll = time.monotonic()
            if self._stop_event.wait(2.0):
                break

    def _build_telemetry_payload(self) -> dict:
        payload = {
            "screen_id": self.screen_id,
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "scene_version": self.scene_version,
            "active_scene": self.server_scene,
            "network_connected": self.connected,
            "events_failed": self.events_failed,
            "queue_depth": self.send_queue.qsize(),
        }
        if self.telemetry_provider:
            try:
                extra = self.telemetry_provider()
                if isinstance(extra, dict):
                    payload.update(extra)
            except Exception as exc:
                if self.debug:
                    print(f"[NetClient] Telemetri sağlayıcı hatası: {exc}")
        return payload

    def _send_heartbeat(self):
        self._last_heartbeat = time.monotonic()
        try:
            self._poll_http.post(
                f"{self.server_base_url}/api/clients/heartbeat",
                json=self._build_telemetry_payload(),
                timeout=2.0,
            )
        except requests.exceptions.RequestException:
            pass

    def _poll_combined(self) -> bool:
        if self._combined_poll_supported is False:
            return False
        now = time.monotonic()
        include_telemetry = now - self._last_heartbeat >= self.heartbeat_interval_s
        telemetry = self._build_telemetry_payload() if include_telemetry else None
        try:
            response = self._poll_http.post(
                f"{self.server_base_url}/api/client/poll",
                json={
                    "screen_id": self.screen_id,
                    "telemetry": telemetry,
                    "settings_revision": self.settings_revision,
                },
                timeout=3.0,
            )
            if response.status_code in (404, 405):
                self._combined_poll_supported = False
                return False
            self._combined_poll_supported = True
            if response.status_code != 200:
                self.last_error = f"HTTP {response.status_code}"
                return True
            data = response.json()
            self._apply_spawn_payload(data.get("spawn_state", {}))
            self._apply_piezo_payload(data.get("piezo_config", {}))
            self._apply_command_payload(data.get("command"))
            self._apply_remote_config_payload(data.get("client_settings"))
            if include_telemetry:
                self._last_heartbeat = now
            return True
        except (requests.exceptions.RequestException, ValueError):
            return True

    def _apply_spawn_payload(self, data: dict):
        self.connected = True
        self.server_game_active = data.get("game_active", False)
        self.server_scene = str(data.get("active_scene", "waiting"))
        self.server_total_score = int(data.get("total_score", 0) or 0)
        self.server_target_score = int(data.get("target_score", 0) or 0)
        self.server_screen_score = int(data.get("screen_score", 0) or 0)
        self.server_screen_target = int(data.get("screen_target", 0) or 0)
        self.server_screen_remaining = int(data.get("screen_remaining", 0) or 0)
        self.server_screen_complete = bool(data.get("screen_complete", False))
        self.server_remaining_seconds = int(data.get("remaining_seconds", 0) or 0)
        self.countdown_active = bool(data.get("countdown_active", False))
        self.countdown_message = data.get("countdown_message")
        self.countdown_remaining_ms = int(data.get("countdown_remaining_ms", 0) or 0)
        if not self.server_game_active or self.server_screen_complete:
            self._clear_queue(self.spawn_queue)
            self.countdown_active = False
            self.countdown_message = None
            self.countdown_remaining_ms = 0
        score_version = data.get("score_version")
        if score_version is not None:
            if self.last_score_version is None:
                self.last_score_version = score_version
            elif score_version != self.last_score_version:
                self.last_score_version = score_version
                self.score_reset_queue.put(score_version)
        if data.get("spawn") and not self.server_screen_complete:
            try:
                self.spawn_queue.put_nowait(data)
                self.spawns_received += 1
            except queue.Full:
                pass

    def _poll_spawn(self):
        try:
            response = self._poll_http.get(
                f"{self.server_base_url}/spawn/poll?screen_id={self.screen_id}",
                timeout=3.0,
            )
            if response.status_code == 200:
                self._apply_spawn_payload(response.json())
        except (requests.exceptions.RequestException, ValueError):
            pass

    def _apply_command_payload(self, data):
        if not isinstance(data, dict):
            return
        command_type = str(data.get("type", "")).lower()
        if command_type not in {"restart", "update"}:
            return
        if self.command_queue.full():
            self._clear_queue(self.command_queue)
        self.command_queue.put({
            "type": command_type,
            "token": str(data.get("token", ""))[:64],
        })
        if self.debug:
            print(f"[NetClient] Operasyon komutu alındı: {command_type}")

    def _apply_remote_config_payload(self, data):
        if not isinstance(data, dict) or not data.get("changed"):
            return
        if not isinstance(data.get("settings"), dict):
            return
        try:
            revision = int(data.get("revision", 0))
        except (TypeError, ValueError):
            return
        if revision < 1:
            return
        if self.remote_config_queue.full():
            self._clear_queue(self.remote_config_queue)
        self.remote_config_queue.put({
            "changed": True,
            "revision": revision,
            "settings": data["settings"],
        })
        if self.debug:
            print(f"[NetClient] Client ayar revizyonu alındı: {revision}")

    def _apply_piezo_payload(self, data: dict):
        if not data.get("changed"):
            return
        config = {
            "threshold": data.get("threshold"),
            "refractory_ms": data.get("refractory_ms"),
        }
        self.piezo_config_queue.put(config)
        if self.debug:
            print(f"[NetClient] Piezo config güncellendi: {config}")

    def _poll_piezo_config(self):
        try:
            response = self._poll_http.get(
                f"{self.server_base_url}/api/piezo/config/poll?screen_id={self.screen_id}",
                timeout=3.0,
            )
            if response.status_code == 200:
                self._apply_piezo_payload(response.json())
        except (requests.exceptions.RequestException, ValueError):
            pass
    def _poll_scene_config(self):
        """Sahne sürümü değiştiyse belgeyi ve assetleri yerel cache'e indir."""
        try:
            url = (
                f"{self.server_base_url}/api/scenes/client"
                f"?screen_id={self.screen_id}&known_version={self.scene_version}"
            )
            response = self._scene_http.get(url, timeout=5.0)
            if response.status_code != 200:
                return

            data = response.json()
            screenshot_token = str(data.get("screenshot_request") or "")
            if screenshot_token and screenshot_token != self._last_screenshot_token:
                if self.scene_screenshot_request_queue.full():
                    self._clear_queue(self.scene_screenshot_request_queue)
                self.scene_screenshot_request_queue.put(screenshot_token)
                self._last_screenshot_token = screenshot_token
            if not data.get("changed"):
                return
            document = data.get("document")
            version = str(data.get("version", ""))
            if not isinstance(document, dict) or not version:
                return

            asset_paths, assets_complete = self._sync_scene_assets(
                data.get("assets", [])
            )
            payload = {
                "version": version,
                "document": document,
                "asset_paths": asset_paths,
                "preview": bool(data.get("preview", False)),
                "preview_scene": data.get("preview_scene"),
            }
            if self.scene_config_queue.full():
                self._clear_queue(self.scene_config_queue)
            self.scene_config_queue.put(payload)

            self.scene_preview = payload["preview"]
            self.scene_preview_scene = payload["preview_scene"]
            if assets_complete:
                self.scene_version = version

            if self.debug:
                mode = "ÖNİZLEME" if self.scene_preview else "YAYIN"
                print(f"[NetClient] Sahne {mode} sürümü alındı: {version}")
        except (requests.exceptions.RequestException, ValueError, OSError) as exc:
            if self.debug:
                print(f"[NetClient] Sahne polling hatası: {exc}")

    def _upload_scene_screenshot(self):
        """Talep üzerine alınan görüntüyü scene polling threadinde yükle."""
        try:
            request_token, content, attempts = self.scene_screenshot_upload_queue.get_nowait()
        except queue.Empty:
            return
        try:
            response = self._scene_http.post(
                f"{self.server_base_url}/api/scenes/screenshot/upload",
                json={
                    "screen_id": self.screen_id,
                    "request_token": request_token,
                    "data_base64": base64.b64encode(content).decode("ascii"),
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                if self.debug:
                    print("[NetClient] Client görüntüsü editöre gönderildi")
                return
        except requests.exceptions.RequestException:
            pass
        if attempts < 2 and self.running:
            self.scene_screenshot_upload_queue.put((request_token, content, attempts + 1))
    def _sync_scene_assets(self, manifest: list) -> tuple:
        """Manifest assetlerini checksum ile indir; (path map, tamlık) döndür."""
        os.makedirs(self.scene_cache_dir, exist_ok=True)
        paths = {}
        complete = True
        for asset in manifest:
            name = os.path.basename(str(asset.get("name", "")))
            expected = str(asset.get("sha256", ""))
            relative_url = str(asset.get("url", ""))
            if not name or not expected or not relative_url:
                complete = False
                continue
            target = os.path.join(self.scene_cache_dir, name)
            try:
                if os.path.isfile(target) and self._file_sha256(target) == expected:
                    paths[name] = target
                    continue

                asset_url = (
                    relative_url
                    if relative_url.startswith(("http://", "https://"))
                    else f"{self.server_base_url}{relative_url}"
                )
                response = self._scene_http.get(asset_url, timeout=10.0)
                if response.status_code != 200:
                    complete = False
                    continue
                content = response.content
                if len(content) > 10 * 1024 * 1024:
                    complete = False
                    continue
                digest = hashlib.sha256(content).hexdigest()
                if digest != expected:
                    complete = False
                    continue

                fd, temp_path = tempfile.mkstemp(
                    dir=self.scene_cache_dir,
                    prefix=".scene_asset_",
                    suffix=os.path.splitext(name)[1],
                )
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, target)
                except BaseException:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise
                paths[name] = target
            except (requests.exceptions.RequestException, OSError):
                complete = False
        return paths, complete

    @staticmethod
    def _file_sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)

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
                    self._offline_events[event.event_id] = event.to_dict()
                    self._offline_event_ids.add(event.event_id)
                    self.send_queue.put(event)
                self._offline_dirty = False
                self._last_offline_flush = time.monotonic()
                if self.debug and events_data:
                    print(f"[NetClient] {len(events_data)} offline event yüklendi")
            except Exception as exc:
                if self.debug:
                    print(f"[NetClient] Offline queue yükleme hatası: {exc}")

    def _flush_offline_events(self, force: bool = False):
        with self._offline_lock:
            if not self._offline_dirty:
                return
            now = time.monotonic()
            if not force and now - self._last_offline_flush < self.offline_flush_interval_s:
                return
            try:
                self._write_offline_events_unlocked(list(self._offline_events.values()))
                self._offline_dirty = False
                self._last_offline_flush = now
            except Exception as exc:
                if self.debug:
                    print(f"[NetClient] Offline queue flush hatası: {exc}")

    def _save_offline_queue(self):
        with self._offline_lock:
            while True:
                try:
                    event = self.send_queue.get_nowait()
                    self._offline_events[event.event_id] = event.to_dict()
                    self._offline_event_ids.add(event.event_id)
                    self._offline_dirty = True
                except queue.Empty:
                    break
        self._flush_offline_events(force=True)
        if self.debug and self._offline_events:
            print(f"[NetClient] {len(self._offline_events)} event dosyaya kaydedildi")

    def _add_to_offline_queue(self, event: ScoreEvent):
        with self._offline_lock:
            if event.event_id in self._offline_event_ids:
                return
            self._offline_events[event.event_id] = event.to_dict()
            self._offline_event_ids.add(event.event_id)
            self._offline_dirty = True

    def _remove_from_offline_queue(self, event_id: str):
        with self._offline_lock:
            if event_id not in self._offline_event_ids:
                return
            self._offline_events.pop(event_id, None)
            self._offline_event_ids.discard(event_id)
            self._offline_dirty = True
    # ============== Status ==============

    def get_countdown_status(self) -> Dict[str, Any]:
        """Sunucuyla senkron oyun başlangıç sayımını döndür."""
        return {
            "active": self.countdown_active,
            "message": self.countdown_message,
            "remaining_ms": self.countdown_remaining_ms,
        }

    def get_status(self) -> Dict[str, Any]:
        """Client durumunu döndür"""
        return {
            "connected": self.connected,
            "game_active": self.server_game_active,
            "active_scene": self.server_scene,
            "server_total_score": self.server_total_score,
"server_target_score": self.server_target_score,
            "server_screen_score": self.server_screen_score,
            "server_screen_target": self.server_screen_target,
            "server_screen_remaining": self.server_screen_remaining,
            "server_screen_complete": self.server_screen_complete,
            "server_remaining_seconds": self.server_remaining_seconds,
            "countdown_active": self.countdown_active,
            "countdown_message": self.countdown_message,
            "countdown_remaining_ms": self.countdown_remaining_ms,
            "scene_version": self.scene_version,
            "scene_preview": self.scene_preview,
            "scene_preview_scene": self.scene_preview_scene,
            "events_sent": self.events_sent,
            "events_failed": self.events_failed,
            "spawns_received": self.spawns_received,
            "score_version": self.last_score_version,
            "queue_size": self.send_queue.qsize(),
            "last_error": self.last_error,
        }
