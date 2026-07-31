"""USB kamera çekimi ve kalıcı oyun oturumu fotoğraf arşivi."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import time
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageOps


CaptureBackend = Callable[[Path], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PhotoSessionManager:
    """Oturum metadata'sını saklar ve kamera çekimlerini tek işçide sıraya alır."""

    def __init__(
        self,
        base_dir: str | Path,
        camera_config: Optional[dict] = None,
        capture_backend: Optional[CaptureBackend] = None,
    ):
        defaults = {
            "enabled": True,
            "device": "/dev/video0",
            "command": "fswebcam",
            "width": 1920,
            "height": 1080,
            "jpeg_quality": 92,
            "warmup_frames": 5,
            "timeout_seconds": 12,
            "capture_delay_ms": 350,
        }
        self.config = {**defaults, **(camera_config or {})}
        self.base_dir = Path(base_dir).resolve()
        self.capture_backend = capture_backend
        self.current_session_id: Optional[str] = None
        self.last_error: Optional[str] = None
        self._lock = threading.RLock()
        self._capture_queue: queue.Queue = queue.Queue(maxsize=32)
        self._pending: set[tuple[str, int]] = set()
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._capturing = False

    def initialize(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="photo-capture-worker",
                daemon=True,
            )
            self._worker.start()

    def shutdown(self):
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            try:
                self._capture_queue.put(None, timeout=2.0)
            except queue.Full:
                pass
            self._worker.join(timeout=15.0)

    @staticmethod
    def _clean_name(value: str, fallback: str) -> str:
        cleaned = " ".join(str(value or "").strip().split())[:80]
        return cleaned or fallback

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        value = str(session_id or "")
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}_[a-f0-9]{8}", value):
            raise ValueError("Geçersiz oturum kimliği")
        return value

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / self._validate_session_id(session_id)

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _write_metadata(self, metadata: dict):
        session_dir = self._session_dir(metadata["id"])
        session_dir.mkdir(parents=True, exist_ok=True)
        destination = session_dir / "session.json"
        fd, temp_name = tempfile.mkstemp(
            prefix=".session_", suffix=".json", dir=str(session_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        except BaseException:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            raise

    def _read_metadata(self, session_id: str) -> dict:
        path = self._metadata_path(session_id)
        if not path.is_file():
            raise FileNotFoundError("Fotoğraf oturumu bulunamadı")
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("id") != session_id:
            raise ValueError("Oturum metadata'sı geçersiz")
        return data

    @staticmethod
    def _decorate(metadata: dict) -> dict:
        result = json.loads(json.dumps(metadata))
        session_id = result["id"]
        for photo in result.get("photos", []):
            filename = photo.get("filename", "")
            thumbnail = photo.get("thumbnail", "")
            photo["url"] = f"/api/photo-sessions/{session_id}/photos/{filename}"
            photo["download_url"] = photo["url"] + "?download=true"
            photo["thumbnail_url"] = (
                f"/api/photo-sessions/{session_id}/photos/{thumbnail}"
                if thumbnail
                else photo["url"]
            )
        result["download_url"] = f"/api/photo-sessions/{session_id}/download"
        return result

    def start_session(
        self,
        name: str = "",
        *,
        capture_enabled: bool = False,
        consent_confirmed: bool = False,
        child_count: int = 0,
        duration_minutes: int = 0,
        difficulty: str = "normal",
        screen_targets: Optional[dict] = None,
    ) -> dict:
        if capture_enabled and not consent_confirmed:
            raise ValueError("Fotoğraf çekimi için veli/katılımcı onayı doğrulanmalıdır")
        self.initialize()
        with self._lock:
            if self.current_session_id:
                try:
                    current = self._read_metadata(self.current_session_id)
                    if current.get("status") == "active":
                        self.end_session("restarted", completed=False)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                    pass
            now = datetime.now(timezone.utc)
            session_id = f"{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
            fallback = f"Oturum {now.astimezone().strftime('%d.%m.%Y %H:%M')}"
            metadata = {
                "id": session_id,
                "name": self._clean_name(name, fallback),
                "started_at": now.isoformat(timespec="seconds"),
                "ended_at": None,
                "status": "active",
                "end_reason": None,
                "completed": False,
                "capture_enabled": bool(capture_enabled),
                "consent_confirmed": bool(consent_confirmed),
                "child_count": max(0, int(child_count)),
                "duration_minutes": max(0, int(duration_minutes)),
                "difficulty": str(difficulty or "normal")[:20],
                "screen_targets": {
                    str(key): int(value) for key, value in (screen_targets or {}).items()
                },
                "photos": [],
                "capture_errors": [],
                "sold": False,
                "sale_price": None,
                "customer_name": "",
                "sale_updated_at": None,
            }
            self._write_metadata(metadata)
            self.current_session_id = session_id
            return self._decorate(metadata)

    def end_session(self, reason: str, completed: bool) -> Optional[dict]:
        with self._lock:
            if not self.current_session_id:
                return None
            try:
                metadata = self._read_metadata(self.current_session_id)
            except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                return None
            if metadata.get("status") == "active":
                metadata["status"] = "completed" if completed else "ended"
                metadata["ended_at"] = _utc_now()
                metadata["end_reason"] = str(reason or "manual")[:40]
                metadata["completed"] = bool(completed)
                self._write_metadata(metadata)
            return self._decorate(metadata)

    def get_current(self) -> Optional[dict]:
        with self._lock:
            if not self.current_session_id:
                return None
            try:
                return self._decorate(self._read_metadata(self.current_session_id))
            except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                return None

    def list_sessions(self) -> list[dict]:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        sessions = []
        for path in self.base_dir.glob("*/session.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self._validate_session_id(metadata.get("id", ""))
                sessions.append(self._decorate(metadata))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(sessions, key=lambda item: item.get("started_at", ""), reverse=True)

    def get_session(self, session_id: str) -> dict:
        with self._lock:
            return self._decorate(self._read_metadata(session_id))

    def capture_screen(self, screen_id: int) -> bool:
        """Ekran tamamlanma fotoğrafını sıraya al; skor API'sini bloklama."""
        screen_id = int(screen_id)
        with self._lock:
            session_id = self.current_session_id
            if not session_id or not 1 <= screen_id <= 8:
                return False
            try:
                metadata = self._read_metadata(session_id)
            except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                return False
            if not metadata.get("capture_enabled") or not metadata.get("consent_confirmed"):
                return False
            if any(int(item.get("screen_id", 0)) == screen_id for item in metadata.get("photos", [])):
                return False
            task = (session_id, screen_id)
            if task in self._pending:
                return False
            try:
                self._capture_queue.put_nowait(task)
            except queue.Full:
                self._record_error(session_id, screen_id, "Kamera kuyruğu dolu")
                return False
            self._pending.add(task)
            return True

    def _worker_loop(self):
        while True:
            try:
                task = self._capture_queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if task is None:
                self._capture_queue.task_done()
                break
            session_id, screen_id = task
            self._capturing = True
            try:
                self._capture_for_session(session_id, screen_id)
            except Exception as exc:
                self.last_error = str(exc)
                self._record_error(session_id, screen_id, str(exc))
            finally:
                self._capturing = False
                with self._lock:
                    self._pending.discard((session_id, screen_id))
                self._capture_queue.task_done()

    def _capture_for_session(self, session_id: str, screen_id: int):
        session_dir = self._session_dir(session_id)
        stamp = datetime.now().strftime("%H%M%S_%f")
        filename = f"screen_{screen_id}_{stamp}.jpg"
        thumbnail_name = f"thumb_{filename}"
        temporary = session_dir / f".{filename}.capture"
        destination = session_dir / filename
        try:
            delay_ms = max(0, min(3000, int(self.config.get("capture_delay_ms", 350))))
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            self._capture_image(temporary)
            if not temporary.is_file() or temporary.stat().st_size < 100:
                raise RuntimeError("Kamera geçerli bir JPEG üretmedi")
            os.replace(temporary, destination)
            self._build_thumbnail(destination, session_dir / thumbnail_name)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        with self._lock:
            metadata = self._read_metadata(session_id)
            if any(int(item.get("screen_id", 0)) == screen_id for item in metadata.get("photos", [])):
                destination.unlink(missing_ok=True)
                (session_dir / thumbnail_name).unlink(missing_ok=True)
                return
            metadata.setdefault("photos", []).append({
                "screen_id": screen_id,
                "filename": filename,
                "thumbnail": thumbnail_name,
                "captured_at": _utc_now(),
                "size_bytes": destination.stat().st_size,
            })
            metadata["photos"].sort(key=lambda item: int(item.get("screen_id", 0)))
            self._write_metadata(metadata)
            self.last_error = None

    def _capture_image(self, output_path: Path):
        if self.capture_backend:
            self.capture_backend(output_path)
            return
        if not self.config.get("enabled", True):
            raise RuntimeError("USB kamera yapılandırmada kapalı")
        command = str(self.config.get("command", "fswebcam"))
        executable = shutil.which(command)
        if not executable:
            raise RuntimeError(f"Kamera komutu bulunamadı: {command}")
        width = max(320, min(7680, int(self.config.get("width", 1920))))
        height = max(240, min(4320, int(self.config.get("height", 1080))))
        quality = max(50, min(100, int(self.config.get("jpeg_quality", 92))))
        skip = max(0, min(30, int(self.config.get("warmup_frames", 5))))
        process = subprocess.run(
            [
                executable,
                "--quiet",
                "--device", str(self.config.get("device", "/dev/video0")),
                "--resolution", f"{width}x{height}",
                "--jpeg", str(quality),
                "--skip", str(skip),
                "--no-banner",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=max(3.0, float(self.config.get("timeout_seconds", 12))),
            check=False,
        )
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "bilinmeyen hata").strip()
            raise RuntimeError(f"USB kamera çekimi başarısız: {detail[:240]}")

    @staticmethod
    def _build_thumbnail(source: Path, destination: Path):
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((640, 420), Image.Resampling.LANCZOS)
                image.save(destination, "JPEG", quality=84, optimize=True)
        except (OSError, ValueError):
            shutil.copy2(source, destination)

    def _record_error(self, session_id: str, screen_id: int, message: str):
        with self._lock:
            try:
                metadata = self._read_metadata(session_id)
                errors = metadata.setdefault("capture_errors", [])
                errors.append({
                    "screen_id": int(screen_id),
                    "at": _utc_now(),
                    "message": str(message)[:300],
                })
                metadata["capture_errors"] = errors[-30:]
                self._write_metadata(metadata)
            except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                pass

    def camera_status(self) -> dict:
        command = str(self.config.get("command", "fswebcam"))
        backend_ready = self.capture_backend is not None
        executable = shutil.which(command) if not backend_ready else "test-backend"
        device = str(self.config.get("device", "/dev/video0"))
        device_present = backend_ready or os.path.exists(device)
        enabled = bool(self.config.get("enabled", True))
        return {
            "enabled": enabled,
            "available": bool(enabled and executable and device_present),
            "device": device,
            "command": command,
            "command_available": bool(executable),
            "device_present": bool(device_present),
            "capturing": self._capturing,
            "queue_depth": self._capture_queue.qsize(),
            "last_error": self.last_error,
            "current_session": self.get_current(),
        }

    def capture_test(self) -> Path:
        self.initialize()
        test_dir = self.base_dir / "_camera_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        temporary = test_dir / ".latest.capture"
        destination = test_dir / "latest.jpg"
        try:
            delay_ms = max(0, min(3000, int(self.config.get("capture_delay_ms", 350))))
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            self._capture_image(temporary)
            if not temporary.is_file() or temporary.stat().st_size < 100:
                raise RuntimeError("Kamera geçerli bir JPEG üretmedi")
            os.replace(temporary, destination)
            self.last_error = None
            return destination
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def get_test_photo(self) -> Path:
        path = self.base_dir / "_camera_test" / "latest.jpg"
        if not path.is_file():
            raise FileNotFoundError("Henüz test fotoğrafı çekilmedi")
        return path

    def get_photo_path(self, session_id: str, filename: str) -> Path:
        if Path(filename).name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError("Geçersiz fotoğraf adı")
        metadata = self._read_metadata(session_id)
        allowed = {
            value
            for item in metadata.get("photos", [])
            for value in (item.get("filename"), item.get("thumbnail"))
            if value
        }
        if filename not in allowed:
            raise FileNotFoundError("Fotoğraf bulunamadı")
        path = self._session_dir(session_id) / filename
        if not path.is_file():
            raise FileNotFoundError("Fotoğraf dosyası bulunamadı")
        return path

    def build_download_zip(self, session_id: str) -> tuple[Path, str]:
        metadata = self._read_metadata(session_id)
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", metadata.get("name", "oturum")).strip("_")
        safe_name = safe_name[:50] or "oturum"
        fd, temp_name = tempfile.mkstemp(prefix="polisoyunu_", suffix=".zip")
        os.close(fd)
        zip_path = Path(temp_name)
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "oturum.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                )
                for photo in metadata.get("photos", []):
                    source = self.get_photo_path(session_id, photo["filename"])
                    archive.write(source, arcname=photo["filename"])
            return zip_path, f"{safe_name}_{session_id}.zip"
        except BaseException:
            zip_path.unlink(missing_ok=True)
            raise

    def update_sale(
        self,
        session_id: str,
        *,
        sold: bool,
        sale_price: Optional[float],
        customer_name: str,
    ) -> dict:
        if sale_price is not None and (sale_price < 0 or sale_price > 1_000_000):
            raise ValueError("Satış tutarı geçersiz")
        with self._lock:
            metadata = self._read_metadata(session_id)
            metadata["sold"] = bool(sold)
            metadata["sale_price"] = round(float(sale_price), 2) if sale_price is not None else None
            metadata["customer_name"] = self._clean_name(customer_name, "")
            metadata["sale_updated_at"] = _utc_now()
            self._write_metadata(metadata)
            return self._decorate(metadata)

    def delete_session(self, session_id: str):
        with self._lock:
            metadata = self._read_metadata(session_id)
            if metadata.get("status") == "active":
                raise ValueError("Aktif oyun oturumu silinemez")
            if any(task_session == session_id for task_session, _ in self._pending):
                raise ValueError("Oturum için bekleyen kamera çekimi tamamlanmadan silinemez")
            shutil.rmtree(self._session_dir(session_id))
            if self.current_session_id == session_id:
                self.current_session_id = None