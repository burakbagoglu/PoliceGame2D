"""Kalıcı sahne taslakları, yayın sürümleri, assetler ve client önizlemeleri."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PIL import Image
except ImportError:
    Image = None


ALLOWED_ELEMENT_TYPES = {
    "rect",
    "text",
    "sprite",
    "score",
    "confetti",
    "hit_zone",
    "path",
}
IMAGE_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_ASSET_EXTENSIONS = {".wav", ".ogg", ".mp3"}
ALLOWED_ASSET_EXTENSIONS = IMAGE_ASSET_EXTENSIONS | AUDIO_ASSET_EXTENSIONS
SAFE_ASSET_NAME = re.compile(r"[^A-Za-z0-9._-]+")
SCHEMA_VERSION = 7


def _rect(
    element_id: str,
    x: int,
    y: int,
    width: int,
    height: int,
    fill: str,
    stroke: str,
    z: int = 0,
    radius: int = 30,
) -> dict:
    return {
        "id": element_id,
        "type": "rect",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z": z,
        "fill": fill,
        "stroke": stroke,
        "stroke_width": 10,
        "radius": radius,
        "opacity": 1.0,
        "effect": "none",
        "effect_speed": 1.0,
    }


def _text(
    element_id: str,
    text: str,
    x: int,
    y: int,
    width: int,
    height: int,
    font_size: int,
    color: str,
    outline: str,
    z: int = 1,
    effect: str = "none",
) -> dict:
    return {
        "id": element_id,
        "type": "text",
        "text": text,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z": z,
        "font_size": font_size,
        "font_weight": "bold",
        "color": color,
        "outline_color": outline,
        "outline_width": 5,
        "align": "center",
        "opacity": 1.0,
        "effect": effect,
        "effect_speed": 1.0,
    }


def _sprite(
    element_id: str,
    asset: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int = 0,
) -> dict:
    return {
        "id": element_id,
        "type": "sprite",
        "asset": asset,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z": z,
        "opacity": 1.0,
        "effect": "none",
        "effect_speed": 1.0,
    }

def default_scene_document() -> dict:
    """Mevcut oyun temasını editlenebilir başlangıç sahnelerine dönüştür."""
    yellow = "#ffe038"
    red = "#ec2d4c"
    purple = "#6923a5"
    dark = "#351458"
    white = "#fffff7"

    document = {
        "schema_version": SCHEMA_VERSION,
        "editor": {
            "grid_size": 10, "snap": True, "show_grid": True,
            "guides_x": [], "guides_y": [], "asset_tags": {},
        },
        "prefabs": {},
        "rules": [],
        "game_profiles": {
            "short": {"name": "Kısa Tur", "child_count": 2, "screen_count": 8, "duration_minutes": 30, "difficulty": "easy"},
            "standard": {"name": "Standart", "child_count": 5, "screen_count": 8, "duration_minutes": 35, "difficulty": "normal"},
            "intense": {"name": "Yoğun", "child_count": 10, "screen_count": 8, "duration_minutes": 40, "difficulty": "hard"},
        },
        "canvas": {
            "width": 1920,
            "height": 1080,
            "background": "#282c34",
            "background_asset": "__client_background__",
        },
        "scenes": {
            "waiting": {
                "name": "Oyun Bekleniyor",
                "background": "transparent",
                "elements": [
                    _rect("waiting_card", 250, 330, 1420, 420, yellow, purple),
                    _text(
                        "waiting_title",
                        "OYUN BEKLENİYOR",
                        320,
                        395,
                        1280,
                        135,
                        100,
                        red,
                        purple,
                        effect="pulse",
                    ),
                    {
                        **_text(
                            "waiting_subtitle",
                            "HAZIR OL • HIRSIZLAR YAKINDA",
                            400,
                            555,
                            1120,
                            90,
                            48,
                            purple,
                            purple,
                        ),
                        "outline_width": 0,
                    },
                ],
            },
            "intro": {
                "name": "Hırsızları Vur",
                "background": "transparent",
                "elements": [
                    {
                        **_rect("intro_dim", 0, 0, 1920, 1080, "#1e0a33aa", "#1e0a3300", z=-5, radius=0),
                        "stroke_width": 0,
                    },
                    _rect("intro_card", 260, 225, 1400, 630, yellow, purple, radius=45),
                    _text(
                        "intro_title",
                        "HIRSIZLARI\nVUR",
                        340,
                        315,
                        1240,
                        420,
                        145,
                        red,
                        purple,
                        effect="scale",
                    ),
                    {
                        "id": "intro_confetti",
                        "type": "confetti",
                        "x": 280,
                        "y": 100,
                        "width": 1360,
                        "height": 850,
                        "z": 2,
                        "amount": 90,
                        "opacity": 1.0,
                        "effect": "none",
                        "effect_speed": 1.0,
                    },
                ],
            },
            "countdown": {
                "name": "Geri Sayım",
                "background": "transparent",
                "elements": [
                    {
                        **_rect("countdown_dim", 0, 0, 1920, 1080, "#1e0a33aa", "#1e0a3300", z=-5, radius=0),
                        "stroke_width": 0,
                    },
                    _rect("countdown_card", 390, 180, 1140, 720, yellow, purple, radius=55),
                    _text(
                        "countdown_value",
                        "{countdown}",
                        470,
                        260,
                        980,
                        540,
                        330,
                        red,
                        purple,
                        effect="scale",
                    ),
                    {
                        "id": "countdown_confetti",
                        "type": "confetti",
                        "x": 260,
                        "y": 80,
                        "width": 1400,
                        "height": 900,
                        "z": 2,
                        "amount": 70,
                        "opacity": 1.0,
                        "effect": "none",
                        "effect_speed": 1.0,
                    },
                ],
            },
            "gameplay": {
                "name": "Oyun",
                "background": "transparent",
                "elements": [
                    {
                        "id": "score_widget",
                        "type": "score",
                        "x": 1515,
                        "y": 38,
                        "width": 350,
                        "height": 180,
                        "z": 10,
                        "label": "YAKALANAN",
                        "fill": yellow,
                        "stroke": purple,
                        "color": red,
                        "label_color": purple,
                        "font_size": 80,
                        "radius": 28,
                        "opacity": 1.0,
                        "effect": "none",
                        "effect_speed": 1.0,
                    },
                    _text(
                        "screen_quota_text",
                        "BU EKRAN: {screen_score} / {screen_target}",
                        1515, 225, 350, 72, 32, purple, white, z=10,
                    )
                ],
            },
            "jail": {
                "name": "Hırsız Hapiste",
                "background": "#090b10",
                "elements": [
                    _sprite("jail_background", "jail_background.png", 0, 0, 1920, 1080, z=-20),
                    _sprite("jail_thief", "jail_thief_grabbars.png", 550, 105, 820, 820, z=0),
                    {
                        **_rect("jail_status_panel", 420, 900, 1080, 125, "#11151de6", "#e9b84a", z=10, radius=30),
                        "stroke_width": 4,
                    },
                    _text("jail_title", "HIRSIZ HAPİSTE!", 460, 28, 1000, 100, 72, yellow, dark, z=10),
                    _text(
                        "jail_wait",
                        "BU EKRAN TAMAMLANDI • DİĞER EKRANLAR DEVAM EDİYOR",
                        465,
                        925,
                        990,
                        72,
                        34,
                        white,
                        dark,
                        z=11,
                    ),
                ],
            },
            "win": {
                "name": "Kazanma",
                "background": "#351458",
                "elements": [
                    _rect("win_card", 240, 205, 1440, 670, yellow, purple, radius=55),
                    _text(
                        "win_title",
                        "HARİKASINIZ!",
                        330,
                        285,
                        1260,
                        180,
                        145,
                        red,
                        purple,
                        effect="pulse",
                    ),
                    _text(
                        "win_score",
                        "SKOR: {score}",
                        480,
                        520,
                        960,
                        140,
                        85,
                        purple,
                        white,
                    ),
                    {
                        "id": "win_confetti",
                        "type": "confetti",
                        "x": 180,
                        "y": 20,
                        "width": 1560,
                        "height": 1000,
                        "z": 3,
                        "amount": 150,
                        "opacity": 1.0,
                        "effect": "none",
                        "effect_speed": 1.0,
                    },
                ],
            },
            "lose": {
                "name": "Kaybetme",
                "background": "#282c34",
                "elements": [
                    _rect("lose_card", 280, 255, 1360, 570, yellow, purple, radius=48),
                    _text(
                        "lose_title",
                        "BİR DAHA DENE!",
                        360,
                        340,
                        1200,
                        180,
                        125,
                        red,
                        purple,
                        effect="shake",
                    ),
                    _text(
                        "lose_score",
                        "SKOR: {score}",
                        520,
                        555,
                        880,
                        130,
                        75,
                        dark,
                        white,
                    ),
                ],
            },
        },
    }
    for scene in document["scenes"].values():
        scene.setdefault("layer_groups", [])
        scene.setdefault("duration", 5.0)
        scene.setdefault("loop_timeline", False)
        scene.setdefault("transition", {"type": "none", "duration": 0.35})
        scene.setdefault("audio_cues", [])
    return document


class SceneValidationError(ValueError):
    pass


class SceneRevisionConflict(SceneValidationError):
    def __init__(self, expected: int, actual: int):
        self.expected = int(expected)
        self.actual = int(actual)
        super().__init__(
            f"Taslak başka bir oturumda değişti (beklenen r{expected}, sunucuda r{actual})"
        )


class SceneManager:
    """Thread-safe JSON sahne deposu ve client yayın yöneticisi."""

    def __init__(self, data_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir)
        self.assets_dir = self.data_dir / "assets"
        self.bundled_assets_dir = Path(__file__).resolve().parent / "default_scene_assets"
        self.history_dir = self.data_dir / "history"
        self.client_previews_dir = self.data_dir / "client_previews"
        self.draft_path = self.data_dir / "draft.json"
        self.published_path = self.data_dir / "published.json"
        self._lock = threading.RLock()
        self._preview_by_screen: Dict[int, str] = {}
        self._screenshot_requests: Dict[int, dict] = {}
        self._client_screenshots: Dict[int, dict] = {}
        self._draft_revision = 1
        self._published_version = 1

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.client_previews_dir.mkdir(parents=True, exist_ok=True)
        draft_stored_schema = self._stored_schema_version(self.draft_path)
        published_stored_schema = self._stored_schema_version(self.published_path)
        self._draft = self._load_or_default(self.draft_path)
        self._published = self._load_or_default(self.published_path)
        self._draft_revision = int(self._draft.pop("_draft_revision", 1))
        self._published_version = int(self._published.pop("_published_version", 1))
        draft_migrated = draft_stored_schema is not None and draft_stored_schema < SCHEMA_VERSION
        published_migrated = published_stored_schema is not None and published_stored_schema < SCHEMA_VERSION
        if draft_migrated:
            self._draft_revision += 1
        if published_migrated:
            self._published_version += 1

        if not self.draft_path.exists() or draft_migrated:
            self._write_document(
                self.draft_path,
                self._draft,
                {"_draft_revision": self._draft_revision},
            )
        if not self.published_path.exists() or published_migrated:
            self._write_document(
                self.published_path,
                self._published,
                {"_published_version": self._published_version},
            )

    @staticmethod
    def _stored_schema_version(path: Path) -> Optional[int]:
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle).get("schema_version")
            return int(value) if value is not None else 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
    def _load_or_default(self, path: Path) -> dict:
        if not path.exists():
            return default_scene_document()
        try:
            with path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
            document = self._migrate_document(document)
            self.validate_document(document)
            return document
        except (OSError, json.JSONDecodeError, SceneValidationError):
            return default_scene_document()

    @staticmethod
    def _migrate_document(document: dict) -> dict:
        """Upgrade older scene drafts without losing user layout changes."""
        document = copy.deepcopy(document)
        version = int(document.get("schema_version", 1) or 1)
        if version < 2:
            canvas = document.setdefault("canvas", {})
            canvas.setdefault("background_asset", "__client_background__")
            waiting = document.get("scenes", {}).get("waiting", {})
            has_default_card = any(
                item.get("id") == "waiting_card"
                for item in waiting.get("elements", [])
            )
            if has_default_card and waiting.get("background") == "#282c34":
                waiting["background"] = "transparent"
            document["schema_version"] = 2
            version = 2
        if version < 3:
            for scene in document.get("scenes", {}).values():
                for element in scene.get("elements", []):
                    # "scale" eski non-uniform ölçekleme davranışını aynen korur.
                    element.setdefault("anchor_x", "scale")
                    element.setdefault("anchor_y", "scale")
            document["schema_version"] = 3
            version = 3
        if version < 4:
            document.setdefault("editor", {"grid_size": 10, "snap": True, "show_grid": True})
            document.setdefault("prefabs", {})
            document.setdefault("rules", [])
            for scene in document.get("scenes", {}).values():
                scene.setdefault("duration", 5.0)
                scene.setdefault("loop_timeline", False)
                scene.setdefault("transition", {"type": "none", "duration": 0.35})
                scene.setdefault("audio_cues", [])
                for element in scene.get("elements", []):
                    element.setdefault("locked", False)
                    element.setdefault("hidden", False)
                    element.setdefault("group_id", "")
                    element.setdefault("keyframes", [])
            document["schema_version"] = 4
            version = 4
        if version < 5:
            editor = document.setdefault("editor", {})
            editor.setdefault("guides_x", [])
            editor.setdefault("guides_y", [])
            editor.setdefault("asset_tags", {})
            document.setdefault("game_profiles", copy.deepcopy(default_scene_document()["game_profiles"]))
            for scene in document.get("scenes", {}).values():
                scene.setdefault("layer_groups", [])
                for element in scene.get("elements", []):
                    element.setdefault("folder_id", "")
                    element.setdefault("blend_mode", "normal")
                    element.setdefault("shadow", {"enabled": False, "x": 8, "y": 8, "blur": 8, "color": "#00000088"})
                    element.setdefault("filters", {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "blur": 0})
            document["schema_version"] = 5
            version = 5
        if version < 6:
            defaults = default_scene_document()
            document.setdefault("scenes", {}).setdefault(
                "jail", copy.deepcopy(defaults["scenes"]["jail"])
            )
            gameplay = document.get("scenes", {}).get("gameplay")
            if isinstance(gameplay, dict):
                elements = gameplay.setdefault("elements", [])
                if not any(item.get("id") == "screen_quota_text" for item in elements if isinstance(item, dict)):
                    default_quota = next(
                        item for item in defaults["scenes"]["gameplay"]["elements"]
                        if item.get("id") == "screen_quota_text"
                    )
                    elements.append(copy.deepcopy(default_quota))
            profiles = document.setdefault(
                "game_profiles", copy.deepcopy(defaults["game_profiles"])
            )
            for profile in profiles.values():
                if isinstance(profile, dict):
                    profile["screen_count"] = 8
            document["schema_version"] = 6
            version = 6
        if version < 7:
            defaults = default_scene_document()
            jail = document.setdefault("scenes", {}).get("jail")
            legacy_ids = {
                "jail_card", "jail_progress", "jail_bar_1", "jail_bar_2",
                "jail_bar_3", "jail_bar_4", "jail_bar_5",
            }
            current_ids = {
                str(item.get("id", ""))
                for item in (jail or {}).get("elements", [])
                if isinstance(item, dict)
            }
            if not isinstance(jail, dict) or current_ids & legacy_ids:
                document["scenes"]["jail"] = copy.deepcopy(defaults["scenes"]["jail"])
            else:
                jail["elements"] = [
                    item for item in jail.get("elements", [])
                    if not (
                        isinstance(item, dict)
                        and (item.get("type") == "score" or item.get("id") == "score_widget")
                    )
                ]
            document["schema_version"] = 7
        return document

    @staticmethod
    def validate_document(document: dict):
        if not isinstance(document, dict):
            raise SceneValidationError("Sahne belgesi JSON object olmalı")
        canvas = document.get("canvas")
        scenes = document.get("scenes")
        if not isinstance(canvas, dict) or not isinstance(scenes, dict):
            raise SceneValidationError("canvas ve scenes alanları zorunlu")
        width = canvas.get("width")
        height = canvas.get("height")
        if not isinstance(width, (int, float)) or not 320 <= width <= 7680:
            raise SceneValidationError("Canvas genişliği 320-7680 arasında olmalı")
        if not isinstance(height, (int, float)) or not 180 <= height <= 4320:
            raise SceneValidationError("Canvas yüksekliği 180-4320 arasında olmalı")
        if not 1 <= len(scenes) <= 32:
            raise SceneValidationError("En az 1, en fazla 32 sahne olabilir")

        editor = document.get("editor", {})
        if not isinstance(editor, dict):
            raise SceneValidationError("editor alanı object olmalı")
        for guide_key, limit in (("guides_x", width), ("guides_y", height)):
            guides = editor.get(guide_key, [])
            if not isinstance(guides, list) or len(guides) > 100:
                raise SceneValidationError(f"{guide_key} en fazla 100 değer içermeli")
            if any(not isinstance(value, (int, float)) or not -limit <= value <= limit * 2 for value in guides):
                raise SceneValidationError(f"{guide_key} geçersiz değer içeriyor")
        asset_tags = editor.get("asset_tags", {})
        if not isinstance(asset_tags, dict) or len(asset_tags) > 500:
            raise SceneValidationError("asset_tags en fazla 500 asset içeren object olmalı")
        for asset_name, tags in asset_tags.items():
            if not isinstance(asset_name, str) or len(asset_name) > 180 or not isinstance(tags, list) or len(tags) > 20:
                raise SceneValidationError("asset_tags geçersiz")
            if any(not isinstance(tag, str) or not 1 <= len(tag) <= 40 for tag in tags):
                raise SceneValidationError("Asset etiketi 1-40 karakter olmalı")
        profiles = document.get("game_profiles", {})
        if not isinstance(profiles, dict) or len(profiles) > 32:
            raise SceneValidationError("game_profiles en fazla 32 kayıt içeren object olmalı")
        for profile_id, profile in profiles.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(profile_id)) or not isinstance(profile, dict):
                raise SceneValidationError("Geçersiz oyun profili")
            if profile.get("difficulty", "normal") not in {"easy", "normal", "hard"}:
                raise SceneValidationError(f"{profile_id}.difficulty geçersiz")
            for key, minimum, maximum in (("child_count", 1, 100), ("screen_count", 8, 8), ("duration_minutes", 1, 120)):
                value = profile.get(key, minimum)
                if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                    raise SceneValidationError(f"{profile_id}.{key} geçersiz")
        prefabs = document.get("prefabs", {})
        rules = document.get("rules", [])
        if not isinstance(prefabs, dict) or len(prefabs) > 50:
            raise SceneValidationError("prefabs alanı object olmalı ve en fazla 50 kayıt içermeli")
        if not isinstance(rules, list) or len(rules) > 100:
            raise SceneValidationError("rules alanı liste olmalı ve en fazla 100 kayıt içermeli")
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("scene_id") not in scenes:
                raise SceneValidationError("Her kural geçerli bir scene_id içermeli")
            if rule.get("event") not in {
                "always", "score_gte", "score_lte", "time_lte", "time_gte",
                "hit_gte", "combo_gte", "win", "lose", "game_active", "screen_complete",
            }:
                raise SceneValidationError("Desteklenmeyen sahne kuralı")

        for prefab_id, prefab in prefabs.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", str(prefab_id)):
                raise SceneValidationError("Geçersiz prefab ID")
            if not isinstance(prefab, dict) or not isinstance(prefab.get("elements"), list):
                raise SceneValidationError("Prefab elements alanı liste olmalı")
            if len(prefab["elements"]) > 60:
                raise SceneValidationError("Bir prefab en fazla 60 öğe içerebilir")
        for scene_id, scene in scenes.items():
            if not isinstance(scene_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", scene_id):
                raise SceneValidationError(f"Geçersiz sahne ID: {scene_id}")
            if not isinstance(scene, dict) or not isinstance(scene.get("elements"), list):
                raise SceneValidationError(f"{scene_id} sahnesinin elements alanı liste olmalı")
            if len(scene["elements"]) > 120:
                raise SceneValidationError(f"{scene_id} sahnesinde en fazla 120 öğe olabilir")
            layer_groups = scene.get("layer_groups", [])
            if not isinstance(layer_groups, list) or len(layer_groups) > 40:
                raise SceneValidationError(f"{scene_id}.layer_groups en fazla 40 kayıt olabilir")
            group_ids = set()
            for group in layer_groups:
                if not isinstance(group, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", str(group.get("id", ""))):
                    raise SceneValidationError(f"{scene_id} katman klasörü geçersiz")
                if group["id"] in group_ids:
                    raise SceneValidationError(f"{scene_id} katman klasörü tekrarlanıyor")
                group_ids.add(group["id"])
                opacity = group.get("opacity", 1.0)
                if not isinstance(opacity, (int, float)) or not 0 <= float(opacity) <= 1:
                    raise SceneValidationError(f"{scene_id} katman klasörü opacity geçersiz")
                if group.get("blend_mode", "normal") not in {"normal", "add", "multiply", "screen"}:
                    raise SceneValidationError(f"{scene_id} katman klasörü blend_mode geçersiz")
            duration = scene.get("duration", 5.0)
            if not isinstance(duration, (int, float)) or not 0.1 <= duration <= 600:
                raise SceneValidationError(f"{scene_id}.duration 0.1-600 arasında olmalı")
            transition = scene.get("transition", {"type": "none", "duration": 0.35})
            if not isinstance(transition, dict) or transition.get("type", "none") not in {"none", "fade", "slide_left", "slide_right", "zoom"}:
                raise SceneValidationError(f"{scene_id}.transition geçersiz")
            transition_duration = transition.get("duration", 0.35)
            if not isinstance(transition_duration, (int, float)) or not 0 <= transition_duration <= 5:
                raise SceneValidationError(f"{scene_id}.transition.duration geçersiz")
            audio_cues = scene.get("audio_cues", [])
            if not isinstance(audio_cues, list) or len(audio_cues) > 40:
                raise SceneValidationError(f"{scene_id}.audio_cues en fazla 40 kayıt olabilir")
            for cue in audio_cues:
                if not isinstance(cue, dict) or not isinstance(cue.get("time", 0), (int, float)):
                    raise SceneValidationError(f"{scene_id} ses cue kaydı geçersiz")
                if float(cue.get("time", 0)) < 0 or float(cue.get("time", 0)) > duration:
                    raise SceneValidationError(f"{scene_id} ses cue zamanı sahne süresi dışında")
                for key in ("volume", "pan"):
                    if key in cue and not isinstance(cue[key], (int, float)):
                        raise SceneValidationError(f"{scene_id} ses cue {key} değeri geçersiz")
                if not 0 <= float(cue.get("volume", 1)) <= 1:
                    raise SceneValidationError(f"{scene_id} ses cue volume 0-1 arasında olmalı")
                if not -1 <= float(cue.get("pan", 0)) <= 1:
                    raise SceneValidationError(f"{scene_id} ses cue pan -1 ile 1 arasında olmalı")
                for key in ("fade_in_ms", "fade_out_ms", "max_duration_ms"):
                    if key in cue and (
                        not isinstance(cue[key], (int, float))
                        or not 0 <= float(cue[key]) <= 600_000
                    ):
                        raise SceneValidationError(f"{scene_id} ses cue {key} değeri geçersiz")
            seen_ids = set()
            for element in scene["elements"]:
                if not isinstance(element, dict):
                    raise SceneValidationError("Her öğe JSON object olmalı")
                element_id = element.get("id")
                if not isinstance(element_id, str) or not element_id:
                    raise SceneValidationError("Her öğenin benzersiz ID değeri olmalı")
                if element_id in seen_ids:
                    raise SceneValidationError(f"Tekrarlanan öğe ID: {element_id}")
                seen_ids.add(element_id)
                if element.get("type") not in ALLOWED_ELEMENT_TYPES:
                    raise SceneValidationError(f"Desteklenmeyen öğe tipi: {element.get('type')}")
                for key in ("x", "y", "width", "height"):
                    if not isinstance(element.get(key), (int, float)):
                        raise SceneValidationError(f"{element_id}.{key} sayı olmalı")
                if element["width"] < 1 or element["height"] < 1:
                    raise SceneValidationError(f"{element_id} boyutu pozitif olmalı")
                if element.get("anchor_x", "scale") not in {"scale", "left", "center", "right", "stretch"}:
                    raise SceneValidationError(f"{element_id}.anchor_x geçersiz")
                if element.get("anchor_y", "scale") not in {"scale", "top", "center", "bottom", "stretch"}:
                    raise SceneValidationError(f"{element_id}.anchor_y geçersiz")
                if element.get("blend_mode", "normal") not in {"normal", "add", "multiply", "screen"}:
                    raise SceneValidationError(f"{element_id}.blend_mode geçersiz")
                opacity = element.get("opacity", 1.0)
                if not isinstance(opacity, (int, float)) or not 0 <= float(opacity) <= 1:
                    raise SceneValidationError(f"{element_id}.opacity 0-1 arasında olmalı")
                shadow = element.get("shadow", {})
                if not isinstance(shadow, dict) or not isinstance(shadow.get("enabled", False), bool):
                    raise SceneValidationError(f"{element_id}.shadow geçersiz")
                for key, minimum, maximum in (("x", -500, 500), ("y", -500, 500), ("blur", 0, 100)):
                    value = shadow.get(key, 0)
                    if not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                        raise SceneValidationError(f"{element_id}.shadow.{key} geçersiz")
                filters = element.get("filters", {})
                if not isinstance(filters, dict):
                    raise SceneValidationError(f"{element_id}.filters geçersiz")
                for key, maximum in (("brightness", 3), ("contrast", 3), ("saturation", 3), ("blur", 40)):
                    value = filters.get(key, 1 if key != "blur" else 0)
                    if not isinstance(value, (int, float)) or not 0 <= float(value) <= maximum:
                        raise SceneValidationError(f"{element_id}.filters.{key} geçersiz")
                folder_id = str(element.get("folder_id", ""))
                if folder_id and folder_id not in group_ids:
                    raise SceneValidationError(f"{element_id}.folder_id bulunamadı")
                if element.get("type") == "path":
                    points = element.get("points", [])
                    if not isinstance(points, list) or not 2 <= len(points) <= 40:
                        raise SceneValidationError(f"{element_id}.points 2-40 nokta içermeli")
                    if any(not isinstance(point, dict) or not isinstance(point.get("x"), (int, float)) or not isinstance(point.get("y"), (int, float)) for point in points):
                        raise SceneValidationError(f"{element_id}.points geçersiz")
                keyframes = element.get("keyframes", [])
                if not isinstance(keyframes, list) or len(keyframes) > 120:
                    raise SceneValidationError(f"{element_id}.keyframes en fazla 120 kayıt olabilir")
                previous_time = -1.0
                for keyframe in keyframes:
                    if not isinstance(keyframe, dict) or not isinstance(keyframe.get("time"), (int, float)):
                        raise SceneValidationError(f"{element_id} keyframe zamanı geçersiz")
                    frame_time = float(keyframe["time"])
                    if frame_time < 0 or frame_time > float(scene.get("duration", 5.0)):
                        raise SceneValidationError(f"{element_id} keyframe sahne süresi dışında")
                    if frame_time < previous_time:
                        raise SceneValidationError(f"{element_id} keyframeleri zaman sıralı olmalı")
                    previous_time = frame_time
                sheet = element.get("sprite_sheet")
                if sheet is not None:
                    if element.get("type") != "sprite" or not isinstance(sheet, dict):
                        raise SceneValidationError(f"{element_id}.sprite_sheet yalnızca sprite için geçerli")
                    for key in ("columns", "rows", "fps"):
                        if not isinstance(sheet.get(key), (int, float)) or float(sheet[key]) <= 0:
                            raise SceneValidationError(f"{element_id}.sprite_sheet.{key} pozitif olmalı")

            for group in layer_groups:
                mask_id = str(group.get("mask_element_id", ""))
                if not mask_id:
                    continue
                mask_element = next((item for item in scene["elements"] if item.get("id") == mask_id), None)
                if mask_element is None or str(mask_element.get("folder_id", "")) != str(group["id"]):
                    raise SceneValidationError(f"{scene_id}.{group['id']} kırpma maskesi klasörde bulunamadı")

    def _write_document(self, path: Path, document: dict, metadata: dict):
        payload = copy.deepcopy(document)
        payload.update(metadata)
        fd, temp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.stem}_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except BaseException:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def get_editor_state(self) -> dict:
        with self._lock:
            return {
                "draft": copy.deepcopy(self._draft),
                "draft_revision": self._draft_revision,
                "published_version": self._published_version,
                "assets": self.list_assets(),
                "history": self.list_history(),
                "previews": dict(self._preview_by_screen),
                "client_screenshots": {
                    str(screen_id): copy.deepcopy(metadata)
                    for screen_id, metadata in self._client_screenshots.items()
                },
            }

    def save_draft(self, document: dict, expected_revision: Optional[int] = None) -> dict:
        self.validate_document(document)
        with self._lock:
            if (
                expected_revision is not None
                and int(expected_revision) != self._draft_revision
            ):
                raise SceneRevisionConflict(expected_revision, self._draft_revision)
            self._draft = copy.deepcopy(document)
            self._draft_revision += 1
            self._write_document(
                self.draft_path,
                self._draft,
                {"_draft_revision": self._draft_revision},
            )
            return {
                "success": True,
                "draft_revision": self._draft_revision,
            }

    def publish(self) -> dict:
        with self._lock:
            self.validate_document(self._draft)
            audit = self.audit_document(self._draft)
            if audit["errors"]:
                raise SceneValidationError("; ".join(audit["errors"]))
            self._published = copy.deepcopy(self._draft)
            self._published_version += 1
            self._write_document(
                self.published_path,
                self._published,
                {"_published_version": self._published_version},
            )
            history_path = self.history_dir / f"scene_v{self._published_version}.json"
            self._write_document(
                history_path,
                self._published,
                {"_published_version": self._published_version},
            )
            return {
                "success": True,
                "published_version": self._published_version,
                "audit": audit,
            }

    def audit_document(self, document: Optional[dict] = None) -> dict:
        """Yayın öncesi eksik asset ve Pi dostu bütçe uyarılarını üret."""
        source = document if document is not None else self._draft
        errors = []
        warnings = []
        known_assets = {asset["name"]: asset for asset in self.list_assets()}
        referenced = set()
        for scene_id, scene in source.get("scenes", {}).items():
            elements = [item for item in scene.get("elements", []) if not item.get("hidden")]
            if len(elements) > 45:
                warnings.append(f"{scene_id}: {len(elements)} görünür öğe Pi istemcileri yorabilir")
            confetti = sum(
                int(item.get("amount", 0) or 0)
                for item in elements if item.get("type") == "confetti"
            )
            if confetti > 140:
                warnings.append(f"{scene_id}: konfeti sayısı {confetti}, önerilen üst sınır 140")
            for element in elements:
                if element.get("type") == "sprite" and element.get("asset"):
                    referenced.add(str(element["asset"]))
            for cue in scene.get("audio_cues", []):
                if cue.get("asset"):
                    referenced.add(str(cue["asset"]))
        for name in sorted(referenced):
            if name not in known_assets:
                errors.append(f"Eksik asset: {name}")
                continue
            info = known_assets[name]
            if info.get("kind") == "image":
                if info.get("width", 0) > 2048 or info.get("height", 0) > 2048:
                    warnings.append(f"{name}: görsel boyutu Pi için büyük")
                if info.get("size", 0) > 2 * 1024 * 1024:
                    warnings.append(f"{name}: dosya boyutu 2 MB üzerinde")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def rollback(self, version: int) -> dict:
        history_path = self.history_dir / f"scene_v{int(version)}.json"
        if not history_path.exists():
            raise FileNotFoundError(f"Sahne sürümü bulunamadı: v{version}")
        with history_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        document.pop("_published_version", None)
        self.validate_document(document)
        with self._lock:
            self._draft = copy.deepcopy(document)
            self._draft_revision += 1
            self._write_document(
                self.draft_path,
                self._draft,
                {"_draft_revision": self._draft_revision},
            )
        return {
            "success": True,
            "draft_revision": self._draft_revision,
            "message": f"v{version} taslağa yüklendi; yayınlamak için Yayınla'ya basın.",
        }

    def list_history(self) -> list:
        versions = []
        for path in self.history_dir.glob("scene_v*.json"):
            match = re.fullmatch(r"scene_v(\d+)\.json", path.name)
            if match:
                versions.append(int(match.group(1)))
        return sorted(versions, reverse=True)

    def set_preview(self, screen_id: int, scene_id: str):
        with self._lock:
            if scene_id not in self._draft.get("scenes", {}):
                raise SceneValidationError(f"Sahne bulunamadı: {scene_id}")
            self._preview_by_screen[int(screen_id)] = scene_id

    def clear_preview(self, screen_id: int):
        with self._lock:
            self._preview_by_screen.pop(int(screen_id), None)

    def get_client_payload(self, screen_id: int, known_version: str = "") -> dict:
        with self._lock:
            preview_scene = self._preview_by_screen.get(int(screen_id))
            if preview_scene:
                version = f"draft-{self._draft_revision}-{preview_scene}"
                document = self._draft
            else:
                version = f"published-{self._published_version}"
                document = self._published

            changed = str(known_version) != version
            payload = {
                "changed": changed,
                "version": version,
                "preview": bool(preview_scene),
                "preview_scene": preview_scene,
                "screenshot_request": self._screenshot_requests.get(
                    int(screen_id), {}
                ).get("token"),
            }
            if changed:
                payload["document"] = copy.deepcopy(document)
                payload["assets"] = self.assets_for_document(document)
            return payload

    def diff_summary(self) -> dict:
        """Taslak ile yayındaki belge arasındaki operatör dostu fark özeti."""
        with self._lock:
            draft, published = copy.deepcopy(self._draft), copy.deepcopy(self._published)
        draft_scenes, published_scenes = draft.get("scenes", {}), published.get("scenes", {})
        added = sorted(set(draft_scenes) - set(published_scenes))
        removed = sorted(set(published_scenes) - set(draft_scenes))
        changed = []
        for scene_id in sorted(set(draft_scenes) & set(published_scenes)):
            if draft_scenes[scene_id] != published_scenes[scene_id]:
                before, after = published_scenes[scene_id], draft_scenes[scene_id]
                changed.append({"id": scene_id, "name": after.get("name", scene_id),
                    "elements_before": len(before.get("elements", [])), "elements_after": len(after.get("elements", [])),
                    "keyframes_before": sum(len(item.get("keyframes", [])) for item in before.get("elements", [])),
                    "keyframes_after": sum(len(item.get("keyframes", [])) for item in after.get("elements", []))})
        profile_changed = draft.get("game_profiles", {}) != published.get("game_profiles", {})
        changed_total = len(added) + len(removed) + len(changed) + int(profile_changed)
        return {"has_changes": bool(changed_total), "changed_total": changed_total,
            "scenes_added": added, "scenes_removed": removed, "scenes_changed": changed,
            "profiles_changed": profile_changed, "draft_revision": self._draft_revision,
            "published_version": self._published_version}
    def get_published_document(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._published)
    def request_client_screenshot(self, screen_id: int) -> dict:
        """Bir clienttan tek kullanımlık ekran görüntüsü isteği oluştur."""
        screen_id = int(screen_id)
        with self._lock:
            request = {"token": secrets.token_urlsafe(18), "requested_at": time.time()}
            self._screenshot_requests[screen_id] = request
            return {
                "success": True,
                "screen_id": screen_id,
                "request_token": request["token"],
                "status": "waiting",
            }

    def save_client_screenshot(self, screen_id: int, request_token: str, content: bytes) -> dict:
        """İstenen client görüntüsünü doğrula ve atomik biçimde sakla."""
        if not content or len(content) > 4 * 1024 * 1024:
            raise SceneValidationError("Client görüntüsü 1 byte - 4 MB arasında olmalı")
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif content.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        else:
            raise SceneValidationError("Client görüntüsü PNG veya JPEG olmalı")

        screen_id = int(screen_id)
        with self._lock:
            pending = self._screenshot_requests.get(screen_id)
            if not pending or not secrets.compare_digest(
                str(pending.get("token", "")), str(request_token)
            ):
                raise SceneValidationError("Ekran görüntüsü isteği geçersiz veya süresi dolmuş")
            target = self.client_previews_dir / f"screen_{screen_id}{extension}"
            fd, temp_path = tempfile.mkstemp(
                dir=str(self.client_previews_dir),
                prefix=f".screen_{screen_id}_",
                suffix=extension,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, target)
                alternate = self.client_previews_dir / (
                    f"screen_{screen_id}.jpg" if extension == ".png" else f"screen_{screen_id}.png"
                )
                if alternate.is_file():
                    alternate.unlink()
            except BaseException:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

            metadata = {
                "screen_id": screen_id,
                "request_token": request_token,
                "captured_at": time.time(),
                "size": len(content),
                "url": f"/api/scenes/screenshot/{screen_id}?v={request_token}",
            }
            self._client_screenshots[screen_id] = metadata
            self._screenshot_requests.pop(screen_id, None)
            return {"success": True, **metadata}

    def get_client_screenshot_status(self, screen_id: int) -> dict:
        screen_id = int(screen_id)
        with self._lock:
            pending = self._screenshot_requests.get(screen_id)
            latest = self._client_screenshots.get(screen_id)
            return {
                "screen_id": screen_id,
                "status": "waiting" if pending else ("ready" if latest else "empty"),
                "request_token": pending.get("token") if pending else None,
                "latest": copy.deepcopy(latest),
            }

    def resolve_client_screenshot(self, screen_id: int) -> Optional[Path]:
        for extension in (".png", ".jpg"):
            candidate = self.client_previews_dir / f"screen_{int(screen_id)}{extension}"
            if candidate.is_file():
                return candidate
        return None

    def assets_for_document(self, document: dict) -> list:
        """Clienta yalnızca yayınlanan belgede gerçekten kullanılan assetleri gönder."""
        used_names = {
            str(element.get("asset", ""))
            for scene in document.get("scenes", {}).values()
            for element in scene.get("elements", [])
            if element.get("type") == "sprite"
            and element.get("asset")
            and Path(str(element.get("asset"))).suffix.lower() in IMAGE_ASSET_EXTENSIONS
        }
        return [
            asset
            for asset in self.list_assets()
            if asset["name"] in used_names
        ]

    @staticmethod
    def sanitize_asset_name(filename: str) -> str:
        filename = Path(filename).name
        safe_name = SAFE_ASSET_NAME.sub("_", filename).strip("._")
        if not safe_name:
            raise SceneValidationError("Geçerli bir asset dosya adı gerekli")
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_ASSET_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_ASSET_EXTENSIONS))
            raise SceneValidationError(f"Asset uzantısı desteklenmiyor: {allowed}")
        return safe_name

    def save_asset(self, filename: str, content: bytes, optimize: bool = True) -> dict:
        if not content:
            raise SceneValidationError("Asset dosyası boş")
        if len(content) > 15 * 1024 * 1024:
            raise SceneValidationError("Asset en fazla 15 MB olabilir")
        safe_name = self.sanitize_asset_name(filename)
        target = self.assets_dir / safe_name
        original_size = len(content)
        optimization = {"optimized": False, "original_size": original_size}
        if optimize and target.suffix.lower() in IMAGE_ASSET_EXTENSIONS:
            content, optimization = self._optimize_image(
                content, target.suffix.lower(), original_size
            )
        fd, temp_path = tempfile.mkstemp(
            dir=str(self.assets_dir),
            prefix=".asset_",
            suffix=target.suffix,
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
        return {**self._asset_info(target), "optimization": optimization}

    @staticmethod
    def _optimize_image(content: bytes, extension: str, original_size: int) -> tuple:
        if Image is None:
            return content, {
                "optimized": False,
                "original_size": original_size,
                "warning": "Pillow kurulu değil; orijinal dosya saklandı",
            }
        try:
            with Image.open(BytesIO(content)) as image:
                image.load()
                original_dimensions = list(image.size)
                if max(image.size) > 2048:
                    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                output = BytesIO()
                save_kwargs = {}
                image_format = {
                    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                    ".webp": "WEBP", ".bmp": "BMP",
                }[extension]
                if image_format == "JPEG":
                    if image.mode not in ("RGB", "L"):
                        image = image.convert("RGB")
                    save_kwargs = {"quality": 88, "optimize": True, "progressive": True}
                elif image_format == "PNG":
                    save_kwargs = {"optimize": True, "compress_level": 9}
                elif image_format == "WEBP":
                    save_kwargs = {"quality": 88, "method": 6}
                image.save(output, format=image_format, **save_kwargs)
                optimized = output.getvalue()
                resized = list(image.size) != original_dimensions
                if len(optimized) >= original_size and not resized:
                    optimized = content
                return optimized, {
                    "optimized": optimized is not content,
                    "original_size": original_size,
                    "stored_size": len(optimized),
                    "saved_bytes": max(0, original_size - len(optimized)),
                    "original_dimensions": original_dimensions,
                    "stored_dimensions": list(image.size),
                }
        except Exception as exc:
            raise SceneValidationError(f"Görsel okunamadı: {exc}") from exc

    def _asset_info(self, path: Path) -> dict:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        info = {
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": digest,
            "url": f"/api/scene-assets/{path.name}",
            "kind": "audio" if path.suffix.lower() in AUDIO_ASSET_EXTENSIONS else "image",
        }
        if info["kind"] == "image" and Image is not None:
            try:
                with Image.open(path) as image:
                    info.update({"width": image.width, "height": image.height, "format": image.format})
            except Exception:
                info["warning"] = "Görsel metadata bilgisi okunamadı"
        elif path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as audio:
                    info["duration_seconds"] = round(
                        audio.getnframes() / max(1, audio.getframerate()), 3
                    )
            except (wave.Error, OSError):
                info["warning"] = "WAV süre bilgisi okunamadı"
        return info
    def list_assets(self) -> list:
        paths = {}
        if self.bundled_assets_dir.is_dir():
            paths.update({
                path.name: path
                for path in self.bundled_assets_dir.iterdir()
                if path.is_file() and path.suffix.lower() in ALLOWED_ASSET_EXTENSIONS
            })
        paths.update({
            path.name: path
            for path in self.assets_dir.iterdir()
            if path.is_file() and path.suffix.lower() in ALLOWED_ASSET_EXTENSIONS
        })
        return [self._asset_info(paths[name]) for name in sorted(paths)]

    def resolve_asset(self, filename: str) -> Optional[Path]:
        try:
            safe_name = self.sanitize_asset_name(filename)
        except SceneValidationError:
            return None
        uploaded = self.assets_dir / safe_name
        if uploaded.is_file():
            return uploaded
        bundled = self.bundled_assets_dir / safe_name
        return bundled if bundled.is_file() else None
