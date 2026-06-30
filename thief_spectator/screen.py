#!/usr/bin/env python3
"""
Thief Spectator (Seyir Ekrani) - Hafif pygame skor goruntuleyici.

Sunucudaki tarayici tabanli /screen sayfasinin RAM-dostu pygame karsiligidir.
Dusuk RAM'li cihazlarda (orn. Pi 3 / Pi Zero, 512MB) Chromium acilamadigi icin
bu uygulama ayni JSON uclarini cekip ekrani native olarak cizer:

  GET /score              -> {total_score, screen_scores, ...}
  GET /api/game/status    -> {is_active, phase, target_score, progress_percent, ...}
  GET /history            -> {events: [{screen_id, points, time}, ...]}

Ag istekleri arka plan thread'inde yapilir; render asla bloklanmaz.
Bagimliliklar: yalnizca pygame (HTTP icin stdlib urllib).

Kullanim:
  python screen.py                       # config.json'dan oku
  python screen.py http://192.168.1.10:8078
  THIEF_SERVER_BASE_URL=... python screen.py
"""
import os
import sys
import json
import time
import threading
import urllib.request

import pygame

# ----------------------------------------------------------------- renkler
GOLD = (255, 209, 102)
GOLD_STRONG = (247, 183, 49)
WHITE = (255, 255, 255)
DIM = (190, 198, 210)
DIMMER = (150, 160, 175)
PANEL = (14, 24, 40)
PANEL_LINE = (60, 72, 96)
BG_TOP = (18, 32, 24)
BG_MID = (8, 20, 34)
BG_BOTTOM = (37, 29, 68)
ROAD = (12, 12, 14)
ROAD_LINE = (255, 209, 102)
GREEN = (53, 208, 127)
RED = (239, 68, 68)
BLUE = (59, 130, 246)


def load_config():
    """config.json + env + argv'den ayarlari oku."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = {
        "server_base_url": "http://192.168.1.10:8078",
        "fullscreen": True,
        "fps": 30,
        "poll_interval_ms": 700,
        "screen_width": 1280,
        "screen_height": 720,
    }
    path = os.path.join(script_dir, "config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as e:
            print(f"[Spectator] config.json okunamadi: {e}")

    cfg["server_base_url"] = os.environ.get(
        "THIEF_SERVER_BASE_URL", cfg["server_base_url"]
    ).rstrip("/")
    if len(sys.argv) > 1:
        cfg["server_base_url"] = sys.argv[1].rstrip("/")

    fs_env = os.environ.get("THIEF_FULLSCREEN")
    if fs_env is not None:
        cfg["fullscreen"] = fs_env.lower() in ("1", "true", "yes")
    return cfg


class ServerPoller(threading.Thread):
    """Sunucuyu arka planda yoklayan ve paylasilan durumu guncelleyen thread."""

    def __init__(self, base_url: str, interval_ms: int):
        super().__init__(daemon=True)
        self.base_url = base_url
        self.interval = max(0.1, interval_ms / 1000.0)
        self.max_backoff = 5.0  # Sunucu kapaliyken yoklama araligi tavani (s)
        self._lock = threading.Lock()
        self._running = True
        self._fail_count = 0
        self.state = {
            "connected": False,
            "total_score": 0,
            "is_active": False,
            "phase": "-",
            "target": 0,
            "progress": 0.0,
            "last_hit": "-",
        }

    def stop(self):
        self._running = False

    def get_state(self) -> dict:
        with self._lock:
            return dict(self.state)

    def _fetch(self, path: str, timeout: float = 2.0):
        url = f"{self.base_url}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run(self):
        while self._running:
            try:
                score = self._fetch("/score")
                status = self._fetch("/api/game/status")
                history = self._fetch("/history")

                events = history.get("events") or []
                latest = events[-1] if events else None
                last_hit = (
                    f"Ekran {latest['screen_id']} +{latest['points']}"
                    if latest else "-"
                )
                target = status.get("target_score")
                if not target:
                    target = (status.get("score_data") or {}).get("target_score", 0)

                with self._lock:
                    self.state.update({
                        "connected": True,
                        "total_score": int(score.get("total_score", 0)),
                        "is_active": bool(status.get("is_active", False)),
                        "phase": status.get("phase", "-") or "-",
                        "target": int(target or 0),
                        "progress": float(status.get("progress_percent", 0) or 0),
                        "last_hit": last_hit,
                    })
                self._fail_count = 0
                sleep_for = self.interval
            except Exception:
                with self._lock:
                    self.state["connected"] = False
                # Sunucu kapaliyken ustel backoff (tavanli) — bosa yoklama yapma
                self._fail_count = min(self._fail_count + 1, 8)
                sleep_for = min(self.max_backoff, self.interval * (2 ** self._fail_count))
            time.sleep(sleep_for)


class Spectator:
    """Pygame seyir ekrani."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Kiosk: ekran koruyucunun ekrani karartmasini engelle
        os.environ.setdefault("SDL_VIDEO_ALLOW_SCREENSAVER", "0")
        pygame.init()
        pygame.mouse.set_visible(False)

        try:
            if cfg["fullscreen"]:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode(
                    (cfg["screen_width"], cfg["screen_height"])
                )
        except pygame.error as e:
            print(f"[Spectator] Ekran acilamadi: {e}")
            print("  - Konsoldan calistiriyorsaniz SDL_VIDEODRIVER=kmsdrm deneyin.")
            print("  - Masaustunde iseniz DISPLAY=:0 ayarli olmali.")
            raise
        self.w, self.h = self.screen.get_size()
        pygame.display.set_caption("Hirsiz Oyunu - Seyir Ekrani")

        self.clock = pygame.time.Clock()
        self.fps = cfg.get("fps", 30)

        # Fontlar (ekran yuksekligine gore olcekli)
        sh = self.h
        self.f_brand = pygame.font.Font(None, max(28, int(sh * 0.075)))
        self.f_status = pygame.font.Font(None, max(18, int(sh * 0.038)))
        self.f_label = pygame.font.Font(None, max(16, int(sh * 0.034)))
        self.f_score = pygame.font.Font(None, max(80, int(sh * 0.34)))
        self.f_target = pygame.font.Font(None, max(24, int(sh * 0.052)))
        self.f_meta_l = pygame.font.Font(None, max(14, int(sh * 0.026)))
        self.f_meta_v = pygame.font.Font(None, max(18, int(sh * 0.04)))

        # Statik arka plan (bir kez)
        self.background = self._make_background()

        # Skor bump animasyonu
        self.prev_score = None
        self.bump = 0.0
        # Onbellekli skor yuzeyi
        self._score_cache_val = None
        self._score_cache_surf = None

        self.poller = ServerPoller(cfg["server_base_url"], cfg["poll_interval_ms"])
        self.poller.start()
        self.running = True

    def _make_background(self):
        """Dikey gradyan + zemin (yol) - bir kez olusturulur."""
        bg = pygame.Surface((self.w, self.h))
        # Ust 60% gradyan: BG_TOP -> BG_MID, alt 40%: BG_MID -> BG_BOTTOM
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            if t < 0.5:
                k = t / 0.5
                c = [int(BG_TOP[i] + (BG_MID[i] - BG_TOP[i]) * k) for i in range(3)]
            else:
                k = (t - 0.5) / 0.5
                c = [int(BG_MID[i] + (BG_BOTTOM[i] - BG_MID[i]) * k) for i in range(3)]
            pygame.draw.line(bg, c, (0, y), (self.w, y))
        return bg

    # ----------------------------------------------------------------- loop
    def run(self):
        road_offset = 0.0
        car_period = 8.0  # saniye: bir gecis
        try:
            while self.running:
                dt = self.clock.tick(self.fps) / 1000.0
                self._handle_events()

                state = self.poller.get_state()

                # Skor degisiminde bump
                score = state["total_score"]
                if self.prev_score is not None and score != self.prev_score:
                    self.bump = 1.0
                self.prev_score = score
                if self.bump > 0:
                    self.bump = max(0.0, self.bump - dt * 4.0)

                road_offset = (road_offset + dt * 140.0) % 170

                self.screen.blit(self.background, (0, 0))
                self._draw_road(road_offset)
                self._draw_police_car(time.time() % car_period / car_period)
                self._draw_hud(state)
                pygame.display.flip()
        finally:
            self.poller.stop()
            pygame.quit()

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()

    # ----------------------------------------------------------------- cizim
    def _draw_road(self, offset: float):
        road_h = int(self.h * 0.13)
        city_h = int(self.h * 0.30)
        ry = self.h - road_h
        # Sehir silueti (basit koyu bant)
        pygame.draw.rect(self.screen, (10, 18, 30), (0, self.h - city_h, self.w, city_h))
        # Yol
        pygame.draw.rect(self.screen, ROAD, (0, ry, self.w, road_h))
        pygame.draw.line(self.screen, (70, 78, 90), (0, ry), (self.w, ry), 3)
        # Kesik orta cizgi (kayan)
        dash_y = ry + road_h // 2
        x = -offset
        while x < self.w:
            pygame.draw.line(self.screen, ROAD_LINE, (x, dash_y), (x + 90, dash_y), 6)
            x += 170

    def _draw_police_car(self, phase: float):
        """Yol uzerinde gidip gelen basit polis arabasi."""
        road_h = int(self.h * 0.13)
        baseline = self.h - road_h + int(road_h * 0.18)
        car_w = max(160, int(self.w * 0.16))
        car_h = int(car_w * 0.38)

        travel = self.w + car_w * 2
        if phase < 0.5:
            x = -car_w + travel * (phase / 0.5)
            facing = 1
        else:
            x = (self.w + car_w) - travel * ((phase - 0.5) / 0.5)
            facing = -1
        x = int(x)
        y = baseline - car_h

        body = pygame.Rect(x, y, car_w, car_h)
        # Govde (beyaz) + orta siyah bant
        pygame.draw.rect(self.screen, (245, 247, 250), body, border_radius=int(car_h * 0.35))
        band = pygame.Rect(x + car_w // 2 - car_w // 18, y, car_w // 9, car_h)
        pygame.draw.rect(self.screen, (20, 26, 38), band)
        # Cam
        cab = pygame.Rect(x + int(car_w * 0.24), y - int(car_h * 0.45),
                          int(car_w * 0.5), int(car_h * 0.5))
        pygame.draw.rect(self.screen, (125, 211, 252), cab, border_radius=int(car_h * 0.25))
        # Tekerlekler
        wr = max(10, int(car_h * 0.32))
        wy = y + car_h
        for wx in (x + int(car_w * 0.22), x + int(car_w * 0.78)):
            pygame.draw.circle(self.screen, (15, 18, 26), (wx, wy), wr)
            pygame.draw.circle(self.screen, (120, 130, 145), (wx, wy), wr // 2)
        # Siren (kirmizi/mavi yanip soner)
        blink = int(time.time() * 3) % 2 == 0
        sir_w = int(car_w * 0.16)
        sir_h = max(5, int(car_h * 0.16))
        sx = x + car_w // 2 - sir_w
        sy = cab.top - sir_h - 4
        left_col = RED if blink else BLUE
        right_col = BLUE if blink else RED
        pygame.draw.rect(self.screen, left_col, (sx, sy, sir_w, sir_h))
        pygame.draw.rect(self.screen, right_col, (sx + sir_w, sy, sir_w, sir_h))

    def _status_pill(self, text: str, color, cx_right: int, top: int):
        surf = self.f_status.render(text, True, WHITE)
        pad_x, pad_y = 22, 12
        w = surf.get_width() + pad_x * 2
        h = surf.get_height() + pad_y * 2
        rect = pygame.Rect(cx_right - w, top, w, h)
        pill = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(pill, (*color, 60), (0, 0, w, h), border_radius=h // 2)
        pygame.draw.rect(pill, (*color, 220), (0, 0, w, h), width=2, border_radius=h // 2)
        self.screen.blit(pill, rect.topleft)
        self.screen.blit(surf, (rect.x + pad_x, rect.y + pad_y))

    def _score_surface(self, score: int):
        if score != self._score_cache_val:
            self._score_cache_val = score
            self._score_cache_surf = self.f_score.render(str(score), True, GOLD)
        return self._score_cache_surf

    def _draw_hud(self, state: dict):
        pad = int(self.h * 0.05)

        # Ust bar: marka + durum
        brand = self.f_brand.render("HIRSIZ OYUNU", True, WHITE)
        self.screen.blit(brand, (pad, pad))

        if not state["connected"]:
            self._status_pill("Baglanti yok", RED, self.w - pad, pad)
        elif state["is_active"]:
            self._status_pill("Oyun Aktif", GREEN, self.w - pad, pad)
        else:
            self._status_pill("Beklemede", DIMMER, self.w - pad, pad)

        # Merkez panel
        center_x = self.w // 2
        cy = int(self.h * 0.30)

        label = self.f_label.render("TOPLAM SKOR", True, DIM)
        self.screen.blit(label, (center_x - label.get_width() // 2, cy))

        # Skor (bump ile olceklenir)
        score_surf = self._score_surface(state["total_score"])
        if self.bump > 0:
            scale = 1.0 + 0.08 * self.bump
            sw = int(score_surf.get_width() * scale)
            sh = int(score_surf.get_height() * scale)
            score_surf = pygame.transform.smoothscale(score_surf, (sw, sh))
        sy = cy + label.get_height() + int(self.h * 0.01)
        self.screen.blit(score_surf, (center_x - score_surf.get_width() // 2, sy))

        # Hedef
        target = state["target"]
        target_txt = f"Hedef: {target}" if target else "Hedef: -"
        tsurf = self.f_target.render(target_txt, True, WHITE)
        ty = sy + score_surf.get_height() + int(self.h * 0.015)
        self.screen.blit(tsurf, (center_x - tsurf.get_width() // 2, ty))

        # Ilerleme cubugu
        bar_w = int(self.w * 0.5)
        bar_h = max(12, int(self.h * 0.022))
        bx = center_x - bar_w // 2
        by = ty + tsurf.get_height() + int(self.h * 0.02)
        pygame.draw.rect(self.screen, (0, 0, 0), (bx, by, bar_w, bar_h), border_radius=bar_h // 2)
        pct = max(0.0, min(100.0, state["progress"])) / 100.0
        fill_w = int(bar_w * pct)
        if fill_w > 0:
            # Yesil -> altin -> kirmizi gecisi (basit: pct'ye gore renk)
            if pct < 0.5:
                k = pct / 0.5
                col = [int(GREEN[i] + (GOLD[i] - GREEN[i]) * k) for i in range(3)]
            else:
                k = (pct - 0.5) / 0.5
                col = [int(GOLD[i] + (RED[i] - GOLD[i]) * k) for i in range(3)]
            pygame.draw.rect(self.screen, col, (bx, by, fill_w, bar_h), border_radius=bar_h // 2)

        # Meta kutular: Faz + Son vurus
        my = by + bar_h + int(self.h * 0.03)
        box_w = int(bar_w * 0.49)
        box_h = int(self.h * 0.1)
        gap = bar_w - box_w * 2
        self._meta_box(bx, my, box_w, box_h, "FAZ", str(state["phase"]))
        self._meta_box(bx + box_w + gap, my, box_w, box_h, "SON VURUS", str(state["last_hit"]))

    def _meta_box(self, x, y, w, h, label, value):
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(box, (0, 0, 0, 60), (0, 0, w, h), border_radius=16)
        pygame.draw.rect(box, (*PANEL_LINE, 160), (0, 0, w, h), width=1, border_radius=16)
        self.screen.blit(box, (x, y))
        lab = self.f_meta_l.render(label, True, DIMMER)
        self.screen.blit(lab, (x + 16, y + 12))
        val = self.f_meta_v.render(value, True, WHITE)
        self.screen.blit(val, (x + 16, y + 12 + lab.get_height() + 6))


def main():
    cfg = load_config()
    print(f"[Spectator] Sunucu: {cfg['server_base_url']}")
    try:
        Spectator(cfg).run()
    except KeyboardInterrupt:
        print("\n[Spectator] Kapatiliyor...")
    except Exception as e:
        print(f"[Spectator] Hata: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
