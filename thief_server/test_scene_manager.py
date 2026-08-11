"""Sahne taslak/yayın/asset altyapısı testleri."""

import copy
from io import BytesIO

import pytest
from PIL import Image

from scene_manager import (
    SceneManager,
    SceneRevisionConflict,
    SceneValidationError,
    default_scene_document,
)


def test_initial_state_contains_editable_game_scenes(tmp_path):
    manager = SceneManager(tmp_path)
    state = manager.get_editor_state()

    assert set(("waiting", "intro", "countdown", "gameplay", "win", "lose")) <= set(
        state["draft"]["scenes"]
    )
    assert state["draft_revision"] == 1
    assert state["published_version"] == 1


def test_save_publish_and_client_change_detection(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    document["scenes"]["waiting"]["name"] = "Yeni Bekleme"

    saved = manager.save_draft(document)
    assert saved["draft_revision"] == 2
    published = manager.publish()
    assert published["published_version"] == 2

    first = manager.get_client_payload(1)
    assert first["changed"] is True
    assert first["version"] == "published-2"
    assert first["document"]["scenes"]["waiting"]["name"] == "Yeni Bekleme"

    unchanged = manager.get_client_payload(1, known_version="published-2")
    assert unchanged == {
        "changed": False,
        "version": "published-2",
        "preview": False,
        "preview_scene": None,
        "screenshot_request": None,
    }


def test_preview_is_isolated_per_screen(tmp_path):
    manager = SceneManager(tmp_path)
    manager.set_preview(3, "win")

    preview = manager.get_client_payload(3)
    normal = manager.get_client_payload(2)

    assert preview["preview"] is True
    assert preview["preview_scene"] == "win"
    assert preview["version"].startswith("draft-")
    assert normal["preview"] is False
    assert normal["version"].startswith("published-")

    manager.clear_preview(3)
    assert manager.get_client_payload(3)["preview"] is False


def test_rollback_loads_history_into_draft(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    document["scenes"]["waiting"]["name"] = "Sürüm İki"
    manager.save_draft(document)
    version = manager.publish()["published_version"]

    newer = copy.deepcopy(document)
    newer["scenes"]["waiting"]["name"] = "Henüz Yayınlanmadı"
    manager.save_draft(newer)
    manager.rollback(version)

    assert manager.get_editor_state()["draft"]["scenes"]["waiting"]["name"] == "Sürüm İki"


def test_schema_one_draft_migrates_to_builtin_background(tmp_path):
    document = default_scene_document()
    document["schema_version"] = 1
    document["canvas"].pop("background_asset", None)
    document["scenes"]["waiting"]["background"] = "#282c34"
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "draft.json").write_text(
        __import__("json").dumps(document),
        encoding="utf-8",
    )

    manager = SceneManager(tmp_path)
    migrated = manager.get_editor_state()["draft"]

    assert migrated["schema_version"] == 7
    assert "jail" in migrated["scenes"]
    assert any(item["id"] == "screen_quota_text" for item in migrated["scenes"]["gameplay"]["elements"])
    assert migrated["canvas"]["background_asset"] == "__client_background__"
    assert migrated["scenes"]["waiting"]["background"] == "transparent"
    assert migrated["scenes"]["waiting"]["elements"][0]["anchor_x"] == "scale"
    assert migrated["scenes"]["waiting"]["elements"][0]["anchor_y"] == "scale"


def test_jail_scene_uses_bundled_assets_without_scoreboard(tmp_path):
    manager = SceneManager(tmp_path)
    jail = manager.get_editor_state()["draft"]["scenes"]["jail"]
    elements = jail["elements"]

    assert {item["asset"] for item in elements if item["type"] == "sprite"} == {
        "jail_background.png",
        "jail_thief_grabbars.png",
    }
    assert not any(item["type"] == "score" or item["id"] == "score_widget" for item in elements)
    assert manager.resolve_asset("jail_background.png").is_file()
    assert manager.resolve_asset("jail_thief_grabbars.png").is_file()
    payload_assets = {item["name"] for item in manager.assets_for_document(manager.get_client_payload(1)["document"])}
    assert {"jail_background.png", "jail_thief_grabbars.png"} <= payload_assets


def test_v6_custom_jail_is_preserved_but_scoreboard_is_removed(tmp_path):
    document = default_scene_document()
    document["schema_version"] = 6
    document["scenes"]["jail"] = {
        "name": "Ozel Hapis",
        "background": "#000000",
        "elements": [
            {"id": "custom_label", "type": "text", "text": "Bekle", "x": 10, "y": 10,
             "width": 200, "height": 80, "z": 1},
            {"id": "score_widget", "type": "score", "x": 0, "y": 0,
             "width": 200, "height": 100, "z": 2},
        ],
    }
    (tmp_path / "draft.json").write_text(__import__("json").dumps(document), encoding="utf-8")

    jail = SceneManager(tmp_path).get_editor_state()["draft"]["scenes"]["jail"]

    assert jail["name"] == "Ozel Hapis"
    assert [item["id"] for item in jail["elements"]] == ["custom_label"]

def test_published_schema_migration_bumps_version_and_persists(tmp_path):
    document = default_scene_document()
    document["schema_version"] = 6
    document["scenes"]["jail"] = {
        "name": "Eski Hapis",
        "background": "#000000",
        "elements": [
            {"id": "jail_card", "type": "rect", "x": 0, "y": 0,
             "width": 100, "height": 100, "z": 0},
        ],
    }
    document["_published_version"] = 4
    (tmp_path / "published.json").write_text(__import__("json").dumps(document), encoding="utf-8")

    manager = SceneManager(tmp_path)
    stored = __import__("json").loads((tmp_path / "published.json").read_text(encoding="utf-8"))

    assert manager.get_client_payload(1)["version"] == "published-5"
    assert stored["schema_version"] == 7
    assert stored["_published_version"] == 5
    assert stored["scenes"]["jail"]["elements"][0]["id"] == "jail_background"

def test_client_screenshot_request_is_one_time_and_served(tmp_path):
    manager = SceneManager(tmp_path)
    request = manager.request_client_screenshot(4)

    payload = manager.get_client_payload(4, known_version="published-1")
    assert payload["changed"] is False
    assert payload["screenshot_request"] == request["request_token"]

    content = b"\x89PNG\r\n\x1a\npreview"
    saved = manager.save_client_screenshot(4, request["request_token"], content)
    assert saved["screen_id"] == 4
    assert manager.get_client_screenshot_status(4)["status"] == "ready"
    assert manager.resolve_client_screenshot(4).read_bytes() == content
    assert manager.get_client_payload(4, "published-1")["screenshot_request"] is None

def test_asset_is_sanitized_hashed_and_resolved(tmp_path):
    manager = SceneManager(tmp_path)
    asset = manager.save_asset("../Kötü isim.png", b"fake-png-content", optimize=False)

    assert asset["name"] == "K_t_isim.png"
    assert len(asset["sha256"]) == 64
    assert manager.resolve_asset(asset["name"]).read_bytes() == b"fake-png-content"
    assert manager.resolve_asset("../secret.png") is None



def test_v4_document_supports_prefabs_rules_timeline_audio_and_sprites(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    element = document["scenes"]["waiting"]["elements"][0]
    element["keyframes"] = [
        {"time": 0.5, "x": element["x"] + 20, "easing": "ease_out"}
    ]
    document["scenes"]["waiting"]["audio_cues"] = [
        {"id": "cue_1", "time": 0.2, "sound": "hit", "volume": 0.8}
    ]
    document["rules"] = [
        {"id": "rule_1", "scene_id": "waiting", "event": "score_gte", "value": 10}
    ]
    document["prefabs"] = {
        "card": {"name": "Card", "elements": [dict(element)]}
    }
    sprite = {
        "id": "sheet", "type": "sprite", "asset": "sheet.png",
        "x": 0, "y": 0, "width": 100, "height": 100,
        "sprite_sheet": {"columns": 4, "rows": 2, "fps": 8, "start": 0, "end": 7},
    }
    document["scenes"]["waiting"]["elements"].append(sprite)

    saved = manager.save_draft(document)
    audio = manager.save_asset("effect.ogg", b"fake-audio")

    assert saved["success"] is True
    assert audio["name"] == "effect.ogg"
    assert manager.get_editor_state()["draft"]["rules"][0]["event"] == "score_gte"

def test_draft_revision_conflict_prevents_silent_overwrite(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    document["scenes"]["waiting"]["name"] = "İlk sekme"

    saved = manager.save_draft(document, expected_revision=1)

    assert saved["draft_revision"] == 2
    with pytest.raises(SceneRevisionConflict) as conflict:
        manager.save_draft(default_scene_document(), expected_revision=1)
    assert conflict.value.actual == 2


def test_image_optimization_and_document_audit(tmp_path):
    manager = SceneManager(tmp_path)
    buffer = BytesIO()
    Image.new("RGBA", (2400, 1200), (255, 0, 0, 255)).save(buffer, format="PNG")

    asset = manager.save_asset("large.png", buffer.getvalue())
    document = default_scene_document()
    document["scenes"]["waiting"]["elements"].append({
        "id": "missing", "type": "sprite", "asset": "missing.png",
        "x": 0, "y": 0, "width": 10, "height": 10,
    })

    assert asset["width"] == 2048
    assert asset["optimization"]["optimized"] is True
    assert manager.audit_document(document)["errors"] == ["Eksik asset: missing.png"]

def test_rejects_invalid_scene_documents_and_assets(tmp_path):
    manager = SceneManager(tmp_path)
    invalid = default_scene_document()
    invalid["scenes"]["waiting"]["elements"][0]["width"] = 0

    with pytest.raises(SceneValidationError):
        manager.save_draft(invalid)
    with pytest.raises(SceneValidationError):
        manager.save_asset("script.exe", b"bad")
    with pytest.raises(SceneValidationError):
        manager.set_preview(1, "missing")


def test_v5_supports_profiles_layer_folders_paths_and_diff(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    scene = document["scenes"]["gameplay"]
    scene["layer_groups"] = [{"id": "actors", "name": "Oyuncular", "opacity": 0.8, "mask_element_id": "target"}]
    scene["elements"].append({
        "id": "target", "type": "hit_zone", "x": 700, "y": 700,
        "width": 500, "height": 200, "folder_id": "actors",
        "blend_mode": "screen", "filters": {"brightness": 1.1},
    })
    scene["elements"].append({
        "id": "route", "type": "path", "x": 100, "y": 500,
        "width": 1600, "height": 200,
        "points": [{"x": 0, "y": 100}, {"x": 800, "y": 20}, {"x": 1600, "y": 100}],
    })

    manager.save_draft(document)
    diff = manager.diff_summary()

    assert diff["has_changes"] is True
    assert any(item["id"] == "gameplay" for item in diff["scenes_changed"])
    assert document["game_profiles"]["standard"]["screen_count"] == 8


def test_v5_rejects_missing_layer_folder_and_short_path(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    document["scenes"]["gameplay"]["elements"].append({
        "id": "bad_path", "type": "path", "x": 0, "y": 0,
        "width": 100, "height": 100, "folder_id": "missing",
        "points": [{"x": 0, "y": 0}],
    })
    with pytest.raises(SceneValidationError):
        manager.save_draft(document)

def test_v5_rejects_invalid_filter_budget(tmp_path):
    manager = SceneManager(tmp_path)
    document = default_scene_document()
    document["scenes"]["waiting"]["elements"][0]["filters"] = {"blur": 999}
    with pytest.raises(SceneValidationError):
        manager.save_draft(document)