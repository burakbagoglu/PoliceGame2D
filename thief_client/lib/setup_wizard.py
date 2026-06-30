"""
Setup Wizard - Klavye ile kontrol edilen, canlı önizlemeli kurulum ekranı.

İlk açılışta (config 'installed' değilse) veya oyun içinde S tuşuyla açılır.
Pleksi/oynanabilir alan, hırsız boyutu/hızı, band ve genel ayarları düzenler,
config sözlüğüne yazar. Hesaplanan oynanabilir alan canlı olarak önizlenir.

Kontroller:
  Yukarı / Aşağı : alan seç
  Sol / Sağ veya - / + : değeri değiştir (basılı tutunca hızlanır)
  Enter : kaydet ve çık
  Esc   : iptal (kaydetmeden çık)
"""
import copy
import os
import pygame

from lib.playarea import PlayAreaConfig
from lib.animation import SpriteSheet


# ---- Renkler ----
COL_BG = (18, 20, 26)
COL_PANEL = (28, 32, 42)
COL_PANEL_BORDER = (70, 80, 100)
COL_TEXT = (220, 224, 230)
COL_DIM = (130, 138, 150)
COL_SECTION = (120, 180, 255)
COL_SELECT_BG = (52, 64, 92)
COL_VALUE = (255, 220, 120)
COL_PLAY_FILL = (40, 44, 52)
COL_PLAY_BORDER = (90, 200, 120)
COL_BAND = (255, 255, 0)
COL_GROUND = (90, 110, 140)


class WizField:
    """Düzenlenebilir tek bir ayar alanı."""

    def __init__(self, path, label, kind, *, default=None, step=1, vmin=None, vmax=None,
                 options=None, unit="", decimals=0, visible=None, section=None):
        self.path = path              # list[str], örn. ["playarea", "plexi_width_cm"]
        self.label = label
        self.kind = kind              # "bool" | "int" | "float" | "enum"
        self.default = default
        self.step = step
        self.vmin = vmin
        self.vmax = vmax
        self.options = options or []
        self.unit = unit
        self.decimals = decimals
        self.visible_fn = visible     # lambda cfg -> bool
        self.section = section        # bölüm başlığı (ilk alanda)

    def is_visible(self, cfg) -> bool:
        return self.visible_fn(cfg) if self.visible_fn else True

    def get(self, cfg):
        node = cfg
        for key in self.path[:-1]:
            node = node.setdefault(key, {})
        value = node.get(self.path[-1])
        if value is None:
            return self.default
        return value

    def set(self, cfg, value):
        node = cfg
        for key in self.path[:-1]:
            node = node.setdefault(key, {})
        node[self.path[-1]] = value

    def _clamp(self, value):
        if self.vmin is not None and value < self.vmin:
            value = self.vmin
        if self.vmax is not None and value > self.vmax:
            value = self.vmax
        return value

    def adjust(self, cfg, direction: int):
        value = self.get(cfg)
        if self.kind == "bool":
            self.set(cfg, not bool(value))
        elif self.kind == "enum":
            try:
                idx = self.options.index(value)
            except ValueError:
                idx = 0
            idx = (idx + direction) % len(self.options)
            self.set(cfg, self.options[idx])
        elif self.kind == "int":
            self.set(cfg, int(self._clamp(int(value) + self.step * direction)))
        elif self.kind == "float":
            new = round(float(value) + self.step * direction, 4)
            self.set(cfg, self._clamp(new))

    def display_value(self, cfg) -> str:
        value = self.get(cfg)
        if self.kind == "bool":
            return "AÇIK" if value else "KAPALI"
        if self.kind == "enum":
            labels = {
                "physical": "Fiziksel (inç+cm)",
                "manual_px": "Manuel (px)",
                "center": "Orta", "left": "Sol", "right": "Sağ",
                "top": "Üst", "bottom": "Alt", "custom": "Özel",
            }
            return labels.get(value, str(value))
        if self.kind == "float":
            txt = f"{float(value):.{self.decimals}f}"
        else:
            txt = str(int(value))
        return f"{txt} {self.unit}".strip()


def _build_fields(screen_w=1920, screen_h=1080):
    """Sihirbaz alanlarını tanımla."""
    is_phys = lambda c: c.get("playarea", {}).get("mode", "physical") == "physical"
    is_manual = lambda c: c.get("playarea", {}).get("mode") == "manual_px"
    is_enabled = lambda c: bool(c.get("playarea", {}).get("enabled", False))
    band_on = lambda c: bool(c.get("band_enabled", False))

    phys = lambda c: is_enabled(c) and is_phys(c)
    custom_x = lambda c: phys(c) and c.get("playarea", {}).get("align_x") == "custom"
    custom_y = lambda c: phys(c) and c.get("playarea", {}).get("align_y") == "custom"
    manual = lambda c: is_enabled(c) and is_manual(c)

    def_w = int(screen_w * 0.7)
    def_h = int(screen_h * 0.7)

    return [
        WizField(["playarea", "enabled"], "Pleksi alanı kullan", "bool", default=False,
                 section="PLEKSİ / OYNANABİLİR ALAN"),
        WizField(["playarea", "mode"], "Ölçü modu", "enum", default="physical",
                 options=["physical", "manual_px"], visible=is_enabled),
        WizField(["playarea", "screen_diagonal_in"], "Ekran köşegeni", "float", default=24.0,
                 step=0.5, vmin=1, unit="inç", decimals=1, visible=phys),
        WizField(["playarea", "plexi_width_cm"], "Pleksi genişlik", "float", default=50.0,
                 step=0.5, vmin=1, unit="cm", decimals=1, visible=phys),
        WizField(["playarea", "plexi_height_cm"], "Pleksi yükseklik", "float", default=30.0,
                 step=0.5, vmin=1, unit="cm", decimals=1, visible=phys),
        WizField(["playarea", "align_x"], "Yatay hizalama", "enum", default="center",
                 options=["center", "left", "right", "custom"], visible=phys),
        WizField(["playarea", "margin_left_cm"], "Soldan boşluk", "float", default=0.0,
                 step=0.5, vmin=0, unit="cm", decimals=1, visible=custom_x),
        WizField(["playarea", "align_y"], "Dikey hizalama", "enum", default="center",
                 options=["center", "top", "bottom", "custom"], visible=phys),
        WizField(["playarea", "margin_top_cm"], "Üstten boşluk", "float", default=0.0,
                 step=0.5, vmin=0, unit="cm", decimals=1, visible=custom_y),
        WizField(["playarea", "x"], "X konum", "int", default=(screen_w - def_w) // 2,
                 step=5, vmin=0, unit="px", visible=manual),
        WizField(["playarea", "y"], "Y konum", "int", default=(screen_h - def_h) // 2,
                 step=5, vmin=0, unit="px", visible=manual),
        WizField(["playarea", "width"], "Genişlik", "int", default=def_w,
                 step=10, vmin=10, unit="px", visible=manual),
        WizField(["playarea", "height"], "Yükseklik", "int", default=def_h,
                 step=10, vmin=10, unit="px", visible=manual),

        WizField(["thief_scale"], "Hırsız boyutu", "int", default=4, step=1, vmin=1, vmax=16,
                 section="HIRSIZ"),
        WizField(["thief_speed_px_s"], "Hız", "float", default=360, step=20, vmin=20, unit="px/s", decimals=0),
        WizField(["thief_ground_pct"], "Zemin çizgisi", "float", default=95, step=1, vmin=0, vmax=100, unit="%", decimals=0),
        WizField(["anim_fps"], "Animasyon FPS", "int", default=12, step=1, vmin=1, vmax=30),

        WizField(["band_enabled"], "Hedef band", "bool", default=False, section="HEDEF BAND"),
        WizField(["band_center_pct"], "Band merkezi", "float", default=50, step=1, vmin=0, vmax=100, unit="%", decimals=0, visible=band_on),
        WizField(["band_width_px"], "Band genişliği", "int", default=120, step=10, vmin=10, unit="px", visible=band_on),

        WizField(["screen_id"], "Ekran ID", "int", default=1, step=1, vmin=1, section="GENEL"),
        WizField(["random_direction"], "Rastgele yön", "bool", default=False),
        WizField(["hit_cooldown_ms"], "Vuruş bekleme", "int", default=200, step=50, vmin=0, unit="ms"),
    ]


class SetupWizard:
    """Klavye ile kontrol edilen kurulum ekranı."""

    def __init__(self, screen, raw_config: dict, screen_w: int, screen_h: int,
                 sprite_path: str = None):
        self.screen = screen
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.cfg = copy.deepcopy(raw_config)
        self.cfg.setdefault("playarea", {})

        self.fields = _build_fields(screen_w, screen_h)
        self.selected = 0
        self.scroll = 0

        # Eksik anahtarları default'larla doldur (kaydedilen config eksiksiz olsun)
        for f in self.fields:
            if f.get(self.cfg) is None:
                f.set(self.cfg, f.default)

        # Fontlar (ekran boyutuna göre ölçeklenir)
        base = max(16, int(screen_h / 38))
        self.font = pygame.font.Font(None, base)
        self.font_small = pygame.font.Font(None, int(base * 0.78))
        self.font_section = pygame.font.Font(None, int(base * 0.85))
        self.font_title = pygame.font.Font(None, int(base * 1.5))

        # Önizleme için tek bir hırsız frame'i
        self.preview_thief = None
        self._sprite_path = sprite_path
        if sprite_path and os.path.exists(sprite_path):
            try:
                sheet = SpriteSheet(sprite_path, 48, 64)
                # West (sola) yürüyüş satırından bir frame
                self.preview_thief = sheet.get_frame(3, 1)
            except Exception:
                self.preview_thief = None

        # Panel geometrisi
        self.panel_w = int(screen_w * 0.40)
        self.panel_w = max(360, min(self.panel_w, 640))

    # ---- Yardımcılar ----
    def _visible_fields(self):
        return [f for f in self.fields if f.is_visible(self.cfg)]

    def _move_selection(self, direction: int):
        vis = self._visible_fields()
        if not vis:
            return
        cur = vis[self.selected % len(vis)]
        idx = vis.index(cur)
        idx = (idx + direction) % len(vis)
        self.selected = idx

    def _current_field(self):
        vis = self._visible_fields()
        if not vis:
            return None
        self.selected %= len(vis)
        return vis[self.selected]

    def _play_config(self) -> PlayAreaConfig:
        return PlayAreaConfig.from_dict(self.cfg.get("playarea"))

    # ---- Ana döngü ----
    def run(self):
        """Sihirbazı çalıştır. Kaydedilirse güncel cfg sözlüğünü, iptal edilirse None döner."""
        clock = pygame.time.Clock()
        pygame.key.set_repeat(300, 35)
        pygame.mouse.set_visible(False)
        result = None
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        result = None
                        running = False
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        result = self._finalize()
                        running = False
                    elif event.key in (pygame.K_UP, pygame.K_w):
                        self._move_selection(-1)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self._move_selection(1)
                    elif event.key in (pygame.K_LEFT, pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_a):
                        f = self._current_field()
                        if f:
                            f.adjust(self.cfg, -1)
                    elif event.key in (pygame.K_RIGHT, pygame.K_PLUS, pygame.K_EQUALS,
                                       pygame.K_KP_PLUS, pygame.K_d):
                        f = self._current_field()
                        if f:
                            f.adjust(self.cfg, +1)

            self._draw()
            pygame.display.flip()
            clock.tick(60)

        pygame.key.set_repeat(0)
        return result

    def _finalize(self):
        """Yüzde/oran alanlarından mutlak piksel değerlerini hesaplayıp cfg'e yaz."""
        cfg = self.cfg
        pa = self._play_config()
        rect = pa.compute(self.screen_w, self.screen_h)

        cfg["thief_y"] = int(round(rect.h * cfg.get("thief_ground_pct", 95) / 100))

        if cfg.get("band_enabled"):
            bw = int(cfg.get("band_width_px", 120))
            cx = rect.w * cfg.get("band_center_pct", 50) / 100
            cfg["band_x_min"] = int(round(cx - bw / 2))
            cfg["band_x_max"] = int(round(cx + bw / 2))

        scale = int(cfg.get("thief_scale", 4))
        margin = cfg.get("spawn_margin_px", 0) or (48 * scale // 2 + 60)
        cfg["spawn_x"] = rect.w + margin
        cfg["reset_x"] = -margin

        cfg["installed"] = True
        return cfg

    # ---- Çizim ----
    def _draw(self):
        self.screen.fill(COL_BG)
        rect = self._play_config().compute(self.screen_w, self.screen_h)
        self._draw_preview(rect)
        self._draw_panel(rect)

    def _draw_preview(self, rect):
        # Oynanabilir alan dolgusu + kenarlık
        pygame.draw.rect(self.screen, COL_PLAY_FILL, (rect.x, rect.y, rect.w, rect.h))
        pygame.draw.rect(self.screen, COL_PLAY_BORDER, (rect.x, rect.y, rect.w, rect.h), 3)

        # Zemin çizgisi
        ground_pct = self.cfg.get("thief_ground_pct", 95)
        gy = rect.y + int(rect.h * ground_pct / 100)
        pygame.draw.line(self.screen, COL_GROUND, (rect.x, gy), (rect.x + rect.w, gy), 2)

        # Hedef band
        if self.cfg.get("band_enabled"):
            bw = int(self.cfg.get("band_width_px", 120))
            cx = rect.x + int(rect.w * self.cfg.get("band_center_pct", 50) / 100)
            band_surf = pygame.Surface((max(1, bw), rect.h), pygame.SRCALPHA)
            band_surf.fill((COL_BAND[0], COL_BAND[1], COL_BAND[2], 70))
            self.screen.blit(band_surf, (cx - bw // 2, rect.y))

        # Hırsız önizleme (zemin çizgisine oturur, band merkezinde)
        if self.preview_thief:
            scale = int(self.cfg.get("thief_scale", 4))
            tw, th = 48 * scale, 64 * scale
            # Önizlemede oynanabilir alana sığmazsa görsel olarak küçült
            max_h = int(rect.h * 0.85)
            if th > max_h and th > 0:
                k = max_h / th
                tw, th = max(1, int(tw * k)), max(1, int(th * k))
            try:
                thief = pygame.transform.scale(self.preview_thief, (tw, th))
                tx = rect.x + rect.w // 2 - tw // 2
                ty = gy - th
                self.screen.blit(thief, (tx, ty))
            except Exception:
                pass

        # Ölçü bilgileri (alanın altına/üstüne)
        ppcm = rect.px_per_cm
        info = f"{rect.w} x {rect.h} px"
        if ppcm > 0:
            info += f"  |  {rect.w / ppcm:.1f} x {rect.h / ppcm:.1f} cm  |  {rect.ppi:.0f} PPI"
        info_surf = self.font_small.render(info, True, COL_TEXT)
        iy = rect.y - info_surf.get_height() - 6
        if iy < 4:
            iy = rect.y + rect.h + 6
        self.screen.blit(info_surf, (rect.x + 4, iy))

    def _draw_panel(self, rect):
        panel = pygame.Surface((self.panel_w, self.screen_h), pygame.SRCALPHA)
        panel.fill((COL_PANEL[0], COL_PANEL[1], COL_PANEL[2], 235))
        self.screen.blit(panel, (0, 0))
        pygame.draw.line(self.screen, COL_PANEL_BORDER,
                         (self.panel_w, 0), (self.panel_w, self.screen_h), 2)

        pad = 24
        y = 18
        title = self.font_title.render("KURULUM", True, COL_TEXT)
        self.screen.blit(title, (pad, y))
        y += title.get_height() + 4
        sub = self.font_small.render(
            "Yukarı/Aşağı: seç   Sol/Sağ veya -/+: değiştir", True, COL_DIM)
        self.screen.blit(sub, (pad, y))
        y += sub.get_height() + 2
        sub2 = self.font_small.render("Enter: kaydet   Esc: iptal", True, COL_DIM)
        self.screen.blit(sub2, (pad, y))
        y += sub2.get_height() + 12

        vis = self._visible_fields()
        if vis:
            self.selected %= len(vis)

        row_h = self.font.get_height() + 12
        section_h = self.font_section.get_height() + 10
        bottom_limit = self.screen_h - 16

        # Seçili alan ekran dışına taşarsa basit kaydırma
        # (alanlar azsa scroll devreye girmez)
        approx_top = y
        for i, f in enumerate(vis):
            if i < self.scroll:
                continue
            if f.section:
                if approx_top + section_h > bottom_limit:
                    break
                sec = self.font_section.render(f.section, True, COL_SECTION)
                self.screen.blit(sec, (pad, approx_top + 4))
                approx_top += section_h

            if approx_top + row_h > bottom_limit:
                break

            selected = (i == self.selected)
            if selected:
                sel_rect = pygame.Rect(8, approx_top - 2, self.panel_w - 16, row_h)
                pygame.draw.rect(self.screen, COL_SELECT_BG, sel_rect, border_radius=6)

            label_col = COL_TEXT if selected else COL_DIM
            label = self.font.render(f.label, True, label_col)
            self.screen.blit(label, (pad, approx_top + 4))

            val_str = f.display_value(self.cfg)
            val_col = COL_VALUE if selected else COL_TEXT
            if selected:
                val_str = f"‹ {val_str} ›"
            val = self.font.render(val_str, True, val_col)
            self.screen.blit(val, (self.panel_w - pad - val.get_width(), approx_top + 4))

            approx_top += row_h

        # Seçili alanı görünür tutmak için kaba scroll ayarı
        # (basit yaklaşım: seçili index alttaysa scroll'u artır)
        self._adjust_scroll(vis, y, row_h, section_h, bottom_limit)

    def _adjust_scroll(self, vis, top, row_h, section_h, bottom_limit):
        """Seçili alan panele sığmıyorsa scroll offsetini güncelle."""
        # Seçili alana kadar olan toplam yüksekliği hesapla
        used = top
        for i, f in enumerate(vis):
            if i < self.scroll:
                continue
            if f.section:
                used += section_h
            if i == self.selected:
                if used + row_h > bottom_limit and self.scroll < self.selected:
                    self.scroll += 1
                break
            used += row_h
        if self.selected < self.scroll:
            self.scroll = self.selected
