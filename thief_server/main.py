#!/usr/bin/env python3
"""
Thief Server - Raspberry Pi 4 için skor toplama + spawn kontrol sunucusu
Tüm client'lardan gelen skorları toplar, spawn zamanlamasını yönetir ve dashboard sunar
"""
import json
import os
import time
import threading
import subprocess
import base64
import binascii
from datetime import datetime
from typing import Any, Dict, Set, Optional, Literal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from audio_manager import AudioManager
from scene_manager import (
    SceneManager, SceneRevisionConflict, SceneValidationError,
)
from scene_audio_runtime import SceneAudioRuntime
from client_telemetry import ClientTelemetryStore
from client_commands import ClientCommandStore
from runtime_state import RuntimeStateStore
from photo_manager import PhotoSessionManager
from photo_auth import PhotoAccessGuard
from spawn_engine import (
    TargetCalculator,
    ScreenSelector,
    AdaptiveSpawnController,
    PhaseBasedSpawner,
    PiezoConfigManager,
    SpawnScheduler,
    GameSession,
)


# ============== Config ==============

def load_config(filepath: str = "config.json") -> dict:
    """Config dosyasını yükle"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.environ.get("THIEF_SERVER_CONFIG") or os.path.join(script_dir, filepath)

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {
            "host": "0.0.0.0",
            "port": 8078,
            "num_screens": 8,
            "game_duration_minutes": 35,
            "base_score_per_child": 15,
            "hits_per_child_per_screen": 6,
            "minimum_hits_per_screen": 12,
            "base_spawn_interval": 3.0,
            "min_spawn_interval": 0.5,
            "max_spawn_interval": 8.0,
            "max_concurrent_spawns": 3,
            "countdown_seconds": 4,
            "result_scene_seconds": 8,
            "client_offline_after_seconds": 15,
            "default_piezo_threshold": 100,
            "default_piezo_refractory_ms": 200,
            "audio": {
                "enabled": True,
                "device_name": "auto-analog",
                "frequency": 44100,
                "output_channels": 2,
                "buffer": 512,
                "mixer_channels": 8,
                "master_volume": 0.8,
                "music_volume": 0.35,
                "sfx_volume": 0.9,
                "music_file": "",
                "hit_sound_file": "",
                "start_sound_file": "",
                "success_sound_file": "",
                "end_sound_file": "",
                "countdown_sound_file": "",
                "go_sound_file": "",
            },
            "debug": False,
            "access_log": False,
        }

    config["host"] = os.environ.get("THIEF_HOST", config.get("host", "0.0.0.0"))
    config["port"] = int(os.environ.get("THIEF_PORT", config.get("port", 8078)))
    config["debug"] = os.environ.get("THIEF_DEBUG", str(config.get("debug", False))).lower() in ("1", "true", "yes")
    config["access_log"] = os.environ.get("THIEF_ACCESS_LOG", str(config.get("access_log", False))).lower() in ("1", "true", "yes")
    return config


CONFIG = load_config()
GAME_SCREEN_COUNT = 8
CONFIG["num_screens"] = GAME_SCREEN_COUNT


# ============== Models ==============

class ScoreEvent(BaseModel):
    """Client'tan gelen skor eventi"""
    event_id: str = Field(min_length=1, max_length=128)
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)
    points: int = Field(ge=1, le=100)
    ts_ms: int = Field(gt=0)


class ScoreResponse(BaseModel):
    """Skor sorgulama yanıtı"""
    total_score: int
    screen_scores: Dict[int, int]
    event_count: int
    last_event_time: Optional[str]
    score_version: int


class StartGameRequest(BaseModel):
    """Oyun başlatma isteği; alanlar seçilen profilin üzerine yazabilir."""
    profile_id: Optional[str] = Field(default=None, min_length=1, max_length=40)
    child_count: Optional[int] = Field(default=None, ge=1, le=100)
    screen_count: Optional[int] = Field(
        default=None,
        ge=1,
        le=GAME_SCREEN_COUNT,
    )
    difficulty: Optional[Literal["easy", "normal", "hard"]] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=120)
    session_name: str = Field(default="", max_length=80)
    capture_photos: bool = False
    photo_consent: bool = False


class PiezoConfigRequest(BaseModel):
    """Piezo ayar isteği"""
    threshold: int
    refractory_ms: int


class AudioConfigRequest(BaseModel):
    """Server hoparlör ses ayarları."""
    enabled: bool = True
    master_volume: float = Field(ge=0.0, le=1.0)
    music_volume: float = Field(ge=0.0, le=1.0)
    sfx_volume: float = Field(ge=0.0, le=1.0)


class AudioTestRequest(BaseModel):
    """Dashboard ses test komutu."""
    sound_type: Literal["hit", "start", "success", "end", "go", "music"]


class SceneDraftRequest(BaseModel):
    document: Dict[str, Any]
    base_revision: Optional[int] = Field(default=None, ge=1)


class ScenePreviewRequest(BaseModel):
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)
    scene_id: str = Field(min_length=1, max_length=40)


class SceneAssetRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    data_base64: str = Field(min_length=1, max_length=22_000_000)
    optimize: bool = True


class ClientTelemetryRequest(BaseModel):
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)
    fps: float = Field(default=0, ge=0, le=240)
    memory_mb: float = Field(default=0, ge=0)
    cpu_temp_c: Optional[float] = None
    uptime_seconds: int = Field(default=0, ge=0)
    scene_version: str = Field(default="", max_length=80)
    active_scene: str = Field(default="", max_length=40)
    network_connected: bool = False
    serial_connected: bool = False
    events_failed: int = Field(default=0, ge=0)
    queue_depth: int = Field(default=0, ge=0)
    app_version: str = Field(default="", max_length=40)
    update_state: str = Field(default="idle", max_length=24)
    update_version: str = Field(default="", max_length=40)
    update_error: str = Field(default="", max_length=240)
    frame_time_p95_ms: float = Field(default=0, ge=0, le=1000)
    draw_time_p95_ms: float = Field(default=0, ge=0, le=1000)
    blit_time_p95_ms: float = Field(default=0, ge=0, le=1000)
    flip_time_p95_ms: float = Field(default=0, ge=0, le=1000)
    performance_profile: str = Field(default="", max_length=32)
    quality_level: str = Field(default="", max_length=16)
    render_width: int = Field(default=0, ge=0, le=7680)
    render_height: int = Field(default=0, ge=0, le=4320)
    output_width: int = Field(default=0, ge=0, le=7680)
    output_height: int = Field(default=0, ge=0, le=4320)
    direct_render: bool = False
    render_mode: str = Field(default="full-render", max_length=24)
    updated_pixel_ratio: float = Field(default=100, ge=0, le=100)
    dirty_rect_count: int = Field(default=0, ge=0, le=128)
    piezo: Dict[str, Any] = Field(default_factory=dict)


class ClientPollRequest(BaseModel):
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)
    telemetry: Optional[Dict[str, Any]] = None


class PhotoLoginRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=128)


class PhotoSaleUpdateRequest(BaseModel):
    sold: bool = False
    sale_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    customer_name: str = Field(default="", max_length=80)


class PhotoCleanupRequest(BaseModel):
    dry_run: bool = True


class SceneScreenshotRequest(BaseModel):
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)


class SceneScreenshotUpload(BaseModel):
    screen_id: int = Field(ge=1, le=GAME_SCREEN_COUNT)
    request_token: str = Field(min_length=8, max_length=128)
    data_base64: str = Field(min_length=1, max_length=6_000_000)


# ============== Score Manager ==============

class ScoreManager:
    """Skor yöneticisi - idempotent event işleme"""

    def __init__(self, num_screens: int = GAME_SCREEN_COUNT):
        self._lock = threading.RLock()
        self.num_screens = num_screens
        self.screen_scores: Dict[int, int] = {i: 0 for i in range(1, num_screens + 1)}
        self.total_score: int = 0
        self.processed_events: Set[str] = set()
        self.event_count: int = 0
        self.last_event_time: Optional[datetime] = None
        self.event_history: list = []
        self.max_history = 100
        self.score_version: int = 0

    def process_event(self, event: ScoreEvent) -> bool:
        """Event'i işle. True: yeni, False: duplicate"""
        with self._lock:
            if event.event_id in self.processed_events:
                return False

            if not 1 <= event.screen_id <= self.num_screens:
                raise ValueError(f"Geçersiz ekran kimliği: {event.screen_id}")

            self.processed_events.add(event.event_id)

            screen_id = event.screen_id
            self.screen_scores[screen_id] = self.screen_scores.get(screen_id, 0) + event.points
            self.total_score += event.points

            self.event_count += 1
            self.last_event_time = datetime.now()

            self.event_history.append({
                "event_id": event.event_id[:8] + "...",
                "screen_id": event.screen_id,
                "points": event.points,
                "time": self.last_event_time.strftime("%H:%M:%S"),
            })

            if len(self.event_history) > self.max_history:
                self.event_history.pop(0)

            return True

    def get_scores(self) -> ScoreResponse:
        with self._lock:
            return ScoreResponse(
                total_score=self.total_score,
                screen_scores=self.screen_scores.copy(),
                event_count=self.event_count,
                last_event_time=(
                    self.last_event_time.strftime("%Y-%m-%d %H:%M:%S")
                    if self.last_event_time else None
                ),
                score_version=self.score_version,
            )

    def get_screen_score(self, screen_id: int) -> int:
        with self._lock:
            return self.screen_scores[screen_id]

    def get_history(self) -> list:
        with self._lock:
            return list(self.event_history)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "screen_scores": dict(self.screen_scores),
                "total_score": self.total_score,
                "processed_events": list(self.processed_events),
                "event_count": self.event_count,
                "event_history": list(self.event_history),
                "score_version": self.score_version,
                "last_event_time": (
                    self.last_event_time.isoformat()
                    if self.last_event_time else None
                ),
            }

    def restore(self, payload: dict):
        with self._lock:
            raw_scores = payload.get("screen_scores", {})
            self.screen_scores = {
                screen_id: max(
                    0,
                    int(raw_scores.get(str(screen_id), raw_scores.get(screen_id, 0))),
                )
                for screen_id in range(1, self.num_screens + 1)
            }
            self.total_score = sum(self.screen_scores.values())
            self.processed_events = {
                str(item) for item in payload.get("processed_events", [])
                if item
            }
            self.event_count = max(
                len(self.processed_events),
                int(payload.get("event_count", 0) or 0),
            )
            self.event_history = list(payload.get("event_history", []))[-self.max_history:]
            self.score_version = max(0, int(payload.get("score_version", 0) or 0))
            raw_time = payload.get("last_event_time")
            try:
                self.last_event_time = datetime.fromisoformat(raw_time) if raw_time else None
            except (TypeError, ValueError):
                self.last_event_time = None

    def reset(self):
        with self._lock:
            self.screen_scores = {i: 0 for i in range(1, self.num_screens + 1)}
            self.total_score = 0
            self.processed_events.clear()
            self.event_count = 0
            self.last_event_time = None
            self.event_history.clear()
            self.score_version += 1


# ============== Global Instances ==============

score_manager = ScoreManager(num_screens=GAME_SCREEN_COUNT)
target_calculator = TargetCalculator(
    base_score_per_child=CONFIG.get("base_score_per_child", 15)
)
piezo_config = PiezoConfigManager(
    threshold=CONFIG.get("default_piezo_threshold", 100),
    refractory_ms=CONFIG.get("default_piezo_refractory_ms", 200),
)
audio_manager = AudioManager(
    config=CONFIG.get("audio", {}),
    base_dir=os.path.dirname(os.path.abspath(__file__)),
)
scene_manager = SceneManager(
    os.environ.get(
        "THIEF_SCENE_DATA_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_data"),
    )
)
client_telemetry = ClientTelemetryStore(
    offline_after_seconds=CONFIG.get("client_offline_after_seconds", 15)
)
client_commands = ClientCommandStore()
runtime_state_store = RuntimeStateStore(
    os.environ.get(
        "THIEF_RUNTIME_STATE_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_state.json"),
    )
)
photo_manager = PhotoSessionManager(
    base_dir=os.environ.get(
        "THIEF_PHOTO_DATA_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo_sessions"),
    ),
    camera_config=CONFIG.get("camera", {}),
)
photo_guard = PhotoAccessGuard(
    ttl_seconds=int(CONFIG.get("camera", {}).get("admin_session_seconds", 28800))
)

# Spawn scheduler (oyun başlatılınca oluşturulur)
spawn_scheduler: Optional[SpawnScheduler] = None
result_scene: Optional[str] = None
result_scene_until = 0.0

# Global aktif ekran takibi (oyun başlamadan ÖNCE de kaydeder)
import time as _time
active_polling_screens: Dict[int, float] = {}  # screen_id -> last_poll_time
active_polling_lock = threading.RLock()


# ============== FastAPI App ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    photo_manager.initialize()
    audio_ready = audio_manager.initialize()
    _restore_runtime_checkpoint()
    print("=" * 50)
    print("Thief Server başlatıldı")
    print(f"Adres: http://{CONFIG['host']}:{CONFIG['port']}")
    print(f"Ekran sayısı: {CONFIG['num_screens']}")
    audio_status = audio_manager.get_status()
    if audio_ready:
        print(f"Ses cihazı: {audio_status['device_active']}")
    else:
        print(f"Ses devre dışı: {audio_status['last_error'] or 'kapalı'}")
    print("=" * 50)
    yield
    global spawn_scheduler
    if spawn_scheduler:
        _checkpoint_runtime()
        spawn_scheduler.stop()
    audio_manager.shutdown()
    photo_manager.shutdown()
    print("\nThief Server kapatıldı")


app = FastAPI(
    title="Thief Game Server",
    description="Hırsız oyunu skor toplama ve spawn kontrol sunucusu",
    version="2.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def protect_photo_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/photos" or request.url.path.startswith((
        "/api/photo-", "/api/camera/",
    )):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ============== Game API Endpoints ==============

def _checkpoint_runtime():
    scheduler = spawn_scheduler
    if not scheduler or not scheduler.session.is_active:
        runtime_state_store.clear()
        return
    session = scheduler.session
    runtime_state_store.save({
        "schema_version": 1,
        "saved_at": _time.time(),
        "session": {
            "child_count": session.child_count,
            "target_score": session.target_score,
            "screen_count": session.screen_count,
            "current_score": session.current_score,
            "start_time": session.start_time,
            "is_active": session.is_active,
            "total_seconds": session.total_seconds,
            "total_spawns": session.total_spawns,
            "countdown_seconds": session.countdown_seconds,
            "screen_targets": dict(session.screen_targets),
            "screen_scores": dict(session.screen_scores),
        },
        "score_manager": score_manager.snapshot(),
        "photo_session_id": photo_manager.current_session_id,
    })


def _clear_runtime_checkpoint():
    runtime_state_store.clear()


def _restore_runtime_checkpoint() -> bool:
    global spawn_scheduler
    payload = runtime_state_store.load()
    if not payload or payload.get("schema_version") != 1:
        return False
    raw = payload.get("session")
    if not isinstance(raw, dict) or not raw.get("is_active"):
        runtime_state_store.clear()
        return False
    start_time = float(raw.get("start_time", 0) or 0)
    total_seconds = max(1, int(raw.get("total_seconds", 1) or 1))
    if start_time <= 0 or _time.time() >= start_time + total_seconds:
        runtime_state_store.clear()
        photo_manager.end_session("power_timeout", completed=False)
        return False

    session = GameSession(
        child_count=max(1, int(raw.get("child_count", 1))),
        target_score=max(1, int(raw.get("target_score", 1))),
        screen_count=GAME_SCREEN_COUNT,
        current_score=max(0, int(raw.get("current_score", 0))),
        start_time=start_time,
        is_active=True,
        total_seconds=total_seconds,
        total_spawns=max(0, int(raw.get("total_spawns", 0))),
        countdown_seconds=max(0, int(raw.get("countdown_seconds", 0))),
        screen_targets=raw.get("screen_targets", {}),
        screen_scores=raw.get("screen_scores", {}),
    )
    screen_selector = ScreenSelector(GAME_SCREEN_COUNT)
    adaptive = AdaptiveSpawnController(
        base_spawn_interval=CONFIG.get("base_spawn_interval", 3.0),
        min_spawn_interval=CONFIG.get("min_spawn_interval", 0.5),
        max_spawn_interval=CONFIG.get("max_spawn_interval", 8.0),
        max_concurrent_spawns=CONFIG.get("max_concurrent_spawns", 3),
    )
    phase_spawner = PhaseBasedSpawner(total_seconds=total_seconds)
    spawn_scheduler = SpawnScheduler(
        session=session,
        screen_selector=screen_selector,
        adaptive_controller=adaptive,
        phase_spawner=phase_spawner,
        debug=CONFIG.get("debug", False),
        on_session_end=_on_session_end,
        on_countdown_tick=_on_countdown_tick,
        on_gameplay_start=_on_gameplay_start,
    )
    score_manager.restore(payload.get("score_manager", {}))
    spawn_scheduler.resume()
    if not session.countdown_active:
        audio_manager.play_music()
    print(
        f"[Recovery] Aktif oyun kurtarıldı: "
        f"{session.current_score}/{session.target_score}, "
        f"kalan {max(0, total_seconds - session.elapsed_seconds)} sn"
    )
    return True


def _on_session_end(reason: str):
    """Süre/hedef nedeniyle otomatik biten oyunun sesini ve fotoğraf oturumunu yönet."""
    completed = reason == "target"
    _show_result_scene("win" if completed else "lose")
    audio_manager.end_game(completed=completed)
    photo_manager.end_session(reason, completed=completed)
    _clear_runtime_checkpoint()


def _show_result_scene(scene_id: Optional[str]):
    """Oyun sonucu sahnesini ayarlanan süre boyunca görünür tut."""
    global result_scene, result_scene_until
    result_scene = scene_id
    result_scene_until = (
        _time.time() + float(CONFIG.get("result_scene_seconds", 8))
        if scene_id
        else 0.0
    )


def _runtime_scene() -> str:
    if result_scene and _time.time() < result_scene_until:
        return result_scene
    return "waiting"


def _play_scene_audio_cue(cue: dict) -> bool:
    asset_name = str(cue.get("asset", ""))
    asset_path = scene_manager.resolve_asset(asset_name) if asset_name else None
    return audio_manager.play_scene_cue(
        sound_name=str(cue.get("sound", "")),
        asset_path=str(asset_path) if asset_path else None,
        volume=float(cue.get("volume", 1.0)),
        loop=bool(cue.get("loop", False)),
        fade_in_ms=int(cue.get("fade_in_ms", 0) or 0),
        fade_out_ms=int(cue.get("fade_out_ms", 0) or 0),
        max_duration_ms=int(cue.get("max_duration_ms", 0) or 0),
        pan=float(cue.get("pan", 0) or 0),
    )


scene_audio_runtime = SceneAudioRuntime(
    play_cue=_play_scene_audio_cue,
    stop_loops=audio_manager.stop_scene_audio,
)


def _resolve_server_rule_scene(base_scene: str, document: dict, remaining_seconds: int, game_active: bool) -> str:
    scores = score_manager.get_scores()
    rules = sorted(document.get("rules", []), key=lambda item: float(item.get("priority", 0)), reverse=True)
    for rule in rules:
        scene_id = str(rule.get("scene_id", ""))
        if not rule.get("enabled", True) or scene_id not in document.get("scenes", {}):
            continue
        event = str(rule.get("event", "always"))
        value = float(rule.get("value", 0) or 0)
        matched = (
            event == "always"
            or (event == "win" and base_scene == "win")
            or (event == "lose" and base_scene == "lose")
            or (event == "game_active" and game_active == bool(rule.get("boolean", True)))
            or (event == "score_gte" and scores.total_score >= value)
            or (event == "score_lte" and scores.total_score <= value)
            or (event == "time_lte" and remaining_seconds <= value)
            or (event == "time_gte" and remaining_seconds >= value)
            or (event == "hit_gte" and scores.event_count >= value)
        )
        if matched:
            return scene_id
    return base_scene


def _tick_scene_audio(scene_id: str, remaining_seconds: int = 0, game_active: bool = False):
    document = scene_manager.get_published_document()
    resolved_scene = _resolve_server_rule_scene(scene_id, document, remaining_seconds, game_active)
    scene_audio_runtime.tick(resolved_scene, document)

def _on_countdown_tick(message: str):
    """Merkezi hoparlörde intro ve 3-2-1 seslerini çal."""
    if message == "HIRSIZLARI VUR":
        audio_manager.begin_countdown()
        return
    try:
        audio_manager.play_countdown(int(message))
    except ValueError:
        pass


def _on_gameplay_start():
    """Sayım tamamlanınca müziği ve başlangıç efektini aç."""
    audio_manager.begin_gameplay()


@app.get("/api/game/profiles")
async def game_profiles():
    """Yayınlanmış, sahada güvenle kullanılabilecek oyun profilleri."""
    return {"profiles": scene_manager.get_published_document().get("game_profiles", {})}

@app.post("/api/game/start")
async def start_game(req: StartGameRequest, request: Request):
    """Yeni oyun başlat"""
    global spawn_scheduler
    if req.capture_photos:
        if not req.photo_consent:
            raise HTTPException(
                status_code=400,
                detail="Fotoğraf çekimi için veli/katılımcı onayı doğrulanmalıdır",
            )
        photo_guard.authorize(request, write=True)
    _show_result_scene(None)
    scene_audio_runtime.reset()

    # Önceki oyunu durdur
    if spawn_scheduler:
        spawn_scheduler.stop()

    # Profil varsayılanlarını çöz; istekte verilen alanlar profili ezer.
    profiles = scene_manager.get_published_document().get("game_profiles", {})
    profile = profiles.get(req.profile_id, {}) if req.profile_id else {}
    if req.profile_id and not profile:
        raise HTTPException(status_code=404, detail="Oyun profili bulunamadı")
    child_count = req.child_count or int(profile.get("child_count", 3))
    screen_count = GAME_SCREEN_COUNT
    difficulty = req.difficulty or profile.get("difficulty", "normal")
    duration = req.duration_minutes or int(profile.get("duration_minutes", CONFIG.get("game_duration_minutes", 35)))
    target_info = target_calculator.calculate_screen_quotas(
        child_count=child_count,
        difficulty=difficulty,
        duration_minutes=duration,
        screen_count=screen_count,
        hits_per_child_per_screen=float(CONFIG.get("hits_per_child_per_screen", 6)),
        minimum_per_screen=int(CONFIG.get("minimum_hits_per_screen", 12)),
    )
    target_score = target_info["total_target"]

    # Oturum oluştur
    total_secs = duration * 60
    session = GameSession(
        child_count=child_count,
        target_score=target_score,
        screen_count=screen_count,
        total_seconds=total_secs,
        screen_targets=target_info["screen_targets"],
    )

    # Kontrolcüleri oluştur
    screen_selector = ScreenSelector(screen_count)
    adaptive = AdaptiveSpawnController(
        base_spawn_interval=CONFIG.get("base_spawn_interval", 3.0),
        min_spawn_interval=CONFIG.get("min_spawn_interval", 0.5),
        max_spawn_interval=CONFIG.get("max_spawn_interval", 8.0),
        max_concurrent_spawns=CONFIG.get("max_concurrent_spawns", 3),
    )
    phase_spawner = PhaseBasedSpawner(total_seconds=total_secs)

    # Scheduler oluştur ve başlat
    spawn_scheduler = SpawnScheduler(
        session=session,
        screen_selector=screen_selector,
        adaptive_controller=adaptive,
        phase_spawner=phase_spawner,
        debug=CONFIG.get("debug", False),
        on_session_end=_on_session_end,
        on_countdown_tick=_on_countdown_tick,
        on_gameplay_start=_on_gameplay_start,
    )

    # Oyun başlamadan önce poll yapan ekranları hemen kaydet
    active_cutoff = _time.time() - 10.0
    with active_polling_lock:
        active_snapshot = [
            (sid, last_t)
            for sid, last_t in active_polling_screens.items()
            if 1 <= sid <= screen_count and last_t >= active_cutoff
        ]
    for sid, last_t in active_snapshot:
        spawn_scheduler._active_screens[sid] = last_t

    if CONFIG.get("debug"):
        print(f"[Game] Kayıtlı aktif ekranlar: {[sid for sid, _ in active_snapshot]}")

    try:
        photo_session = photo_manager.start_session(
            req.session_name,
            capture_enabled=req.capture_photos,
            consent_confirmed=req.photo_consent,
            child_count=child_count,
            duration_minutes=duration,
            difficulty=difficulty,
            screen_targets=target_info["screen_targets"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Skorları sıfırla
    score_manager.reset()
    if spawn_scheduler:
        spawn_scheduler.reset_score()

    countdown_seconds = CONFIG.get("countdown_seconds", 4)
    spawn_scheduler.start(countdown_seconds=countdown_seconds)
    _checkpoint_runtime()

    if CONFIG.get("debug"):
        print(f"[Game] Oyun başlatıldı! Çocuk: {child_count}, Hedef: {target_score}")

    return {
        "success": True,
        "target_score": target_score,
        "per_screen_target": target_info["per_screen_target"],
        "screen_targets": target_info["screen_targets"],
        "profile_id": req.profile_id,
        "child_count": child_count,
        "screen_count": screen_count,
        "difficulty": difficulty,
        "game_duration_minutes": duration,
        "countdown_seconds": countdown_seconds,
        "photo_session": photo_session,
    }


@app.get("/api/game/status")
async def game_status():
    """Oyun durumu"""
    current_photo_session = photo_manager.get_current()
    photo_capture = {
        "enabled": bool(current_photo_session and current_photo_session.get("capture_enabled")),
        "photo_count": len(current_photo_session.get("photos", [])) if current_photo_session else 0,
        "status": current_photo_session.get("status") if current_photo_session else None,
    }
    if not spawn_scheduler:
        return {
            "is_active": False,
            "message": "Oyun başlatılmadı",
            "photo_capture": photo_capture,
        }

    status = spawn_scheduler.get_status()
    status['score_data'] = score_manager.get_scores().model_dump()
    status['photo_capture'] = photo_capture
    return status


@app.post("/api/game/end")
async def end_game():
    """Oyunu bitir"""
    global spawn_scheduler

    if spawn_scheduler:
        status = spawn_scheduler.get_status()
        final_score = status["current_score"]
        target = status["target_score"]
        spawn_scheduler.stop()
        completed = bool(status.get("all_screens_complete"))
        _show_result_scene("win" if completed else "lose")
        audio_manager.end_game(completed=completed)
        photo_session = photo_manager.end_session("manual", completed=completed)
        _clear_runtime_checkpoint()

        if CONFIG.get("debug"):
            print(f"[Game] Oyun bitti! Skor: {final_score}/{target}")

        return {
            "success": True,
            "final_score": final_score,
            "target_score": target,
            "completed": completed,
            "photo_session": photo_session,
        }

    return {"success": False, "message": "Aktif oyun yok"}


# ============== Spawn Polling ==============

@app.get("/spawn/poll")
async def spawn_poll(
    screen_id: int = Query(..., ge=1, le=GAME_SCREEN_COUNT)
):
    """Client spawn kontrolü"""
    # Ekranı GLOBAL olarak kaydet (oyun başlamadan önce de)
    with active_polling_lock:
        active_polling_screens[screen_id] = _time.time()

    if not spawn_scheduler:
        active_scene = _runtime_scene()
        _tick_scene_audio(active_scene, 0, False)
        return {
            "spawn": False,
            "game_active": False,
            "active_scene": active_scene,
            "total_score": score_manager.get_scores().total_score,
            "target_score": 0,
            "remaining_seconds": 0,
            "score_version": score_manager.get_scores().score_version,
        }

    scheduler_status = spawn_scheduler.get_status()
    if not scheduler_status["is_active"]:
        active_scene = _runtime_scene()
        remaining_seconds = max(
            0,
            scheduler_status["total_seconds"] - scheduler_status["elapsed_seconds"],
        )
        _tick_scene_audio(active_scene, remaining_seconds, False)
        return {
            "spawn": False,
            "game_active": False,
            "active_scene": active_scene,
            "total_score": score_manager.get_scores().total_score,
            "target_score": scheduler_status["target_score"],
            "remaining_seconds": max(
                0,
                scheduler_status["total_seconds"] - scheduler_status["elapsed_seconds"],
            ),
            "score_version": score_manager.get_scores().score_version,
        }

    remaining_seconds = max(0, scheduler_status["total_seconds"] - scheduler_status["elapsed_seconds"])
    result = spawn_scheduler.poll_spawn(screen_id)
    if result.get("screen_complete"):
        active_scene = "jail"
    elif scheduler_status["countdown_message"] == "HIRSIZLARI VUR":
        active_scene = "intro"
    elif scheduler_status["countdown_active"]:
        active_scene = "countdown"
    else:
        active_scene = "gameplay"

    # Jail ekranı clienta özeldir; merkezi ses diğer ekranlar için gameplay akışında kalır.
    audio_scene = active_scene if active_scene in {"intro", "countdown", "gameplay"} else "gameplay"
    _tick_scene_audio(audio_scene, remaining_seconds, True)
    result.update({
        "game_active": True,
        "participating": True,
        "active_scene": active_scene,
        "phase": scheduler_status["phase"],
        "countdown_active": scheduler_status["countdown_active"],
        "countdown_message": scheduler_status["countdown_message"],
        "countdown_remaining_ms": scheduler_status["countdown_remaining_ms"],
        "total_score": scheduler_status["current_score"],
        "target_score": scheduler_status["target_score"],
        "remaining_seconds": remaining_seconds,
        "score_version": score_manager.get_scores().score_version,
    })
    return result

# ============== Score Endpoints ==============

@app.post("/event")
async def receive_event(event: ScoreEvent):
    """Client vuruşunu yalnızca o ekranın kalan kotası kadar işle."""
    screen_was_complete = False
    if spawn_scheduler and spawn_scheduler.session.is_active:
        if event.screen_id > spawn_scheduler.session.screen_count:
            raise HTTPException(status_code=409, detail=f"Ekran {event.screen_id} aktif oyun oturumuna dahil değil")
        before = spawn_scheduler.session.screen_status(event.screen_id)
        screen_was_complete = bool(before["screen_complete"])
        if before["screen_complete"]:
            return {"success": True, "is_new": False, "accepted_points": 0,
                    "total_score": spawn_scheduler.session.current_score, **before}
        accepted_points = min(event.points, before["screen_remaining"])
        scored_event = event.model_copy(update={"points": accepted_points})
    else:
        accepted_points = event.points
        scored_event = event

    is_new = score_manager.process_event(scored_event)
    quota_result = {}
    if is_new and spawn_scheduler and spawn_scheduler.session.is_active:
        quota_result = spawn_scheduler.update_score(event.screen_id, accepted_points)
        if quota_result.get("screen_complete") and not screen_was_complete:
            photo_manager.capture_screen(event.screen_id)
    if is_new:
        audio_manager.play_hit()
        _checkpoint_runtime()

    if CONFIG.get("debug"):
        status = "✅ YENİ" if is_new else "⏭️ DUPLICATE"
        print(f"[Event] {status} | Ekran {event.screen_id} | +{accepted_points} puan")

    return {"success": True, "is_new": is_new, "accepted_points": accepted_points if is_new else 0,
            "total_score": spawn_scheduler.session.current_score if spawn_scheduler else score_manager.get_scores().total_score,
            **quota_result}

@app.get("/score", response_model=ScoreResponse)
async def get_score():
    return score_manager.get_scores()


@app.get("/score/screen/{screen_id}")
async def get_screen_score(screen_id: int):
    if screen_id < 1 or screen_id > score_manager.num_screens:
        raise HTTPException(status_code=404, detail=f"Ekran {screen_id} bulunamadı")
    return {
        "screen_id": screen_id,
        "score": score_manager.get_screen_score(screen_id),
        "quota": spawn_scheduler.session.screen_status(screen_id) if spawn_scheduler else None,
    }


@app.post("/reset")
async def reset_scores():
    score_manager.reset()
    if spawn_scheduler:
        spawn_scheduler.reset_score()
        _checkpoint_runtime()
    if CONFIG.get("debug"):
        print("🔄 Skorlar sıfırlandı!")
    return {
        "success": True,
        "message": "Skorlar sifirlandi",
        "score_version": score_manager.get_scores().score_version,
    }


@app.get("/history")
async def get_history():
    events = score_manager.get_history()
    return {
        "events": events,
        "count": len(events),
    }


@app.get("/health")
async def health_check():
    scores = score_manager.get_scores()
    game_active = False
    if spawn_scheduler:
        game_active = spawn_scheduler.get_status()["is_active"]
    return {
        "status": "healthy",
        "uptime": "ok",
        "total_score": scores.total_score,
        "game_active": game_active,
        "audio": audio_manager.get_status(),
    }


# ============== Piezo Config Endpoints ==============

@app.post("/api/piezo/config")
async def set_piezo_config(req: PiezoConfigRequest):
    """Piezo threshold ve refractory ayarla"""
    if req.threshold < 0 or req.threshold > 1023:
        raise HTTPException(status_code=400, detail="Threshold 0-1023 arasında olmalı")
    if req.refractory_ms < 50 or req.refractory_ms > 5000:
        raise HTTPException(status_code=400, detail="Refractory 50-5000ms arasında olmalı")

    piezo_config.update(req.threshold, req.refractory_ms)

    if CONFIG.get("debug"):
        print(f"[Piezo] Ayar güncellendi: T={req.threshold}, R={req.refractory_ms}ms")

    return {"success": True, **piezo_config.get_config()}


@app.get("/api/piezo/config")
async def get_piezo_config():
    """Mevcut piezo ayarlarını getir"""
    return piezo_config.get_config()


@app.get("/api/piezo/config/poll")
async def poll_piezo_config(
    screen_id: int = Query(..., ge=1, le=GAME_SCREEN_COUNT)
):
    """Client piezo config polling"""
    result = piezo_config.poll(screen_id)
    if result:
        return {"changed": True, **result}
    return {"changed": False}


# ============== Client Telemetry Endpoints ==============

@app.post("/api/clients/heartbeat")
async def client_heartbeat(req: ClientTelemetryRequest):
    """İstemci sağlık verisini RAM üzerinde güncelle; kalıcı disk yazımı yapma."""
    return {"success": True, **client_telemetry.update(req.screen_id, req.model_dump())}


@app.post("/api/client/poll")
async def combined_client_poll(req: ClientPollRequest):
    """Spawn, piezo ve seyrek heartbeat verisini tek keep-alive isteğinde birleştir."""
    spawn_state = await spawn_poll(req.screen_id)
    piezo_state = piezo_config.poll(req.screen_id)
    heartbeat = None
    if isinstance(req.telemetry, dict):
        payload = {**req.telemetry, "screen_id": req.screen_id}
        try:
            validated = ClientTelemetryRequest(**payload)
            heartbeat = client_telemetry.update(req.screen_id, validated.model_dump())
        except (TypeError, ValueError):
            heartbeat = None
    return {
        "spawn_state": spawn_state,
        "piezo_config": {"changed": bool(piezo_state), **(piezo_state or {})},
        "heartbeat": heartbeat,
        "command": client_commands.poll(req.screen_id),
    }


@app.get("/api/clients/status")
async def get_client_status():
    """Dashboard için çevrimiçi istemci, FPS, sıcaklık ve piezo özetini getir."""
    return client_telemetry.list(GAME_SCREEN_COUNT)


@app.post("/api/clients/{screen_id}/restart")
async def restart_client(screen_id: int):
    """İstemciyi normal poll kanalı üzerinden kapat; systemd yeniden açar."""
    if not 1 <= int(screen_id) <= GAME_SCREEN_COUNT:
        raise HTTPException(status_code=422, detail="Ekran numarası geçersiz")
    status = client_telemetry.list(GAME_SCREEN_COUNT)["clients"][screen_id - 1]
    if not status.get("online"):
        raise HTTPException(status_code=409, detail="İstemci çevrimdışı")
    command = client_commands.queue(screen_id, "restart")
    return {"success": True, "command": command}


@app.post("/api/clients/{screen_id}/update")
async def update_client(screen_id: int, request: Request):
    """PIN korumali, allowlist tabanli client updater servisini tetikle."""
    photo_guard.authorize(request, write=True)
    if not 1 <= int(screen_id) <= GAME_SCREEN_COUNT:
        raise HTTPException(status_code=422, detail="Ekran numarasi gecersiz")
    if spawn_scheduler and spawn_scheduler.get_status().get("is_active"):
        raise HTTPException(status_code=409, detail="Oyun devam ederken client guncellenemez")
    status = client_telemetry.list(GAME_SCREEN_COUNT)["clients"][screen_id - 1]
    if not status.get("online"):
        raise HTTPException(status_code=409, detail="Istemci cevrimdisi")
    if status.get("update_state") == "running":
        raise HTTPException(status_code=409, detail="Bu istemcide guncelleme zaten calisiyor")
    command = client_commands.queue(screen_id, "update")
    return {"success": True, "command": command}


@app.get("/api/field-check")
async def field_check():
    """Sekiz client, kamera ve sesi tek saha hazırlık raporunda birleştir."""
    telemetry = client_telemetry.list(GAME_SCREEN_COUNT)
    clients = []
    for client in telemetry["clients"]:
        issues = []
        if not client.get("online"):
            issues.append("çevrimdışı")
        else:
            if not client.get("serial_connected"):
                issues.append("Arduino/seri bağlantı yok")
            temperature = client.get("cpu_temp_c")
            if temperature is not None and float(temperature) >= 78:
                issues.append("yüksek sıcaklık")
            if client.get("app_version") != "scene-engine-v8-dirty-rect":
                issues.append("client sürümü eski")
            if float(client.get("fps", 0) or 0) < 15:
                issues.append("FPS düşük")
        clients.append({
            "screen_id": client["screen_id"],
            "ready": not issues,
            "issues": issues,
            "online": bool(client.get("online")),
            "app_version": client.get("app_version", ""),
        })

    audio = audio_manager.get_status()
    camera = photo_manager.camera_status()
    audio_ready = not audio.get("enabled") or bool(audio.get("available"))
    camera_ready = not camera.get("enabled") or bool(camera.get("available"))
    return {
        "ready": all(item["ready"] for item in clients) and audio_ready and camera_ready,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "clients": clients,
        "online_count": telemetry["online_count"],
        "audio": {
            "ready": audio_ready,
            "enabled": bool(audio.get("enabled")),
            "available": bool(audio.get("available")),
            "device": audio.get("device_active") or audio.get("device_requested"),
            "error": audio.get("last_error"),
        },
        "camera": {
            "ready": camera_ready,
            "enabled": bool(camera.get("enabled")),
            "available": bool(camera.get("available")),
            "device": camera.get("device"),
            "error": camera.get("last_error"),
        },
    }


# ============== Korumalı USB Kamera ve Oturum Fotoğrafları ==============

@app.get("/api/photo-auth/status")
async def photo_auth_status(request: Request):
    csrf = photo_guard.validate(request.cookies.get(photo_guard.COOKIE_NAME, ""))
    return {
        "configured": photo_guard.configured,
        "authenticated": bool(csrf),
        "csrf": csrf if csrf else None,
    }


@app.post("/api/photo-auth/login")
async def photo_auth_login(req: PhotoLoginRequest, request: Request):
    token, csrf = photo_guard.login(req.pin, request)
    response = JSONResponse({"success": True, "csrf": csrf})
    response.set_cookie(
        photo_guard.COOKIE_NAME,
        token,
        max_age=photo_guard.ttl_seconds,
        httponly=True,
        secure=photo_guard.secure_cookie,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/photo-auth/logout")
async def photo_auth_logout(request: Request):
    photo_guard.authorize(request, write=True)
    response = JSONResponse({"success": True})
    response.delete_cookie(photo_guard.COOKIE_NAME, path="/")
    return response


@app.get("/api/camera/status")
async def camera_status(request: Request):
    photo_guard.authorize(request)
    return photo_manager.camera_status()


@app.post("/api/camera/test")
async def camera_test(request: Request):
    photo_guard.authorize(request, write=True)
    try:
        await run_in_threadpool(photo_manager.capture_test)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True, "url": f"/api/camera/test-image?v={int(_time.time())}"}


@app.get("/api/camera/test-image")
async def camera_test_image(request: Request):
    photo_guard.authorize(request)
    try:
        path = photo_manager.get_test_photo()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/photo-storage")
async def photo_storage(request: Request):
    photo_guard.authorize(request)
    return {
        **photo_manager.storage_status(),
        "cleanup_preview": photo_manager.cleanup_expired(dry_run=True),
    }


@app.post("/api/photo-storage/cleanup")
async def cleanup_photo_storage(
    req: PhotoCleanupRequest,
    request: Request,
):
    photo_guard.authorize(request, write=True)
    return photo_manager.cleanup_expired(dry_run=req.dry_run)


@app.get("/api/photo-sessions")
async def photo_sessions(request: Request):
    photo_guard.authorize(request)
    sessions = photo_manager.list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/photo-sessions/{session_id}")
async def photo_session_detail(session_id: str, request: Request):
    photo_guard.authorize(request)
    try:
        return photo_manager.get_session(session_id)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/photo-sessions/{session_id}")
async def update_photo_session(session_id: str, req: PhotoSaleUpdateRequest, request: Request):
    photo_guard.authorize(request, write=True)
    try:
        return photo_manager.update_sale(
            session_id,
            sold=req.sold,
            sale_price=req.sale_price,
            customer_name=req.customer_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/photo-sessions/{session_id}")
async def delete_photo_session(session_id: str, request: Request):
    photo_guard.authorize(request, write=True)
    try:
        photo_manager.delete_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True}


@app.get("/api/photo-sessions/{session_id}/photos/{filename}")
async def photo_session_file(
    session_id: str,
    filename: str,
    request: Request,
    download: bool = False,
):
    photo_guard.authorize(request)
    try:
        path = photo_manager.get_photo_path(session_id, filename)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=filename if download else None,
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/api/photo-sessions/{session_id}/download")
async def download_photo_session(session_id: str, request: Request):
    photo_guard.authorize(request)
    try:
        zip_path, download_name = await run_in_threadpool(
            photo_manager.build_download_zip, session_id
        )
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=download_name,
        headers={"Cache-Control": "private, no-store"},
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


# ============== Server Audio Endpoints ==============

@app.get("/api/audio/status")
async def get_audio_status():
    """Pi 4 analog ses çıkışı ve mixer durumunu getir."""
    return audio_manager.get_status()


@app.post("/api/audio/config")
async def set_audio_config(req: AudioConfigRequest):
    """Çalışma zamanı ses seviyelerini uygula."""
    status = audio_manager.configure(
        enabled=req.enabled,
        master_volume=req.master_volume,
        music_volume=req.music_volume,
        sfx_volume=req.sfx_volume,
    )
    return {"success": True, **status}


@app.post("/api/audio/test")
async def test_audio(req: AudioTestRequest):
    """Dashboard'dan efekt veya müzik testi yap."""
    played = audio_manager.test_sound(req.sound_type)
    return {
        "success": played,
        "sound_type": req.sound_type,
        **audio_manager.get_status(),
    }


# ============== Scene Editor API ==============

@app.get("/api/scenes/editor")
async def get_scene_editor_state():
    """Editör için taslak, asset ve sürüm bilgisini döndür."""
    return scene_manager.get_editor_state()


@app.put("/api/scenes/draft")
async def save_scene_draft(req: SceneDraftRequest):
    """Sahne belgesini yayınlamadan, revizyon çakışmalarını önleyerek kaydet."""
    try:
        return scene_manager.save_draft(req.document, req.base_revision)
    except SceneRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "server_revision": exc.actual},
        ) from exc
    except SceneValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/scenes/diff")
async def diff_scene_draft():
    """Taslak ile son yayın arasındaki güvenli yayın özetini döndür."""
    return scene_manager.diff_summary()


@app.get("/api/scenes/audit")
async def audit_scene_draft():
    """Yayın öncesi eksik asset ve performans uyarılarını döndür."""
    return scene_manager.audit_document()


@app.post("/api/scenes/publish")
async def publish_scene_draft():
    """Geçerli taslağı atomik olarak bütün clientlara yayınla."""
    try:
        return scene_manager.publish()
    except SceneValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/scenes/rollback/{version}")
async def rollback_scene_version(version: int):
    """Eski yayın sürümünü güvenli biçimde taslağa geri yükle."""
    try:
        return scene_manager.rollback(version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SceneValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/scenes/preview")
async def set_scene_preview(req: ScenePreviewRequest):
    """Bir taslak sahneyi yalnızca seçilen client ekranında göster."""
    try:
        scene_manager.set_preview(req.screen_id, req.scene_id)
    except SceneValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "screen_id": req.screen_id,
        "scene_id": req.scene_id,
    }


@app.delete("/api/scenes/preview/{screen_id}")
async def clear_scene_preview(screen_id: int):
    """Seçilen ekranı taslak önizlemeden normal yayın akışına döndür."""
    if screen_id < 1 or screen_id > GAME_SCREEN_COUNT:
        raise HTTPException(status_code=422, detail="Geçersiz ekran ID")
    scene_manager.clear_preview(screen_id)
    return {"success": True, "screen_id": screen_id}


@app.get("/api/scenes/client")
async def get_client_scenes(
    screen_id: int = Query(..., ge=1, le=GAME_SCREEN_COUNT),
    known_version: str = Query("", max_length=80),
):
    """Clienta yalnızca sürüm değiştiğinde sahne belgesini ve asset manifestini ver."""
    return scene_manager.get_client_payload(screen_id, known_version)


@app.post("/api/scenes/screenshot/request")
async def request_scene_screenshot(req: SceneScreenshotRequest):
    """Seçilen clienttan tek seferlik gerçek Pygame görüntüsü iste."""
    return scene_manager.request_client_screenshot(req.screen_id)


@app.get("/api/scenes/screenshot/{screen_id}/status")
async def get_scene_screenshot_status(
    screen_id: int,
):
    if screen_id < 1 or screen_id > GAME_SCREEN_COUNT:
        raise HTTPException(status_code=422, detail="Geçersiz ekran ID")
    return scene_manager.get_client_screenshot_status(screen_id)


@app.post("/api/scenes/screenshot/upload")
async def upload_scene_screenshot(req: SceneScreenshotUpload):
    """Clientın talep üzerine yakaladığı PNG/JPEG görüntüsünü al."""
    try:
        content = base64.b64decode(req.data_base64, validate=True)
        return scene_manager.save_client_screenshot(
            req.screen_id,
            req.request_token,
            content,
        )
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/scenes/screenshot/{screen_id}")
async def get_scene_screenshot(screen_id: int):
    if screen_id < 1 or screen_id > GAME_SCREEN_COUNT:
        raise HTTPException(status_code=422, detail="Geçersiz ekran ID")
    screenshot_path = scene_manager.resolve_client_screenshot(screen_id)
    if not screenshot_path:
        raise HTTPException(status_code=404, detail="Client görüntüsü henüz yok")
    media_type = "image/png" if screenshot_path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(screenshot_path, media_type=media_type)


@app.post("/api/scenes/assets")
async def upload_scene_asset(req: SceneAssetRequest):
    """Editörden base64 kodlu görsel asset yükle."""
    try:
        content = base64.b64decode(req.data_base64, validate=True)
        return scene_manager.save_asset(req.filename, content, optimize=req.optimize)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Geçersiz asset: {exc}") from exc


@app.get("/api/scene-assets/{filename}")
async def get_scene_asset(filename: str):
    """Yayınlanan/taslak sahnelerin görsel assetini sun."""
    if filename == "__client_background__":
        candidates = [
            os.environ.get("THIEF_GAME_BACKGROUND", ""),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "thief_client", "assets", "bg", "bg.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bg", "bg.png"),
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return FileResponse(os.path.abspath(candidate), media_type="image/png")
        raise HTTPException(status_code=404, detail="Client background not found")

    asset_path = scene_manager.resolve_asset(filename)
    if not asset_path:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")
    return FileResponse(asset_path)


# ============== Dashboard ==============

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎮 Hırsız Oyunu - Kontrol Paneli</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #fff;
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        h1 {
            text-align: center;
            font-size: 2.5rem;
            margin-bottom: 25px;
            text-shadow: 0 0 20px rgba(255, 215, 0, 0.5);
        }

        /* === Grid Layout === */
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }

        .grid-full {
            grid-column: 1 / -1;
        }

        /* === Cards === */
        .card {
            background: rgba(255, 255, 255, 0.08);
            padding: 20px;
            border-radius: 15px;
            backdrop-filter: blur(10px);
        }

        .card h3 {
            margin-bottom: 15px;
            opacity: 0.9;
            font-size: 1.2rem;
        }

        /* === Total Score === */
        .total-score {
            text-align: center;
            background: linear-gradient(135deg, #ff6b6b, #feca57);
            padding: 25px;
            border-radius: 20px;
            margin-bottom: 20px;
            box-shadow: 0 10px 40px rgba(255, 107, 107, 0.3);
        }

        .total-score h2 {
            font-size: 1.3rem;
            opacity: 0.9;
        }

        .total-score .score {
            font-size: 5rem;
            font-weight: bold;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }

        .total-score .target {
            font-size: 1.5rem;
            opacity: 0.8;
        }

        /* === Progress Bar === */
        .progress-container {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            height: 30px;
            margin: 15px 0;
            overflow: hidden;
            position: relative;
        }

        .progress-bar {
            height: 100%;
            border-radius: 10px;
            transition: width 0.5s ease;
            background: linear-gradient(90deg, #2ecc71, #f1c40f, #e74c3c);
        }

        .progress-text {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-weight: bold;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
        }

        /* === Phase Badge === */
        .phase-badge {
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9rem;
        }

        .phase-WARMUP { background: #3498db; }
        .phase-COUNTDOWN {
            background: #9b59b6;
            animation: countdownPulse 0.7s infinite alternate;
        }
        .phase-NORMAL { background: #2ecc71; }
        .phase-INTENSE { background: #e74c3c; }
        @keyframes countdownPulse {
            from { transform: scale(1); box-shadow: 0 0 0 rgba(255, 224, 56, 0); }
            to { transform: scale(1.07); box-shadow: 0 0 16px rgba(255, 224, 56, 0.9); }
        }

        /* === Screens Grid === */
        .screens {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
        }

        .screen-card {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 12px;
            text-align: center;
            transition: transform 0.3s, box-shadow 0.3s;
        }

        .screen-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
        }

        .screen-card h4 {
            font-size: 0.9rem;
            margin-bottom: 8px;
            opacity: 0.8;
        }

        .screen-card .score {
            font-size: 2rem;
            font-weight: bold;
            color: #feca57;
        }

        .screen-card .spawn-pct {
            font-size: 0.8rem;
            opacity: 0.6;
            margin-top: 4px;
        }

        /* === Controls === */
        .controls {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }

        .btn {
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 0.95rem;
            cursor: pointer;
            transition: background 0.3s, transform 0.1s;
            color: white;
            font-weight: 500;
        }

        .btn:hover { transform: scale(1.02); }
        .btn:active { transform: scale(0.98); }

        .btn-green { background: #27ae60; }
        .btn-green:hover { background: #2ecc71; }
        .btn-red { background: #e74c3c; }
        .btn-red:hover { background: #c0392b; }
        .btn-blue { background: #2980b9; }
        .btn-blue:hover { background: #3498db; }
        .btn-orange { background: #e67e22; }
        .btn-orange:hover { background: #f39c12; }

        input[type="number"],
        input[type="text"] {
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: #fff;
            padding: 8px 12px;
            border-radius: 6px;
            width: 80px;
            font-size: 0.95rem;
        }

        select {
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: #fff;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.95rem;
        }

        select option {
            background: #1a1a2e;
            color: #fff;
        }

        label {
            font-size: 0.9rem;
            opacity: 0.8;
        }

        /* === Slider === */
        .slider-group {
            margin: 10px 0;
        }

        .slider-group label {
            display: block;
            margin-bottom: 5px;
        }

        .slider-row {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        input[type="range"] {
            flex: 1;
            accent-color: #feca57;
        }

        .slider-value {
            min-width: 60px;
            text-align: right;
            font-weight: bold;
            color: #feca57;
        }

        /* === Stats === */
        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }

        .stat-row:last-child {
            border-bottom: none;
        }

        .stat-label {
            opacity: 0.7;
        }

        .stat-value {
            font-weight: bold;
        }

        .urgency-HIGH { color: #e74c3c; }
        .urgency-MEDIUM { color: #f39c12; }
        .urgency-NORMAL { color: #2ecc71; }
        .urgency-LOW { color: #3498db; }

        /* === History === */
        .history {
            max-height: 250px;
            overflow-y: auto;
        }

        .history-item {
            padding: 8px 10px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 6px;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.9rem;
        }

        .history-item.new {
            animation: highlight 1s ease;
        }

        @keyframes highlight {
            0% { background: rgba(255, 215, 0, 0.5); }
            100% { background: rgba(255, 255, 255, 0.05); }
        }

        /* === Status indicator === */
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }

        .status-active { background: #2ecc71; box-shadow: 0 0 8px #2ecc71; }
        .status-inactive { background: #e74c3c; }

        /* === Responsive === */
        @media (max-width: 768px) {
            .grid { grid-template-columns: 1fr; }
            h1 { font-size: 1.8rem; }
            .total-score .score { font-size: 3rem; }
        }

        /* === Minimal dashboard refresh === */
        body {
            background: #0b111c;
            color: #e5e7eb;
            padding: 24px;
        }

        .container {
            max-width: 1280px;
        }

        h1 {
            text-align: left;
            font-size: 2rem;
            margin-bottom: 18px;
            text-shadow: none;
            letter-spacing: 0;
        }

        .top-layout {
            display: grid;
            grid-template-columns: minmax(280px, 420px) 1fr;
            gap: 16px;
            align-items: stretch;
            margin-bottom: 16px;
        }

        .total-score {
            display: flex;
            flex-direction: column;
            justify-content: center;
            text-align: left;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 8px;
            box-shadow: none;
            margin-bottom: 0;
            padding: 20px;
        }

        .total-score h2,
        .card h3 {
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #9ca3af;
            opacity: 1;
        }

        .total-score .score {
            font-size: 4.25rem;
            line-height: 1;
            color: #fbbf24;
            text-shadow: none;
        }

        .total-score .target {
            font-size: 1rem;
            color: #9ca3af;
            opacity: 1;
        }

        .progress-container {
            height: 10px;
            margin: 16px 0 4px;
            background: #1f2937;
            border-radius: 999px;
        }

        .progress-bar {
            background: #fbbf24;
            border-radius: 999px;
        }

        .progress-text {
            position: static;
            transform: none;
            display: block;
            margin-top: 10px;
            color: #9ca3af;
            text-shadow: none;
            font-size: 0.9rem;
        }

        .quick-starts {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
        }

        .quick-card {
            border: 1px solid #1f2937;
            border-radius: 8px;
            background: #0f172a;
            color: #e5e7eb;
            padding: 16px;
            text-align: left;
            cursor: pointer;
            transition: border-color 0.16s ease, background 0.16s ease, transform 0.16s ease;
        }

        .quick-card:hover {
            border-color: #fbbf24;
            background: #111c31;
            transform: translateY(-1px);
        }

        .quick-card strong {
            display: block;
            margin-bottom: 8px;
            font-size: 1.05rem;
        }

        .quick-card span {
            display: block;
            color: #9ca3af;
            font-size: 0.9rem;
            line-height: 1.45;
        }

        .grid {
            grid-template-columns: minmax(320px, 1.05fr) minmax(320px, 0.95fr);
            gap: 16px;
            margin-bottom: 16px;
        }

        .card {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 8px;
            padding: 18px;
            backdrop-filter: none;
        }

        .controls {
            gap: 8px;
        }

        .btn {
            border-radius: 7px;
            padding: 9px 14px;
            font-size: 0.9rem;
            font-weight: 650;
        }

        .btn:hover {
            transform: translateY(-1px);
        }

        .btn-green { background: #16a34a; }
        .btn-red { background: #dc2626; }
        .btn-blue { background: #2563eb; }
        .btn-orange { background: #d97706; }

        input[type="number"],
        input[type="text"],
        select {
            background: #0b1220;
            border: 1px solid #263244;
            color: #e5e7eb;
            border-radius: 7px;
        }

        .screen-card {
            background: #0f172a;
            border: 1px solid #1f2937;
            border-radius: 8px;
            padding: 12px;
        }

        .screen-card:hover {
            transform: none;
            box-shadow: none;
            border-color: #374151;
        }

        .history {
            max-height: 210px;
        }

        .history-item {
            background: #0f172a;
            border: 1px solid #1f2937;
            border-radius: 7px;
        }

        .stat-row {
            border-bottom-color: #1f2937;
        }

        @media (max-width: 900px) {
            .top-layout,
            .grid,
            .quick-starts {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-bottom:10px;">
            <a href="/photos" style="background:#e5e7eb;color:#111827;text-decoration:none;padding:10px 14px;border-radius:8px;font-weight:800;">📷 Fotoğraflar</a>
            <a href="/scene-editor" style="background:#fbbf24;color:#111827;text-decoration:none;padding:10px 14px;border-radius:8px;font-weight:800;">🎨 Sahne Editörü</a>
        </div>
        <h1>🎮 Hırsız Oyunu — Kontrol Paneli</h1>

        <div class="top-layout">
            <!-- Skor -->
            <div class="total-score">
                <h2>TOPLAM SKOR</h2>
                <div class="score" id="total-score">0</div>
                <div class="target">Hedef: <span id="target-score">—</span></div>
                <div class="progress-container">
                    <div class="progress-bar" id="progress-bar" style="width: 0%"></div>
                </div>
                <div class="progress-text" id="progress-text">0%</div>
            </div>

            <div class="card">
                <h3>Hızlı Başlangıç</h3>
                <div class="quick-starts">
                    <button class="quick-card" onclick="quickStart(2, 8, 30, 'easy')">
                        <strong>Kısa Tur</strong>
                        <span>2 çocuk · 8 ekran · 30 dk · Kolay</span>
                    </button>
                    <button class="quick-card" onclick="quickStart(5, 8, 35, 'normal')">
                        <strong>Standart</strong>
                        <span>5 çocuk · 8 ekran · 35 dk · Normal</span>
                    </button>
                    <button class="quick-card" onclick="quickStart(10, 8, 40, 'hard')">
                        <strong>Yoğun Mod</strong>
                        <span>10 çocuk · 8 ekran · 40 dk · Zor</span>
                    </button>
                </div>
            </div>
        </div>

        <div class="grid">
            <!-- Oyun Kontrol -->
            <div class="card">
                <h3>🎯 Oyun Kontrolü</h3>
                <div class="controls" style="margin-bottom: 12px;">
                    <label>Oturum adı:</label>
                    <input type="text" id="session-name" maxlength="80" placeholder="Örn. Ece'nin doğum günü">
                    <label>Profil:</label>
                    <select id="game-profile" onchange="applyGameProfile()"><option value="">Özel</option></select>
                </div>
                <div class="controls" style="margin-bottom: 12px;">
                    <label><input type="checkbox" id="capture-photos" checked> Ekran kotası bitince fotoğraf çek</label>
                    <label><input type="checkbox" id="photo-consent"> Veli/katılımcı fotoğraf onayı alındı</label>
                </div>
                <div id="photo-auth-status" style="color:#9ca3af;margin-bottom:8px">Fotoğraf yetkisi kontrol ediliyor…</div>
                <div id="game-start-error" style="color:#f87171;margin-bottom:10px"></div>
                <div class="controls" style="margin-bottom: 15px;">
                    <label>Çocuk:</label>
                    <input type="number" id="child-count" value="3" min="1" max="50">
                    <label>Ekran:</label>
                    <input type="number" id="screen-count" value="8" min="8" max="8" readonly title="Bu mimaride tüm 8 ekran daima aktiftir">
                    <label>Süre (dk):</label>
                    <input type="number" id="duration-minutes" value="35" min="1" max="120">
                    <label>Zorluk:</label>
                    <select id="difficulty">
                        <option value="easy">Kolay</option>
                        <option value="normal" selected>Normal</option>
                        <option value="hard">Zor</option>
                    </select>
                </div>
                <div class="controls">
                    <button class="btn btn-green" onclick="startGame()">▶ Oyunu Başlat</button>
                    <button class="btn btn-red" onclick="endGame()">⏹ Oyunu Bitir</button>
                    <button class="btn btn-orange" onclick="resetScores()">🔄 Skorları Sıfırla</button>
                </div>
            </div>

            <!-- Oyun Durumu -->
            <div class="card">
                <h3><span class="status-dot" id="game-status-dot"></span>Oyun Durumu</h3>
                <div class="stat-row">
                    <span class="stat-label">Durum</span>
                    <span class="stat-value" id="game-active">Pasif</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Faz</span>
                    <span class="stat-value" id="game-phase">—</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Geçen Süre</span>
                    <span class="stat-value" id="elapsed-time">00:00</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Spawn Aralığı</span>
                    <span class="stat-value" id="spawn-interval">—</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Aciliyet</span>
                    <span class="stat-value" id="urgency">—</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Toplam Spawn</span>
                    <span class="stat-value" id="total-spawns">0</span>
                </div>
            </div>

            <!-- Piezo Ayarları -->
            <div class="card">
                <h3>🔧 Piezo Ayarları</h3>
                <div class="slider-group">
                    <label>Threshold (Eşik Değeri)</label>
                    <div class="slider-row">
                        <input type="range" id="piezo-threshold" min="0" max="1023" value="100">
                        <span class="slider-value" id="threshold-value">100</span>
                    </div>
                </div>
                <div class="slider-group">
                    <label>Refractory (Bekleme Süresi, ms)</label>
                    <div class="slider-row">
                        <input type="range" id="piezo-refractory" min="50" max="1000" step="10" value="200">
                        <span class="slider-value" id="refractory-value">200ms</span>
                    </div>
                </div>
                <div class="controls" style="margin-top: 10px;">
                    <button class="btn btn-blue" onclick="applyPiezoConfig()">✅ Uygula</button>
                </div>
            </div>

            <!-- Server Ses Ayarları -->
            <div class="card">
                <h3>🔊 Server Sesi</h3>
                <div class="stat-row">
                    <span class="stat-label">Pi 4 Analog Jak</span>
                    <span class="stat-value" id="audio-device">Kontrol ediliyor…</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Mixer</span>
                    <span class="stat-value" id="audio-status">—</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Müzik</span>
                    <span class="stat-value" id="audio-music-status">Durdu</span>
                </div>
                <label style="display: block; margin: 12px 0;">
                    <input type="checkbox" id="audio-enabled" checked>
                    Sesi etkinleştir
                </label>
                <div class="slider-group">
                    <label>Genel Ses</label>
                    <div class="slider-row">
                        <input type="range" id="audio-master" min="0" max="100" value="80">
                        <span class="slider-value" id="audio-master-value">80%</span>
                    </div>
                </div>
                <div class="slider-group">
                    <label>Müzik</label>
                    <div class="slider-row">
                        <input type="range" id="audio-music" min="0" max="100" value="35">
                        <span class="slider-value" id="audio-music-value">35%</span>
                    </div>
                </div>
                <div class="slider-group">
                    <label>Efekt</label>
                    <div class="slider-row">
                        <input type="range" id="audio-sfx" min="0" max="100" value="90">
                        <span class="slider-value" id="audio-sfx-value">90%</span>
                    </div>
                </div>
                <div class="controls" style="margin-top: 10px;">
                    <button class="btn btn-blue" onclick="applyAudioConfig()">✅ Uygula</button>
                    <button class="btn btn-orange" onclick="testAudio('hit')">🥁 Vuruş Testi</button>
                    <button class="btn btn-green" onclick="testAudio('music')">🎵 Müzik Aç/Kapat</button>
                </div>
                <div id="audio-error" style="color: #f87171; margin-top: 10px;"></div>
            </div>

            <!-- İstatistikler -->
            <div class="card">
                <h3>📊 İstatistikler</h3>
                <div class="stat-row">
                    <span class="stat-label">Toplam Event</span>
                    <span class="stat-value" id="event-count">0</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Son Event</span>
                    <span class="stat-value" id="last-event">—</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Delta</span>
                    <span class="stat-value" id="delta">—</span>
                </div>
            </div>

            <!-- Ekran Skorları -->
            <div class="card grid-full">
                <h3>📺 Ekran Skorları</h3>
                <div class="screens" id="screens"></div>
            </div>

            <div class="card grid-full">
                <h3>İstemci Sağlığı ve Piezo Kalibrasyonu</h3>
                <div class="controls" style="margin-bottom:12px">
                    <label>Ekran:</label>
                    <select id="telemetry-screen" onchange="renderSelectedTelemetry()"></select>
                    <button class="btn btn-blue" onclick="suggestPiezoThreshold()">Gürültüye göre eşik öner</button>
                    <button class="btn btn-green" onclick="runFieldCheck()">Saha kontrolü</button>
                    <span id="telemetry-summary">İstemciler bekleniyor…</span>
                </div>
                <div id="field-check-result" style="margin:0 0 12px;padding:10px;border-radius:8px;display:none"></div>
                <canvas id="piezo-chart" width="1000" height="150" style="width:100%;height:150px;background:#111827;border-radius:10px"></canvas>
                <div class="screens" id="client-health" style="margin-top:12px"></div>
                <p style="opacity:.72;margin-top:10px">Canlı grafik için Arduino seri hattından <code>PIEZO:123</code> veya <code>RAW:123</code> satırları gönderilmelidir. Öneri yalnızca sliderı değiştirir; Uygula düğmesine basılmadan cihazlara gönderilmez.</p>
            </div>

            <!-- Son Olaylar -->
            <div class="card grid-full">
                <h3>📜 Son Olaylar</h3>
                <div class="history" id="history-list"></div>
            </div>
        </div>
    </div>

    <script>
        const numScreens = 8;
        let lastEventCount = 0;
        let gameProfiles = {};
        let photoCsrf = null;

        // === Ekran kartlarını oluştur ===
        function initScreenCards(count) {
            count = 8;
            const container = document.getElementById('screens');
            container.innerHTML = '';
            for (let i = 1; i <= numScreens; i++) {
                container.innerHTML += `
                    <div class="screen-card" id="screen-${i}-card">
                        <h4>📺 Ekran ${i}</h4>
                        <div class="score" id="screen-${i}-score">0</div>
                        <div class="spawn-pct" id="screen-${i}-quota">0 / — hırsız</div>
                        <div class="spawn-pct" id="screen-${i}-state">Devam ediyor</div>
                        <div class="spawn-pct" id="screen-${i}-pct">Spawn: 0%</div>
                    </div>
                `;
            }
        }

        // === Slider etkileşimi ===
        document.getElementById('piezo-threshold').addEventListener('input', function() {
            document.getElementById('threshold-value').textContent = this.value;
        });
        document.getElementById('piezo-refractory').addEventListener('input', function() {
            document.getElementById('refractory-value').textContent = this.value + 'ms';
        });
        for (const [sliderId, valueId] of [
            ['audio-master', 'audio-master-value'],
            ['audio-music', 'audio-music-value'],
            ['audio-sfx', 'audio-sfx-value'],
        ]) {
            document.getElementById(sliderId).addEventListener('input', function() {
                document.getElementById(valueId).textContent = this.value + '%';
            });
        }

        async function loadPhotoAuth() {
            const statusEl = document.getElementById('photo-auth-status');
            try {
                const response = await fetch('/api/photo-auth/status');
                const data = await response.json();
                photoCsrf = data.authenticated ? data.csrf : null;
                if (!data.configured) {
                    statusEl.innerHTML = 'Fotoğraf galerisi PIN’i Pi 4 üzerinde henüz ayarlanmamış.';
                } else if (!data.authenticated) {
                    statusEl.innerHTML = 'Fotoğraflı oyun için önce <a href="/photos" style="color:#93c5fd">galeriye operatör girişi yapın</a>.';
                } else {
                    statusEl.textContent = 'Fotoğraf yetkisi hazır.';
                }
            } catch (error) {
                photoCsrf = null;
                statusEl.textContent = 'Fotoğraf yetkisi kontrol edilemedi.';
            }
        }

        async function loadGameProfiles() {
            try {
                const response = await fetch('/api/game/profiles'); const data = await response.json(); gameProfiles = data.profiles || {};
                const select = document.getElementById('game-profile');
                select.innerHTML = '<option value="">Özel</option>' + Object.entries(gameProfiles).map(([id,p]) => `<option value="${id}">${p.name || id}</option>`).join('');
            } catch (error) { console.error('Profiller yüklenemedi:', error); }
        }
        function applyGameProfile() {
            const profile = gameProfiles[document.getElementById('game-profile').value]; if (!profile) return;
            document.getElementById('child-count').value = profile.child_count; document.getElementById('screen-count').value = 8;
            document.getElementById('duration-minutes').value = profile.duration_minutes; document.getElementById('difficulty').value = profile.difficulty;
        }
        function quickStart(childCount, screenCount, durationMinutes, difficulty) {
            document.getElementById('child-count').value = childCount;
            document.getElementById('screen-count').value = 8;
            document.getElementById('duration-minutes').value = durationMinutes;
            document.getElementById('difficulty').value = difficulty;
            startGame();
        }

        // === Oyun başlat ===
        async function startGame() {
            const childCount = parseInt(document.getElementById('child-count').value) || 3;
            const screenCount = 8;
            const durationMinutes = parseInt(document.getElementById('duration-minutes').value) || 35;
            const difficulty = document.getElementById('difficulty').value;
            const profileId = document.getElementById('game-profile').value || null;
            const sessionName = document.getElementById('session-name').value.trim();
            const capturePhotos = document.getElementById('capture-photos').checked;
            const photoConsent = document.getElementById('photo-consent').checked;
            const errorBox = document.getElementById('game-start-error');
            errorBox.textContent = '';
            if (capturePhotos && !photoConsent) {
                errorBox.textContent = 'Fotoğraf çekimi için onay kutusunu işaretleyin veya çekimi kapatın.';
                return;
            }
            if (capturePhotos && !photoCsrf) {
                errorBox.innerHTML = 'Fotoğraflı oyun için önce <a href="/photos" style="color:#93c5fd">galeriye operatör girişi yapın</a>.';
                return;
            }

            try {
                const res = await fetch('/api/game/start', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(capturePhotos ? {'X-Photo-CSRF': photoCsrf} : {}),
                    },
                    body: JSON.stringify({
                        profile_id: profileId,
                        child_count: childCount,
                        screen_count: screenCount,
                        duration_minutes: durationMinutes,
                        difficulty: difficulty,
                        session_name: sessionName,
                        capture_photos: capturePhotos,
                        photo_consent: photoConsent,
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Oyun başlatılamadı');
                document.getElementById('target-score').textContent = data.target_score;
                document.getElementById('photo-consent').checked = false;
                initScreenCards(8);
            } catch (err) {
                errorBox.textContent = err.message || 'Oyun başlatılamadı';
                console.error('Oyun başlatma hatası:', err);
            }
        }

        // === Oyun bitir ===
        async function endGame() {
            try {
                await fetch('/api/game/end', {method: 'POST'});
            } catch (err) {
                console.error('Oyun bitirme hatası:', err);
            }
        }

        // === Skor sıfırla ===
        async function resetScores() {
            if (!confirm('Tüm skorları sıfırlamak istediğinize emin misiniz?')) return;
            try {
                await fetch('/reset', {method: 'POST'});
            } catch (err) {
                console.error('Sıfırlama hatası:', err);
            }
        }

        let latestTelemetry = {clients: []};

        function renderSelectedTelemetry() {
            const screenId = parseInt(document.getElementById('telemetry-screen').value) || 1;
            const client = latestTelemetry.clients.find(item => item.screen_id === screenId) || {piezo:{}};
            const samples = client.piezo?.samples || [];
            const canvas = document.getElementById('piezo-chart');
            const chart = canvas.getContext('2d');
            chart.clearRect(0, 0, canvas.width, canvas.height);
            chart.fillStyle = '#111827'; chart.fillRect(0, 0, canvas.width, canvas.height);
            const threshold = parseInt(document.getElementById('piezo-threshold').value) || 0;
            const maxValue = Math.max(1023, threshold, ...samples, 1);
            const thresholdY = canvas.height - threshold / maxValue * canvas.height;
            chart.strokeStyle = '#ef4444'; chart.setLineDash([8, 6]); chart.beginPath();
            chart.moveTo(0, thresholdY); chart.lineTo(canvas.width, thresholdY); chart.stroke(); chart.setLineDash([]);
            if (samples.length) {
                chart.strokeStyle = '#60a5fa'; chart.lineWidth = 3; chart.beginPath();
                samples.forEach((value, index) => {
                    const x = samples.length === 1 ? 0 : index / (samples.length - 1) * canvas.width;
                    const y = canvas.height - value / maxValue * canvas.height;
                    if (index) chart.lineTo(x, y); else chart.moveTo(x, y);
                });
                chart.stroke();
            }
            chart.fillStyle = '#fff'; chart.font = '14px sans-serif';
            chart.fillText(`Ekran ${screenId} · anlık ${client.piezo?.latest || 0} · tepe ${client.piezo?.peak || 0} · eşik ${threshold}`, 14, 22);
        }

        function suggestPiezoThreshold() {
            const screenId = parseInt(document.getElementById('telemetry-screen').value) || 1;
            const client = latestTelemetry.clients.find(item => item.screen_id === screenId);
            const samples = client?.piezo?.samples || [];
            if (samples.length < 5) {
                alert('Eşik önermek için en az 5 canlı sensör örneği gerekli.');
                return;
            }
            const sorted = [...samples].sort((a,b) => a-b);
            const noise95 = sorted[Math.floor((sorted.length - 1) * .95)];
            const suggested = Math.max(10, Math.min(1023, Math.round(noise95 * 1.8 + 10)));
            document.getElementById('piezo-threshold').value = suggested;
            document.getElementById('threshold-value').textContent = suggested;
            renderSelectedTelemetry();
        }

        async function restartClient(screenId) {
            if (!confirm(`Ekran ${screenId} yeniden başlatılsın mı?`)) return;
            try {
                const response = await fetch(`/api/clients/${screenId}/restart`, {method:'POST'});
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Komut gönderilemedi');
                alert(`Ekran ${screenId} yeniden başlatma komutunu aldı.`);
            } catch (error) {
                alert(error.message || 'Client yeniden başlatılamadı');
            }
        }

        async function updateClient(screenId) {
            if (!photoCsrf) {
                alert('Client güncellemek için önce galeriye operatör PIN ile giriş yapın.');
                return;
            }
            if (!confirm(`Ekran ${screenId} güvenli şekilde güncellensin mi? Oyun kısa süre yeniden başlayacak.`)) return;
            try {
                const response = await fetch(`/api/clients/${screenId}/update`, {
                    method: 'POST',
                    headers: {'X-Photo-CSRF': photoCsrf},
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Güncelleme komutu gönderilemedi');
                alert(`Ekran ${screenId} update komutunu aldı. Durumu client kartından izleyebilirsiniz.`);
            } catch (error) {
                alert(error.message || 'Client güncellenemedi');
            }
        }
        async function runFieldCheck() {
            const box = document.getElementById('field-check-result');
            box.style.display = 'block';
            box.style.background = '#1f2937';
            box.textContent = 'Saha kontrolü çalışıyor…';
            try {
                const response = await fetch('/api/field-check');
                const report = await response.json();
                const clientLines = report.clients
                    .filter(client => !client.ready)
                    .map(client => `Ekran ${client.screen_id}: ${client.issues.join(', ')}`);
                const systemLines = [
                    report.audio.ready ? '' : `Ses: ${report.audio.error || 'cihaz kullanılamıyor'}`,
                    report.camera.ready ? '' : `Kamera: ${report.camera.error || 'cihaz kullanılamıyor'}`,
                ].filter(Boolean);
                const issues = [...clientLines, ...systemLines];
                box.style.background = report.ready ? '#14532d' : '#7f1d1d';
                box.innerHTML = `<strong>${report.ready ? 'Saha hazır' : 'Kontrol gerekli'}</strong> · ${report.online_count}/8 client bağlı` +
                    (issues.length ? `<br>${issues.join('<br>')}` : '');
            } catch (error) {
                box.style.background = '#7f1d1d';
                box.textContent = 'Saha kontrolü alınamadı';
            }
        }

        async function loadClientTelemetry() {
            try {
                const response = await fetch('/api/clients/status');
                latestTelemetry = await response.json();
                const select = document.getElementById('telemetry-screen');
                const selected = select.value;
                select.innerHTML = latestTelemetry.clients.map(client =>
                    `<option value="${client.screen_id}">Ekran ${client.screen_id}${client.online ? ' · bağlı' : ' · çevrimdışı'}</option>`
                ).join('');
                if ([...select.options].some(option => option.value === selected)) select.value = selected;
                document.getElementById('telemetry-summary').textContent =
                    `${latestTelemetry.online_count}/${latestTelemetry.clients.length} istemci çevrimiçi`;
                document.getElementById('client-health').innerHTML = latestTelemetry.clients.map(client => `
                    <div class="screen-card" style="border-color:${client.online ? '#22c55e' : '#ef4444'}">
                        <h4>Ekran ${client.screen_id} · ${client.online ? 'Bağlı' : 'Çevrimdışı'}</h4>
                        <div>FPS: ${client.fps ?? '—'} · P95 kare: ${client.frame_time_p95_ms ? client.frame_time_p95_ms + ' ms' : '—'}</div>
                        <div>P95 çizim: ${client.draw_time_p95_ms ?? '—'} ms · kopya: ${client.blit_time_p95_ms ?? '—'} ms · flip: ${client.flip_time_p95_ms ?? '—'} ms</div>
                        <div>RAM: ${client.memory_mb ? client.memory_mb + ' MB' : '—'} · Sıcaklık: ${client.cpu_temp_c != null ? client.cpu_temp_c + ' °C' : '—'}</div>
                        <div>Profil: ${client.performance_profile || '—'} · Kalite: ${client.quality_level || '—'}</div>
                        <div>Render: ${client.render_width && client.render_height ? client.render_width + '×' + client.render_height : '—'} → Çıkış: ${client.output_width && client.output_height ? client.output_width + '×' + client.output_height : '—'} · Direct: ${client.direct_render ? 'Aktif' : 'Kapalı'}</div>
                        <div>Render yolu: ${client.render_mode || '—'} · Güncellenen: ${client.updated_pixel_ratio != null ? client.updated_pixel_ratio + '%' : '—'} · Bölge: ${client.dirty_rect_count ?? 0}</div>
                        <div>Seri: ${client.serial_connected ? 'OK' : 'Yok'} · Sahne: ${client.active_scene || '—'} · Kuyruk: ${client.queue_depth ?? 0}</div>
                        <div>Update: ${client.update_state || 'idle'}${client.update_version ? ' · ' + client.update_version : ''}${client.update_error ? ' · ' + client.update_error : ''}</div>
                        <button class="btn btn-orange" style="margin-top:8px" onclick="restartClient(${client.screen_id})" ${client.online ? '' : 'disabled'}>Client'ı yeniden başlat</button>
                        <button class="btn btn-green" style="margin-top:8px" onclick="updateClient(${client.screen_id})" ${client.online ? '' : 'disabled'}>Güvenli güncelle</button>
                    </div>`).join('');
                renderSelectedTelemetry();
            } catch (error) {
                document.getElementById('telemetry-summary').textContent = 'Telemetri alınamadı';
            }
        }
        // === Piezo ayarla ===
        async function applyPiezoConfig() {
            const threshold = parseInt(document.getElementById('piezo-threshold').value);
            const refractory = parseInt(document.getElementById('piezo-refractory').value);

            try {
                const res = await fetch('/api/piezo/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({threshold: threshold, refractory_ms: refractory}),
                });
                const data = await res.json();
                if (data.success) {
                    alert(`Piezo ayarları güncellendi!\\nThreshold: ${data.threshold}\\nRefractory: ${data.refractory_ms}ms`);
                }
            } catch (err) {
                console.error('Piezo ayar hatası:', err);
            }
        }

        // === Server sesi ===
        function renderAudioStatus(data, syncControls = false) {
            const device = data.device_active || data.device_requested || 'Bulunamadı';
            document.getElementById('audio-device').textContent = device;
            document.getElementById('audio-status').textContent =
                !data.enabled ? 'Kapalı' : (data.available ? 'Hazır' : 'Kullanılamıyor');
            document.getElementById('audio-music-status').textContent =
                data.music_playing ? 'Çalıyor' : 'Durdu';
            document.getElementById('audio-error').textContent = data.last_error || '';

            if (syncControls) {
                document.getElementById('audio-enabled').checked = data.enabled;
                document.getElementById('audio-master').value = Math.round(data.master_volume * 100);
                document.getElementById('audio-music').value = Math.round(data.music_volume * 100);
                document.getElementById('audio-sfx').value = Math.round(data.sfx_volume * 100);
                document.getElementById('audio-master-value').textContent =
                    Math.round(data.master_volume * 100) + '%';
                document.getElementById('audio-music-value').textContent =
                    Math.round(data.music_volume * 100) + '%';
                document.getElementById('audio-sfx-value').textContent =
                    Math.round(data.sfx_volume * 100) + '%';
            }
        }

        async function loadAudioStatus(syncControls = true) {
            try {
                const res = await fetch('/api/audio/status');
                renderAudioStatus(await res.json(), syncControls);
            } catch (err) {
                document.getElementById('audio-error').textContent =
                    'Ses durumu alınamadı';
            }
        }

        async function applyAudioConfig() {
            const payload = {
                enabled: document.getElementById('audio-enabled').checked,
                master_volume: parseInt(document.getElementById('audio-master').value) / 100,
                music_volume: parseInt(document.getElementById('audio-music').value) / 100,
                sfx_volume: parseInt(document.getElementById('audio-sfx').value) / 100,
            };
            try {
                const res = await fetch('/api/audio/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
                renderAudioStatus(await res.json(), true);
            } catch (err) {
                document.getElementById('audio-error').textContent =
                    'Ses ayarları uygulanamadı';
            }
        }

        async function testAudio(soundType) {
            try {
                const res = await fetch('/api/audio/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sound_type: soundType}),
                });
                renderAudioStatus(await res.json(), false);
            } catch (err) {
                document.getElementById('audio-error').textContent =
                    'Ses testi çalıştırılamadı';
            }
        }

        // === Durum güncelleme ===
        async function updateStatus() {
            try {
                // Oyun durumu
                const statusRes = await fetch('/api/game/status');
                const status = await statusRes.json();

                const dot = document.getElementById('game-status-dot');
                const activeEl = document.getElementById('game-active');

                if (status.is_active) {
                    dot.className = 'status-dot status-active';
                    activeEl.textContent = 'Aktif';

                    // Faz
                    const phaseEl = document.getElementById('game-phase');
                    phaseEl.innerHTML = `<span class="phase-badge phase-${status.phase}">${status.phase}</span>`;

                    // Süre
                    const elapsed = status.elapsed_seconds || 0;
                    const mins = Math.floor(elapsed / 60);
                    const secs = elapsed % 60;
                    document.getElementById('elapsed-time').textContent =
                        `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

                    // Spawn bilgisi
                    document.getElementById('spawn-interval').textContent =
                        `${status.spawn_interval?.toFixed(1) || '—'}s`;
                    document.getElementById('total-spawns').textContent =
                        status.total_spawns || 0;

                    // Urgency
                    const urgencyEl = document.getElementById('urgency');
                    urgencyEl.textContent = status.urgency || '—';
                    urgencyEl.className = `stat-value urgency-${status.urgency || 'NORMAL'}`;

                    // Delta
                    document.getElementById('delta').textContent =
                        status.delta !== undefined ? status.delta.toFixed(3) : '—';

                    // Progress
                    const pct = status.progress_percent || 0;
                    document.getElementById('progress-bar').style.width = `${Math.min(100, pct)}%`;
                    document.getElementById('progress-text').textContent = `${pct.toFixed(1)}%`;
                    document.getElementById('target-score').textContent = status.target_score || '—';

                    // Ekran başına bağımsız hırsız kotası
                    const screenTargets = status.screen_targets || {};
                    const screenScores = status.screen_scores || {};
                    const completedScreens = new Set((status.completed_screens || []).map(String));
                    for (let sid = 1; sid <= 8; sid++) {
                        const key = String(sid);
                        const score = Number(screenScores[key] ?? 0);
                        const target = Number(screenTargets[key] ?? 0);
                        const complete = completedScreens.has(key);
                        const scoreEl = document.getElementById(`screen-${sid}-score`);
                        const quotaEl = document.getElementById(`screen-${sid}-quota`);
                        const stateEl = document.getElementById(`screen-${sid}-state`);
                        const cardEl = document.getElementById(`screen-${sid}-card`);
                        if (scoreEl) scoreEl.textContent = score;
                        if (quotaEl) quotaEl.textContent = `${score} / ${target || "—"} hırsız`;
                        if (stateEl) {
                            stateEl.textContent = complete ? "HAPİSTE · TAMAMLANDI" : "Devam ediyor";
                            stateEl.style.color = complete ? "#22c55e" : "";
                        }
                        if (cardEl) {
                            cardEl.style.borderColor = complete ? "#22c55e" : "";
                            cardEl.style.background = complete ? "rgba(34,197,94,.12)" : "";
                        }
                    }

                    // Ekran spawn istatistikleri
                    if (status.screen_stats) {
                        for (const [sid, pctVal] of Object.entries(status.screen_stats)) {
                            const pctEl = document.getElementById(`screen-${sid}-pct`);
                            if (pctEl) pctEl.textContent = `Spawn: ${pctVal}%`;
                        }
                    }
                } else {
                    dot.className = 'status-dot status-inactive';
                    activeEl.textContent = 'Pasif';
                }

                // Skor
                const scoreRes = await fetch('/score');
                const scoreData = await scoreRes.json();

                document.getElementById('total-score').textContent = scoreData.total_score;
                document.getElementById('event-count').textContent = scoreData.event_count;
                document.getElementById('last-event').textContent = scoreData.last_event_time || '—';

                for (const [screenId, score] of Object.entries(scoreData.screen_scores)) {
                    const el = document.getElementById(`screen-${screenId}-score`);
                    if (el) el.textContent = score;
                }

                // Geçmiş
                const histRes = await fetch('/history');
                const histData = await histRes.json();
                const histContainer = document.getElementById('history-list');
                histContainer.innerHTML = '';
                const events = histData.events.slice(-10).reverse();
                events.forEach((event, index) => {
                    const isNew = histData.count > lastEventCount && index === 0;
                    histContainer.innerHTML += `
                        <div class="history-item ${isNew ? 'new' : ''}">
                            <span>📺 Ekran ${event.screen_id}</span>
                            <span>+${event.points} puan</span>
                            <span>${event.time}</span>
                        </div>
                    `;
                });
                lastEventCount = histData.count;

            } catch (err) {
                console.error('Güncelleme hatası:', err);
            }
        }

        // === Piezo ayarlarını yükle ===
        async function loadPiezoConfig() {
            try {
                const res = await fetch('/api/piezo/config');
                const data = await res.json();
                document.getElementById('piezo-threshold').value = data.threshold;
                document.getElementById('threshold-value').textContent = data.threshold;
                document.getElementById('piezo-refractory').value = data.refractory_ms;
                document.getElementById('refractory-value').textContent = data.refractory_ms + 'ms';
            } catch (err) {
                console.error('Piezo config yükleme hatası:', err);
            }
        }

        // === Başlat ===
        initScreenCards(numScreens);
        loadGameProfiles();
        loadPhotoAuth();
        updateStatus();
        loadPiezoConfig();
        loadAudioStatus(true);
        loadClientTelemetry();

        setInterval(updateStatus, 1000);
        setInterval(loadClientTelemetry, 2000);
        setInterval(() => loadAudioStatus(false), 3000);
    </script>
</body>
</html>
"""


SCREEN_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hirsiz Oyunu - Ekran</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        :root {
            --gold: #ffd166;
            --gold-strong: #f7b731;
            --ink: #07111f;
            --panel: rgba(10, 18, 32, 0.72);
            --line: rgba(255, 255, 255, 0.14);
        }
        body {
            min-height: 100vh;
            overflow: hidden;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            color: #fff;
            background:
                radial-gradient(circle at 18% 16%, rgba(255, 209, 102, 0.22), transparent 28%),
                radial-gradient(circle at 84% 18%, rgba(87, 123, 255, 0.18), transparent 30%),
                linear-gradient(145deg, #122018 0%, #081422 46%, #251d44 100%);
        }
        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px);
            background-size: 88px 88px;
            mask-image: linear-gradient(to bottom, black 0%, transparent 78%);
        }
        .stage {
            position: relative;
            width: 100vw;
            height: 100vh;
            display: grid;
            grid-template-rows: auto 1fr;
            padding: clamp(26px, 4vw, 60px);
            isolation: isolate;
        }
        .topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 24px;
            z-index: 5;
        }
        .brand {
            font-size: clamp(34px, 4.3vw, 72px);
            font-weight: 900;
            letter-spacing: 0;
            text-shadow: 0 12px 36px rgba(0,0,0,0.35);
        }
        .status {
            padding: 12px 22px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: rgba(255,255,255,0.1);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.18), 0 12px 32px rgba(0,0,0,0.22);
            font-size: clamp(16px, 1.5vw, 24px);
            backdrop-filter: blur(12px);
        }
        .score-wrap {
            position: relative;
            z-index: 4;
            display: grid;
            place-items: center;
            text-align: center;
            padding-bottom: 17vh;
        }
        .score-card {
            width: min(940px, 78vw);
            padding: clamp(24px, 3vw, 44px);
            border: 1px solid var(--line);
            border-radius: 28px;
            background:
                linear-gradient(180deg, rgba(255,255,255,0.12), rgba(255,255,255,0.035)),
                var(--panel);
            box-shadow: 0 28px 90px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.16);
            backdrop-filter: blur(18px);
        }
        .label {
            font-size: clamp(18px, 1.7vw, 30px);
            color: rgba(255,255,255,0.74);
            text-transform: uppercase;
            font-weight: 900;
            letter-spacing: 0.12em;
        }
        .score {
            font-size: clamp(128px, 19vw, 310px);
            line-height: 0.82;
            font-weight: 1000;
            color: var(--gold);
            text-shadow: 0 20px 70px rgba(247,183,49,0.36), 0 4px 0 rgba(0,0,0,0.12);
            transition: transform 0.25s ease;
        }
        .score.bump { transform: scale(1.08); }
        .target {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-top: 14px;
            padding: 10px 22px;
            border-radius: 999px;
            background: rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.92);
            font-size: clamp(22px, 2.7vw, 46px);
            font-weight: 850;
        }
        .progress {
            width: 100%;
            height: 20px;
            border-radius: 999px;
            background: rgba(0,0,0,0.28);
            overflow: hidden;
            margin-top: 26px;
            box-shadow: inset 0 1px 8px rgba(0,0,0,0.38);
        }
        .bar {
            height: 100%;
            width: 0%;
            border-radius: inherit;
            background: linear-gradient(90deg, #35d07f, var(--gold), #ff6b6b);
            box-shadow: 0 0 24px rgba(255,209,102,0.36);
            transition: width 0.5s ease;
        }
        .meta-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            margin-top: 18px;
            text-align: left;
        }
        .meta {
            min-height: 74px;
            padding: 14px 18px;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 18px;
            background: rgba(0,0,0,0.18);
        }
        .meta span {
            display: block;
            color: rgba(255,255,255,0.58);
            font-size: clamp(12px, 1vw, 16px);
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: 6px;
        }
        .meta strong {
            font-size: clamp(18px, 1.7vw, 28px);
            font-weight: 800;
        }
        .city {
            position: absolute;
            inset: auto 0 0 0;
            height: 34vh;
            z-index: 1;
            background:
                linear-gradient(90deg, transparent 0 5%, rgba(255,255,255,0.07) 5% 6%, transparent 6% 12%),
                linear-gradient(0deg, #06101d 0%, #101b2d 100%);
            background-size: 180px 100%, 100% 100%;
            border-top: 1px solid rgba(255,255,255,0.08);
            opacity: 0.92;
        }
        .road {
            position: absolute;
            left: 0;
            right: 0;
            bottom: 0;
            height: 13vh;
            z-index: 2;
            background: linear-gradient(180deg, #1b1b1f 0%, #090909 100%);
            border-top: 5px solid rgba(255,255,255,0.13);
            box-shadow: 0 -18px 50px rgba(0,0,0,0.26);
        }
        .dash {
            position: absolute;
            top: 48%;
            left: 0;
            width: 200%;
            border-top: 7px dashed rgba(255,209,102,0.94);
            animation: roadMove 1.2s linear infinite;
        }
        .police-shadow {
            position: absolute;
            bottom: 11.8vh;
            left: -260px;
            width: 240px;
            height: 28px;
            z-index: 2;
            border-radius: 50%;
            background: rgba(0,0,0,0.38);
            filter: blur(8px);
            animation: patrol 8s linear infinite;
        }
        .police-car {
            position: absolute;
            bottom: 11.6vh;
            left: -260px;
            width: 238px;
            height: 92px;
            z-index: 3;
            border-radius: 28px 46px 24px 24px;
            background:
                linear-gradient(90deg, #f8fafc 0 45%, #111827 45% 54%, #f8fafc 54% 100%);
            border: 4px solid #e5e7eb;
            box-shadow: 0 18px 42px rgba(0,0,0,0.36), inset 0 -18px 0 rgba(15,23,42,0.1);
            animation: patrol 8s linear infinite;
        }
        .police-car::before {
            content: "";
            position: absolute;
            left: 58px;
            top: -38px;
            width: 116px;
            height: 48px;
            border-radius: 36px 36px 8px 8px;
            background:
                linear-gradient(90deg, #7dd3fc 0 46%, #dbeafe 46% 54%, #7dd3fc 54% 100%);
            border: 4px solid #e5e7eb;
            box-shadow: inset 0 -10px 0 rgba(15,23,42,0.12);
        }
        .wheel {
            position: absolute;
            bottom: -18px;
            width: 46px;
            height: 46px;
            border-radius: 50%;
            background:
                radial-gradient(circle, #cbd5e1 0 18%, #64748b 20% 32%, #020617 34% 67%, #111827 69%);
            border: 5px solid #0f172a;
            box-shadow: inset 0 0 0 3px rgba(255,255,255,0.06), 0 8px 14px rgba(0,0,0,0.32);
        }
        .wheel-left { left: 34px; }
        .wheel-right { right: 34px; }
        .siren-light {
            position: absolute;
            left: 50%;
            top: -58px;
            width: 84px;
            height: 18px;
            z-index: 8;
            display: block;
            border-radius: 999px;
            background: linear-gradient(90deg, #ef4444 0 50%, #3b82f6 50% 100%);
            box-shadow: -22px 0 34px rgba(239,68,68,0.72), 22px 0 34px rgba(59,130,246,0.72);
            transform: translateX(-50%);
            animation: beacon 0.55s steps(2, end) infinite;
        }
        .headlight {
            position: absolute;
            left: -62px;
            bottom: calc(11.6vh + 38px);
            width: 150px;
            height: 52px;
            z-index: 2;
            clip-path: polygon(0 38%, 100% 0, 100% 100%, 0 62%);
            background: linear-gradient(90deg, rgba(255,238,160,0), rgba(255,238,160,0.32));
            filter: blur(2px);
            animation: patrolBeam 8s linear infinite;
        }
        .light {
            position: absolute;
            inset: 0;
            z-index: 0;
            pointer-events: none;
            mix-blend-mode: screen;
            opacity: 0.22;
            background: linear-gradient(90deg, rgba(231,76,60,0.5), transparent 46%, rgba(52,152,219,0.48));
            animation: siren 1.1s steps(2, end) infinite;
        }
        @keyframes patrol {
            0% { transform: translateX(0); }
            48% { transform: translateX(calc(100vw + 290px)); }
            49% { transform: translateX(calc(100vw + 290px)) rotateY(180deg); }
            100% { transform: translateX(0) rotateY(180deg); }
        }
        @keyframes patrolBeam {
            0% { transform: translateX(0); opacity: 0.88; }
            48% { transform: translateX(calc(100vw + 290px)); opacity: 0.88; }
            49% { transform: translateX(calc(100vw + 290px)) rotateY(180deg); opacity: 0.88; }
            100% { transform: translateX(0) rotateY(180deg); opacity: 0.88; }
        }
        @keyframes beacon {
            0%, 100% { filter: brightness(1.35); }
            50% { filter: brightness(0.8); }
        }
        @keyframes roadMove {
            to { transform: translateX(-170px); }
        }
        @keyframes siren {
            0%, 100% { filter: hue-rotate(0deg); }
            50% { filter: hue-rotate(150deg); }
        }
        @media (max-width: 900px) {
            .score-card { width: min(96vw, 720px); }
            .meta-row { grid-template-columns: 1fr; }
            .score-wrap { padding-bottom: 18vh; }
        }
    </style>
</head>
<body>
    <div class="light"></div>
    <div class="stage">
        <div class="topbar">
            <div class="brand">Hirsiz Oyunu</div>
            <div class="status" id="status">Beklemede</div>
        </div>
        <main class="score-wrap">
            <div class="score-card">
                <div class="label">Toplam Skor</div>
                <div class="score" id="score">0</div>
                <div class="target">Hedef: <span id="target">-</span></div>
                <div class="progress"><div class="bar" id="bar"></div></div>
                <div class="meta-row">
                    <div class="meta"><span>Faz</span><strong id="phase">-</strong></div>
                    <div class="meta"><span>Son vurus</span><strong id="last">-</strong></div>
                </div>
            </div>
        </main>
    </div>
    <div class="city"></div>
    <div class="road"><div class="dash"></div></div>
    <div class="headlight"></div>
    <div class="police-shadow"></div>
    <div class="police-car">
        <div class="siren-light"></div>
        <div class="wheel wheel-left"></div>
        <div class="wheel wheel-right"></div>
    </div>
    <script>
        let lastScore = null;
        async function refreshScreen() {
            try {
                const [scoreRes, statusRes, historyRes] = await Promise.all([
                    fetch('/score'),
                    fetch('/api/game/status'),
                    fetch('/history'),
                ]);
                const scoreData = await scoreRes.json();
                const statusData = await statusRes.json();
                const historyData = await historyRes.json();

                const scoreEl = document.getElementById('score');
                if (lastScore !== null && scoreData.total_score !== lastScore) {
                    scoreEl.classList.remove('bump');
                    void scoreEl.offsetWidth;
                    scoreEl.classList.add('bump');
                }
                lastScore = scoreData.total_score;
                scoreEl.textContent = scoreData.total_score;

                const active = Boolean(statusData.is_active);
                document.getElementById('status').textContent = active ? 'Oyun Aktif' : 'Beklemede';
                document.getElementById('phase').textContent = statusData.phase || '-';

                const target = statusData.target_score || statusData.score_data?.target_score || '-';
                document.getElementById('target').textContent = target;

                const pct = statusData.progress_percent || 0;
                document.getElementById('bar').style.width = `${Math.min(100, pct)}%`;

                const latest = (historyData.events || []).slice(-1)[0];
                document.getElementById('last').textContent = latest
                    ? `Ekran ${latest.screen_id} +${latest.points}`
                    : '-';
            } catch (err) {
                document.getElementById('status').textContent = 'Baglanti yok';
            }
        }
        refreshScreen();
        setInterval(refreshScreen, 700);
    </script>
</body>
</html>
"""


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Ana sayfa - Dashboard"""
    return DASHBOARD_HTML


@app.get("/photos", response_class=HTMLResponse)
async def photo_gallery():
    """PIN korumalı API'leri kullanan operatör fotoğraf galerisi."""
    gallery_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "photo_gallery.html",
    )
    try:
        with open(gallery_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        return HTMLResponse(
            content,
            headers={
                "Content-Security-Policy": (
                    "default-src 'self'; img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'self'"
                ),
            },
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Fotoğraf galerisi okunamadı") from exc


@app.get("/screen", response_class=HTMLResponse)
async def screen():
    """Seyir ekrani - animasyonlu skor gorunumu"""
    return SCREEN_HTML


@app.get("/scene-editor", response_class=HTMLResponse)
async def scene_editor():
    """Tarayıcı tabanlı sürükle-bırak Pygame sahne editörü."""
    editor_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "scene_editor.html",
    )
    try:
        with open(editor_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Sahne editörü dosyası okunamadı",
        ) from exc


# ============== Main ==============

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=CONFIG.get("host", "0.0.0.0"),
        port=CONFIG.get("port", 8078),
        reload=CONFIG.get("debug", False),
        access_log=CONFIG.get("access_log", False),
    )
