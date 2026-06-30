"""
Playarea modülü - Pleksi/oynanabilir alan hesabı.

Ekran çözünürlüğü (px) + ekran köşegeni (inç) + pleksi ölçüleri (cm) verildiğinde
oyunun çizileceği dikdörtgeni (x, y, w, h) piksel cinsinden hesaplar. Bu dikdörtgen
dışında kalan her yer (sol/sağ/üst/alt) siyah bar olarak kalır.

İki mod:
  - "physical": inç + cm girilir, PPI üzerinden piksele çevrilir.
  - "manual_px": doğrudan px (x, y, width, height) girilir.
"""
import math
from dataclasses import dataclass


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


@dataclass
class PlayRect:
    """Hesaplanmış oynanabilir alan (piksel)."""
    x: int
    y: int
    w: int
    h: int
    px_per_cm: float = 0.0
    ppi: float = 0.0


@dataclass
class PlayAreaConfig:
    """config.json içindeki 'playarea' bloğunun temsili."""
    enabled: bool = False
    mode: str = "physical"          # "physical" | "manual_px"

    # physical mod
    screen_diagonal_in: float = 24.0
    plexi_width_cm: float = 50.0
    plexi_height_cm: float = 30.0
    align_x: str = "center"         # center | left | right | custom
    align_y: str = "center"         # center | top | bottom | custom
    margin_left_cm: float = 0.0     # align_x == custom iken soldan boşluk
    margin_top_cm: float = 0.0      # align_y == custom iken üstten boşluk

    # manual_px mod
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_dict(cls, d) -> "PlayAreaConfig":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            mode=str(d.get("mode", "physical")),
            screen_diagonal_in=float(d.get("screen_diagonal_in", 24.0)),
            plexi_width_cm=float(d.get("plexi_width_cm", 50.0)),
            plexi_height_cm=float(d.get("plexi_height_cm", 30.0)),
            align_x=str(d.get("align_x", "center")),
            align_y=str(d.get("align_y", "center")),
            margin_left_cm=float(d.get("margin_left_cm", 0.0)),
            margin_top_cm=float(d.get("margin_top_cm", 0.0)),
            x=int(d.get("x", 0)),
            y=int(d.get("y", 0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "screen_diagonal_in": self.screen_diagonal_in,
            "plexi_width_cm": self.plexi_width_cm,
            "plexi_height_cm": self.plexi_height_cm,
            "align_x": self.align_x,
            "align_y": self.align_y,
            "margin_left_cm": self.margin_left_cm,
            "margin_top_cm": self.margin_top_cm,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def px_per_cm(self, screen_w: int, screen_h: int) -> float:
        """Ekran köşegeninden cm başına piksel oranı."""
        if self.screen_diagonal_in <= 0:
            return 0.0
        diag_px = math.hypot(screen_w, screen_h)
        ppi = diag_px / self.screen_diagonal_in
        return ppi / 2.54

    def compute(self, screen_w: int, screen_h: int) -> PlayRect:
        """Fiziksel ekran boyutuna göre oynanabilir alanı hesapla."""
        # Devre dışı: tüm ekran oynanabilir (eski davranış birebir korunur).
        if not self.enabled:
            return PlayRect(0, 0, screen_w, screen_h, 0.0, 0.0)

        if self.mode == "manual_px":
            w = int(self.width) if self.width > 0 else screen_w
            h = int(self.height) if self.height > 0 else screen_h
            w = int(_clamp(w, 1, screen_w))
            h = int(_clamp(h, 1, screen_h))
            x = int(_clamp(self.x, 0, screen_w - w))
            y = int(_clamp(self.y, 0, screen_h - h))
            return PlayRect(x, y, w, h, 0.0, 0.0)

        # physical mod
        diag_px = math.hypot(screen_w, screen_h)
        ppi = diag_px / self.screen_diagonal_in if self.screen_diagonal_in > 0 else 0.0
        ppcm = ppi / 2.54

        w = int(round(self.plexi_width_cm * ppcm))
        h = int(round(self.plexi_height_cm * ppcm))
        w = int(_clamp(w, 1, screen_w))
        h = int(_clamp(h, 1, screen_h))

        # Yatay hizalama
        if self.align_x == "left":
            x = 0
        elif self.align_x == "right":
            x = screen_w - w
        elif self.align_x == "custom":
            x = int(round(self.margin_left_cm * ppcm))
        else:  # center
            x = (screen_w - w) // 2

        # Dikey hizalama
        if self.align_y == "top":
            y = 0
        elif self.align_y == "bottom":
            y = screen_h - h
        elif self.align_y == "custom":
            y = int(round(self.margin_top_cm * ppcm))
        else:  # center
            y = (screen_h - h) // 2

        x = int(_clamp(x, 0, max(0, screen_w - w)))
        y = int(_clamp(y, 0, max(0, screen_h - h)))

        return PlayRect(x, y, w, h, ppcm, ppi)
