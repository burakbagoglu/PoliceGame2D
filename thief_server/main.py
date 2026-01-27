#!/usr/bin/env python3
"""
Thief Server - Raspberry Pi 5 için skor toplama sunucusu
Tüm client'lardan gelen skorları toplar ve dashboard sunar
"""
import json
import os
import time
from datetime import datetime
from typing import Dict, Set, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# ============== Config ==============

def load_config(filepath: str = "config.json") -> dict:
    """Config dosyasını yükle"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, filepath)
    
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    return {
        "host": "0.0.0.0",
        "port": 8000,
        "num_screens": 5,
        "debug": True
    }


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


# ============== Score Manager ==============

class ScoreManager:
    """Skor yöneticisi - idempotent event işleme"""
    
    def __init__(self, num_screens: int = 5):
        self.num_screens = num_screens
        
        # Ekran bazlı skorlar
        self.screen_scores: Dict[int, int] = {i: 0 for i in range(1, num_screens + 1)}
        
        # Toplam skor
        self.total_score: int = 0
        
        # İşlenmiş event ID'leri (idempotency için)
        self.processed_events: Set[str] = set()
        
        # Event sayacı
        self.event_count: int = 0
        
        # Son event zamanı
        self.last_event_time: Optional[datetime] = None
        
        # Event geçmişi (son 100 event)
        self.event_history: list = []
        self.max_history = 100
    
    def process_event(self, event: ScoreEvent) -> bool:
        """
        Event'i işle
        
        Returns:
            True: Event işlendi (yeni)
            False: Event zaten işlenmiş (duplicate)
        """
        # Idempotency kontrolü
        if event.event_id in self.processed_events:
            return False
        
        # Event ID'yi kaydet
        self.processed_events.add(event.event_id)
        
        # Skoru güncelle
        screen_id = event.screen_id
        if 1 <= screen_id <= self.num_screens:
            self.screen_scores[screen_id] += event.points
            self.total_score += event.points
        
        # İstatistikleri güncelle
        self.event_count += 1
        self.last_event_time = datetime.now()
        
        # Geçmişe ekle
        self.event_history.append({
            "event_id": event.event_id[:8] + "...",
            "screen_id": event.screen_id,
            "points": event.points,
            "time": self.last_event_time.strftime("%H:%M:%S")
        })
        
        # Geçmiş limitini kontrol et
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)
        
        return True
    
    def get_scores(self) -> ScoreResponse:
        """Mevcut skorları döndür"""
        return ScoreResponse(
            total_score=self.total_score,
            screen_scores=self.screen_scores,
            event_count=self.event_count,
            last_event_time=self.last_event_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_event_time else None
        )
    
    def reset(self):
        """Skorları sıfırla"""
        self.screen_scores = {i: 0 for i in range(1, self.num_screens + 1)}
        self.total_score = 0
        self.processed_events.clear()
        self.event_count = 0
        self.last_event_time = None
        self.event_history.clear()


# ============== Global Score Manager ==============

score_manager = ScoreManager(num_screens=CONFIG.get("num_screens", 5))


# ============== FastAPI App ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama yaşam döngüsü"""
    print("=" * 50)
    print("🎮 Thief Server Başlatıldı!")
    print(f"📍 http://{CONFIG['host']}:{CONFIG['port']}")
    print(f"📺 Ekran sayısı: {CONFIG['num_screens']}")
    print("=" * 50)
    yield
    print("\n🛑 Thief Server Kapatıldı")


app = FastAPI(
    title="Thief Game Server",
    description="Hırsız oyunu skor toplama sunucusu",
    version="1.0.0",
    lifespan=lifespan
)


# ============== API Endpoints ==============

@app.post("/event")
async def receive_event(event: ScoreEvent):
    """
    Client'tan skor eventi al
    
    Idempotent: Aynı event_id tekrar gelirse ignore edilir
    """
    is_new = score_manager.process_event(event)
    
    if CONFIG.get("debug"):
        status = "✅ YENİ" if is_new else "⏭️ DUPLICATE"
        print(f"[Event] {status} | Ekran {event.screen_id} | +{event.points} puan")
    
    return {
        "success": True,
        "is_new": is_new,
        "total_score": score_manager.total_score
    }


@app.get("/score", response_model=ScoreResponse)
async def get_score():
    """Mevcut skorları getir"""
    return score_manager.get_scores()


@app.get("/score/screen/{screen_id}")
async def get_screen_score(screen_id: int):
    """Belirli bir ekranın skorunu getir"""
    if screen_id < 1 or screen_id > score_manager.num_screens:
        raise HTTPException(status_code=404, detail=f"Ekran {screen_id} bulunamadı")
    
    return {
        "screen_id": screen_id,
        "score": score_manager.screen_scores[screen_id]
    }


@app.post("/reset")
async def reset_scores():
    """Tüm skorları sıfırla"""
    score_manager.reset()
    
    if CONFIG.get("debug"):
        print("🔄 Skorlar sıfırlandı!")
    
    return {"success": True, "message": "Skorlar sıfırlandı"}


@app.get("/history")
async def get_history():
    """Son event'leri getir"""
    return {
        "events": score_manager.event_history,
        "count": len(score_manager.event_history)
    }


@app.get("/health")
async def health_check():
    """Sağlık kontrolü"""
    return {
        "status": "healthy",
        "uptime": "ok",
        "total_score": score_manager.total_score
    }


# ============== Dashboard ==============

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎮 Hırsız Oyunu - Skor Tablosu</title>
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
            max-width: 1200px;
            margin: 0 auto;
        }
        
        h1 {
            text-align: center;
            font-size: 3rem;
            margin-bottom: 30px;
            text-shadow: 0 0 20px rgba(255, 215, 0, 0.5);
        }
        
        .total-score {
            text-align: center;
            background: linear-gradient(135deg, #ff6b6b, #feca57);
            padding: 30px;
            border-radius: 20px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(255, 107, 107, 0.3);
        }
        
        .total-score h2 {
            font-size: 1.5rem;
            opacity: 0.9;
        }
        
        .total-score .score {
            font-size: 6rem;
            font-weight: bold;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        
        .screens {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .screen-card {
            background: rgba(255, 255, 255, 0.1);
            padding: 25px;
            border-radius: 15px;
            text-align: center;
            backdrop-filter: blur(10px);
            transition: transform 0.3s, box-shadow 0.3s;
        }
        
        .screen-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }
        
        .screen-card h3 {
            font-size: 1.2rem;
            margin-bottom: 10px;
            opacity: 0.8;
        }
        
        .screen-card .score {
            font-size: 3rem;
            font-weight: bold;
            color: #feca57;
        }
        
        .stats {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
        }
        
        .stats h3 {
            margin-bottom: 15px;
            opacity: 0.8;
        }
        
        .stats p {
            margin: 8px 0;
            font-size: 1.1rem;
        }
        
        .history {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 15px;
            max-height: 300px;
            overflow-y: auto;
        }
        
        .history h3 {
            margin-bottom: 15px;
            opacity: 0.8;
        }
        
        .history-item {
            padding: 10px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .history-item.new {
            animation: highlight 1s ease;
        }
        
        @keyframes highlight {
            0% { background: rgba(255, 215, 0, 0.5); }
            100% { background: rgba(255, 255, 255, 0.05); }
        }
        
        .btn {
            background: #e74c3c;
            color: white;
            border: none;
            padding: 12px 25px;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
            transition: background 0.3s;
        }
        
        .btn:hover {
            background: #c0392b;
        }
        
        .actions {
            text-align: center;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎮 Hırsız Oyunu</h1>
        
        <div class="total-score">
            <h2>TOPLAM SKOR</h2>
            <div class="score" id="total-score">0</div>
        </div>
        
        <div class="screens" id="screens">
            <!-- Ekran kartları JS ile doldurulacak -->
        </div>
        
        <div class="stats">
            <h3>📊 İstatistikler</h3>
            <p>Toplam Event: <strong id="event-count">0</strong></p>
            <p>Son Event: <strong id="last-event">-</strong></p>
        </div>
        
        <div class="history">
            <h3>📜 Son Olaylar</h3>
            <div id="history-list">
                <!-- Geçmiş JS ile doldurulacak -->
            </div>
        </div>
        
        <div class="actions">
            <button class="btn" onclick="resetScores()">🔄 Skorları Sıfırla</button>
        </div>
    </div>
    
    <script>
        const NUM_SCREENS = 5;
        let lastEventCount = 0;
        
        // Ekran kartlarını oluştur
        function initScreenCards() {
            const container = document.getElementById('screens');
            for (let i = 1; i <= NUM_SCREENS; i++) {
                container.innerHTML += `
                    <div class="screen-card">
                        <h3>📺 Ekran ${i}</h3>
                        <div class="score" id="screen-${i}-score">0</div>
                    </div>
                `;
            }
        }
        
        // Skorları güncelle
        async function updateScores() {
            try {
                const response = await fetch('/score');
                const data = await response.json();
                
                // Toplam skor
                document.getElementById('total-score').textContent = data.total_score;
                
                // Ekran skorları
                for (const [screenId, score] of Object.entries(data.screen_scores)) {
                    const el = document.getElementById(`screen-${screenId}-score`);
                    if (el) el.textContent = score;
                }
                
                // İstatistikler
                document.getElementById('event-count').textContent = data.event_count;
                document.getElementById('last-event').textContent = data.last_event_time || '-';
                
            } catch (err) {
                console.error('Skor güncelleme hatası:', err);
            }
        }
        
        // Geçmişi güncelle
        async function updateHistory() {
            try {
                const response = await fetch('/history');
                const data = await response.json();
                
                const container = document.getElementById('history-list');
                container.innerHTML = '';
                
                // Son 10 event'i göster (tersten)
                const events = data.events.slice(-10).reverse();
                
                events.forEach((event, index) => {
                    const isNew = data.count > lastEventCount && index === 0;
                    container.innerHTML += `
                        <div class="history-item ${isNew ? 'new' : ''}">
                            <span>📺 Ekran ${event.screen_id}</span>
                            <span>+${event.points} puan</span>
                            <span>${event.time}</span>
                        </div>
                    `;
                });
                
                lastEventCount = data.count;
                
            } catch (err) {
                console.error('Geçmiş güncelleme hatası:', err);
            }
        }
        
        // Skorları sıfırla
        async function resetScores() {
            if (!confirm('Tüm skorları sıfırlamak istediğinize emin misiniz?')) {
                return;
            }
            
            try {
                await fetch('/reset', { method: 'POST' });
                updateScores();
                updateHistory();
            } catch (err) {
                console.error('Sıfırlama hatası:', err);
            }
        }
        
        // Başlat
        initScreenCards();
        updateScores();
        updateHistory();
        
        // Her saniye güncelle
        setInterval(() => {
            updateScores();
            updateHistory();
        }, 1000);
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Ana sayfa - Dashboard"""
    return DASHBOARD_HTML


# ============== Main ==============

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host=CONFIG.get("host", "0.0.0.0"),
        port=CONFIG.get("port", 8000),
        reload=CONFIG.get("debug", False)
    )
