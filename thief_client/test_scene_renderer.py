"""Pygame sahne renderer testleri."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from lib.scene_renderer import SceneRenderer, parse_color


def make_document():
    return {
        "schema_version": 1,
        "canvas": {"width": 1920, "height": 1080, "background": "#282c34"},
        "scenes": {
            "waiting": {
                "name": "Bekleme",
                "background": "#102030",
                "elements": [
                    {
                        "id": "card",
                        "type": "rect",
                        "x": 200,
                        "y": 200,
                        "width": 600,
                        "height": 300,
                        "z": 0,
                        "fill": "#ffe038",
                        "stroke": "#6923a5",
                        "stroke_width": 8,
                        "radius": 20,
                    },
                    {
                        "id": "label",
                        "type": "text",
                        "x": 250,
                        "y": 260,
                        "width": 500,
                        "height": 160,
                        "z": 1,
                        "text": "SKOR {score}",
                        "font_size": 70,
                        "font_weight": "bold",
                        "align": "center",
                        "color": "#ec2d4c",
                        "outline_color": "#6923a5",
                        "outline_width": 3,
                    },
                ],
            }
        },
    }


def setup_module():
    pygame.init()
    pygame.display.set_mode((1, 1))


def teardown_module():
    pygame.quit()


def test_parse_color_supports_alpha_hex():
    assert parse_color("#ffe038") == (255, 224, 56)
    assert parse_color("#1e0a33aa") == (30, 10, 51, 170)
    assert parse_color("transparent") == (0, 0, 0, 0)


def test_renderer_draws_scaled_scene_and_dynamic_text():
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-1", make_document())
    target = pygame.Surface((960, 540))

    assert renderer.draw(target, "waiting", {"score": 12}) is True
    assert target.get_at((0, 0))[:3] == (16, 32, 48)
    assert target.get_at((150, 150))[:3] == (255, 224, 56)
    assert renderer.has_scene("waiting") is True
    assert renderer.has_scene("missing") is False


def test_apply_sets_preview_and_missing_sprite_is_safe():
    document = make_document()
    document["scenes"]["waiting"]["elements"].append({
        "id": "sprite",
        "type": "sprite",
        "asset": "missing.png",
        "x": 1000,
        "y": 200,
        "width": 300,
        "height": 300,
        "z": 2,
    })
    renderer = SceneRenderer(960, 540)
    renderer.apply("draft-2", document, preview_scene="waiting")
    target = pygame.Surface((960, 540))

    renderer.draw(target, "waiting")
    assert renderer.preview_scene == "waiting"
    assert target.get_at((550, 150))[:3] == (74, 44, 98)

def test_anchor_layout_preserves_edge_and_center_on_non_widescreen_target():
    renderer = SceneRenderer(1000, 1000)
    renderer.apply("published-1", make_document())

    right = renderer._element_rect({
        "x": 1720, "y": 50, "width": 100, "height": 100,
        "anchor_x": "right", "anchor_y": "top",
    })
    centered = renderer._element_rect({
        "x": 860, "y": 490, "width": 200, "height": 100,
        "anchor_x": "center", "anchor_y": "center",
    })
    stretched = renderer._element_rect({
        "x": 100, "y": 100, "width": 1720, "height": 100,
        "anchor_x": "stretch", "anchor_y": "top",
    })

    uniform_scale = min(1000 / 1920, 1000 / 1080)
    assert abs(right.right - (1000 - round(100 * uniform_scale))) <= 1
    assert abs(centered.centerx - 500) <= 1
    assert stretched.left == round(100 * uniform_scale)
    assert abs(stretched.right - (1000 - round(100 * uniform_scale))) <= 1

def test_keyframe_interpolation_and_scene_event_rules():
    document = make_document()
    document["scenes"]["bonus"] = {
        "name": "Bonus", "background": "transparent", "elements": []
    }
    document["rules"] = [
        {"id": "bonus_rule", "scene_id": "bonus", "event": "score_gte", "value": 10, "priority": 5}
    ]
    element = document["scenes"]["waiting"]["elements"][0]
    element["keyframes"] = [
        {"time": 1.0, "x": 400, "opacity": 0.5, "easing": "linear"}
    ]
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-4", document)

    animated = renderer._animated_element(element, 0.5)

    assert animated["x"] == 300
    assert animated["opacity"] == 0.75
    assert renderer.resolve_scene("gameplay", {"score": 12}) == "bonus"
    assert renderer.resolve_scene("gameplay", {"score": 2}) == "gameplay"


def test_hidden_element_is_not_rendered():
    document = make_document()
    document["scenes"]["waiting"]["elements"][0]["hidden"] = True
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-4", document)
    target = pygame.Surface((960, 540))

    renderer.draw(target, "waiting")

    assert target.get_at((150, 150))[:3] == (16, 32, 48)

def test_layer_folder_visibility_and_filter_pipeline():
    document = make_document()
    document["scenes"]["waiting"]["layer_groups"] = [
        {"id": "hidden_folder", "hidden": True, "opacity": 0.5, "blend_mode": "multiply"}
    ]
    document["scenes"]["waiting"]["elements"][0]["folder_id"] = "hidden_folder"
    document["scenes"]["waiting"]["elements"][1]["filters"] = {
        "brightness": 1.1, "contrast": 1.1, "saturation": 0.8, "blur": 1
    }
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-5", document)
    target = pygame.Surface((960, 540))

    assert renderer.draw(target, "waiting", {"score": 5}) is True
    assert target.get_at((150, 150))[:3] == (16, 32, 48)

def test_screen_quota_tokens_are_replaced():
    text = SceneRenderer._replace_tokens(
        "{screen_score}/{screen_target} - {screen_remaining} - {screen_complete}",
        {
            "screen_score": 7,
            "screen_target": 12,
            "screen_remaining": 5,
            "screen_complete": False,
        },
    )

    assert text == "7/12 - 5 - False"


def test_surface_cache_reuses_entries_and_stays_bounded():
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-cache", make_document())
    source = pygame.Surface((320, 180), pygame.SRCALPHA)
    source.fill((200, 100, 40, 255))
    filters = {"brightness": 1.1, "contrast": 1.1, "saturation": 0.9, "blur": 2}

    first = renderer._apply_filters(source, filters)
    second = renderer._apply_filters(source, filters)

    assert first is second
    assert renderer._surface_cache_bytes <= renderer._surface_cache_limit


def test_low_quality_disables_blur_filter():
    renderer = SceneRenderer(960, 540)
    renderer.set_quality("low")
    source = pygame.Surface((64, 64), pygame.SRCALPHA)

    filtered = renderer._apply_filters(source, {"blur": 12})

    assert filtered is source


def test_minimal_quality_skips_scale_effect_and_confetti(monkeypatch):
    document = make_document()
    document["scenes"]["waiting"]["elements"][1]["effect"] = "scale"
    document["scenes"]["waiting"]["elements"].append({
        "id": "confetti",
        "type": "confetti",
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 1080,
        "amount": 180,
    })
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-minimal", document)
    renderer.set_quality("minimal")
    target = pygame.Surface((960, 540))

    def unexpected_scale(*_args, **_kwargs):
        raise AssertionError("minimal quality must not scale animated elements")

    monkeypatch.setattr(pygame.transform, "scale", unexpected_scale)

    assert renderer.draw(target, "waiting", {"score": 1}) is True


def test_minimal_scene_static_detection_ignores_disabled_effects():
    document = make_document()
    document["scenes"]["waiting"]["elements"][1]["effect"] = "pulse"
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-static", document)

    assert renderer.is_scene_static("waiting") is False

    renderer.set_quality("minimal")

    assert renderer.is_scene_static("waiting") is True


def test_scene_static_detection_rejects_keyframes_and_sprite_sheets():
    document = make_document()
    renderer = SceneRenderer(960, 540)
    renderer.apply("published-static", document)
    assert renderer.is_scene_static("waiting") is True

    document["scenes"]["waiting"]["elements"][0]["keyframes"] = [
        {"time": 1, "x": 300}
    ]
    renderer.apply("published-keyframes", document)
    assert renderer.is_scene_static("waiting") is False

    document["scenes"]["waiting"]["elements"][0].pop("keyframes")
    document["scenes"]["waiting"]["elements"][0]["sprite_sheet"] = {
        "columns": 2,
        "rows": 1,
        "start": 0,
        "end": 1,
    }
    renderer.apply("published-sheet", document)
    assert renderer.is_scene_static("waiting") is False