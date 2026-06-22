#!/usr/bin/env python3
"""
Thief Server - Raspberry Pi 5 için skor toplama + spawn kontrol sunucusu
Tüm client'lardan gelen skorları toplar, spawn zamanlamasını yönetir ve dashboard sunar
"""
import json
import os
import time
import threading
from datetime import datetime
from typing import Dict, Set, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

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
            "num_screens": 12,
            "game_duration_minutes": 45,
            "base_score_per_child": 15,
            "base_spawn_interval": 3.0,
            "min_spawn_interval": 0.5,
            "max_spawn_interval": 8.0,
            "max_concurrent_spawns": 3,
            "default_piezo_threshold": 100,
            "default_piezo_refractory_ms": 200,
            "debug": False,
            "access_log": False,
        }

    config["host"] = os.environ.get("THIEF_HOST", config.get("host", "0.0.0.0"))
    config["port"] = int(os.environ.get("THIEF_PORT", config.get("port", 8078)))
    config["debug"] = os.environ.get("THIEF_DEBUG", str(config.get("debug", False))).lower() in ("1", "true", "yes")
    config["access_log"] = os.environ.get("THIEF_ACCESS_LOG", str(config.get("access_log", False))).lower() in ("1", "true", "yes")
    return config


CONFIG = load_config()


# ============== Models ==============

class ScoreEvent(BaseModel):
    """Client'tan gelen skor eventi"""
    event_id: str
    screen_id: int
    points: int
    ts_ms: int


class ScoreResponse(BaseModel):
    """Skor sorgulama yanıtı"""
    total_score: int
    screen_scores: Dict[int, int]
    event_count: int
    last_event_time: Optional[str]
    score_version: int


class StartGameRequest(BaseModel):
    """Oyun başlatma isteği"""
    child_count: int
    screen_count: int = 12
    difficulty: str = "normal"
    duration_minutes: int = 20


class PiezoConfigRequest(BaseModel):
    """Piezo ayar isteği"""
    threshold: int
    refractory_ms: int


# ============== Score Manager ==============

class ScoreManager:
    """Skor yöneticisi - idempotent event işleme"""

    def __init__(self, num_screens: int = 12):
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

            self.processed_events.add(event.event_id)

            screen_id = event.screen_id
            if 1 <= screen_id <= self.num_screens:
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

score_manager = ScoreManager(num_screens=CONFIG.get("num_screens", 12))
target_calculator = TargetCalculator(
    base_score_per_child=CONFIG.get("base_score_per_child", 15)
)
piezo_config = PiezoConfigManager(
    threshold=CONFIG.get("default_piezo_threshold", 100),
    refractory_ms=CONFIG.get("default_piezo_refractory_ms", 200),
)

# Spawn scheduler (oyun başlatılınca oluşturulur)
spawn_scheduler: Optional[SpawnScheduler] = None

# Global aktif ekran takibi (oyun başlamadan ÖNCE de kaydeder)
import time as _time
active_polling_screens: Dict[int, float] = {}  # screen_id -> last_poll_time
active_polling_lock = threading.RLock()


# ============== FastAPI App ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 50)
    print("🎮 Thief Server Başlatıldı!")
    print(f"📍 http://{CONFIG['host']}:{CONFIG['port']}")
    print(f"📺 Ekran sayısı: {CONFIG['num_screens']}")
    print("=" * 50)
    yield
    global spawn_scheduler
    if spawn_scheduler:
        spawn_scheduler.stop()
    print("\n🛑 Thief Server Kapatıldı")


app = FastAPI(
    title="Thief Game Server",
    description="Hırsız oyunu skor toplama ve spawn kontrol sunucusu",
    version="2.0.0",
    lifespan=lifespan,
)


# ============== Game API Endpoints ==============

@app.post("/api/game/start")
async def start_game(req: StartGameRequest):
    """Yeni oyun başlat"""
    global spawn_scheduler

    # Önceki oyunu durdur
    if spawn_scheduler:
        spawn_scheduler.stop()

    # Hedef hesapla
    duration = req.duration_minutes or CONFIG.get("game_duration_minutes", 20)
    target_info = target_calculator.calculate(req.child_count, req.difficulty, duration)
    target_score = target_info['total_target']

    # Oturum oluştur
    total_secs = duration * 60
    session = GameSession(
        child_count=req.child_count,
        target_score=target_score,
        screen_count=req.screen_count,
        total_seconds=total_secs,
    )

    # Kontrolcüleri oluştur
    screen_selector = ScreenSelector(req.screen_count)
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
    )

    # Oyun başlamadan önce poll yapan ekranları hemen kaydet
    with active_polling_lock:
        active_snapshot = list(active_polling_screens.items())
    for sid, last_t in active_snapshot:
        spawn_scheduler._active_screens[sid] = last_t
        # Dinamik kuyruk oluştur
        if sid not in spawn_scheduler.spawn_queues:
            import queue
            spawn_scheduler.spawn_queues[sid] = queue.Queue()

    if CONFIG.get("debug"):
        print(f"[Game] Kayıtlı aktif ekranlar: {[sid for sid, _ in active_snapshot]}")

    spawn_scheduler.start()

    # Skorları sıfırla
    score_manager.reset()
    if spawn_scheduler:
        spawn_scheduler.reset_score()

    if CONFIG.get("debug"):
        print(f"[Game] Oyun başlatıldı! Çocuk: {req.child_count}, Hedef: {target_score}")

    return {
        "success": True,
        "target_score": target_score,
        "child_count": req.child_count,
        "screen_count": req.screen_count,
        "difficulty": req.difficulty,
        "game_duration_minutes": duration,
    }


@app.get("/api/game/status")
async def game_status():
    """Oyun durumu"""
    if not spawn_scheduler:
        return {"is_active": False, "message": "Oyun başlatılmadı"}

    status = spawn_scheduler.get_status()
    status['score_data'] = score_manager.get_scores().model_dump()
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

        if CONFIG.get("debug"):
            print(f"[Game] Oyun bitti! Skor: {final_score}/{target}")

        return {
            "success": True,
            "final_score": final_score,
            "target_score": target,
            "completed": final_score >= target,
        }

    return {"success": False, "message": "Aktif oyun yok"}


# ============== Spawn Polling ==============

@app.get("/spawn/poll")
async def spawn_poll(screen_id: int = Query(...)):
    """Client spawn kontrolü"""
    # Ekranı GLOBAL olarak kaydet (oyun başlamadan önce de)
    with active_polling_lock:
        active_polling_screens[screen_id] = _time.time()

    if not spawn_scheduler:
        return {
            "spawn": False,
            "game_active": False,
            "score_version": score_manager.get_scores().score_version,
        }

    scheduler_status = spawn_scheduler.get_status()
    if not scheduler_status["is_active"]:
        return {
            "spawn": False,
            "game_active": False,
            "score_version": score_manager.get_scores().score_version,
        }

    result = spawn_scheduler.poll_spawn(screen_id)
    result["game_active"] = True
    result["score_version"] = score_manager.get_scores().score_version
    return result


# ============== Score Endpoints ==============

@app.post("/event")
async def receive_event(event: ScoreEvent):
    """Client'tan skor eventi al"""
    is_new = score_manager.process_event(event)

    # Spawn scheduler'a da bildir
    if is_new and spawn_scheduler and spawn_scheduler.session.is_active:
        spawn_scheduler.update_score(event.points)

    if CONFIG.get("debug"):
        status = "✅ YENİ" if is_new else "⏭️ DUPLICATE"
        print(f"[Event] {status} | Ekran {event.screen_id} | +{event.points} puan")

    return {
        "success": True,
        "is_new": is_new,
        "total_score": score_manager.get_scores().total_score,
    }


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
    }


@app.post("/reset")
async def reset_scores():
    score_manager.reset()
    if spawn_scheduler:
        spawn_scheduler.reset_score()
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
async def poll_piezo_config(screen_id: int = Query(...)):
    """Client piezo config polling"""
    result = piezo_config.poll(screen_id)
    if result:
        return {"changed": True, **result}
    return {"changed": False}


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
        .phase-NORMAL { background: #2ecc71; }
        .phase-INTENSE { background: #e74c3c; }

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

        input[type="number"] {
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
                    <button class="quick-card" onclick="quickStart(2, 5, 6, 'easy')">
                        <strong>Kısa Tur</strong>
                        <span>2 çocuk · 5 ekran · 6 dk · Kolay</span>
                    </button>
                    <button class="quick-card" onclick="quickStart(5, 12, 20, 'normal')">
                        <strong>Standart</strong>
                        <span>5 çocuk · 12 ekran · 20 dk · Normal</span>
                    </button>
                    <button class="quick-card" onclick="quickStart(10, 12, 30, 'hard')">
                        <strong>Yoğun Mod</strong>
                        <span>10 çocuk · 12 ekran · 30 dk · Zor</span>
                    </button>
                </div>
            </div>
        </div>

        <div class="grid">
            <!-- Oyun Kontrol -->
            <div class="card">
                <h3>🎯 Oyun Kontrolü</h3>
                <div class="controls" style="margin-bottom: 15px;">
                    <label>Çocuk:</label>
                    <input type="number" id="child-count" value="3" min="1" max="50">
                    <label>Ekran:</label>
                    <input type="number" id="screen-count" value="12" min="1" max="20">
                    <label>Süre (dk):</label>
                    <input type="number" id="duration-minutes" value="20" min="1" max="120">
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

            <!-- Son Olaylar -->
            <div class="card grid-full">
                <h3>📜 Son Olaylar</h3>
                <div class="history" id="history-list"></div>
            </div>
        </div>
    </div>

    <script>
        let numScreens = 12;
        let lastEventCount = 0;

        // === Ekran kartlarını oluştur ===
        function initScreenCards(count) {
            numScreens = count || 12;
            const container = document.getElementById('screens');
            container.innerHTML = '';
            for (let i = 1; i <= numScreens; i++) {
                container.innerHTML += `
                    <div class="screen-card">
                        <h4>📺 Ekran ${i}</h4>
                        <div class="score" id="screen-${i}-score">0</div>
                        <div class="spawn-pct" id="screen-${i}-pct">0%</div>
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

        function quickStart(childCount, screenCount, durationMinutes, difficulty) {
            document.getElementById('child-count').value = childCount;
            document.getElementById('screen-count').value = screenCount;
            document.getElementById('duration-minutes').value = durationMinutes;
            document.getElementById('difficulty').value = difficulty;
            startGame();
        }

        // === Oyun başlat ===
        async function startGame() {
            const childCount = parseInt(document.getElementById('child-count').value) || 3;
            const screenCount = parseInt(document.getElementById('screen-count').value) || 12;
            const durationMinutes = parseInt(document.getElementById('duration-minutes').value) || 20;
            const difficulty = document.getElementById('difficulty').value;

            try {
                const res = await fetch('/api/game/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        child_count: childCount,
                        screen_count: screenCount,
                        duration_minutes: durationMinutes,
                        difficulty: difficulty,
                    }),
                });
                const data = await res.json();
                document.getElementById('target-score').textContent = data.target_score;
                initScreenCards(screenCount);
            } catch (err) {
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

                    // Ekran spawn istatistikleri
                    if (status.screen_stats) {
                        for (const [sid, pctVal] of Object.entries(status.screen_stats)) {
                            const pctEl = document.getElementById(`screen-${sid}-pct`);
                            if (pctEl) pctEl.textContent = `${pctVal}%`;
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
        updateStatus();
        loadPiezoConfig();

        setInterval(updateStatus, 1000);
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


@app.get("/screen", response_class=HTMLResponse)
async def screen():
    """Seyir ekrani - animasyonlu skor gorunumu"""
    return SCREEN_HTML


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
