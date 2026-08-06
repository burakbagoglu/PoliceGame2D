"""Pi Zero direct-render presentation tests."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pygame


_SPEC = spec_from_file_location(
    "thief_client_game_main",
    Path(__file__).with_name("main.py"),
)
_CLIENT_MAIN = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CLIENT_MAIN)
ThiefGame = _CLIENT_MAIN.ThiefGame


def test_direct_render_present_skips_full_canvas_blit(monkeypatch):
    game = ThiefGame.__new__(ThiefGame)
    game.canvas = object()
    game.present_surface = None
    game.direct_render = True
    game.view_x = 0
    game.view_y = 0
    game.output_view_w = 1280
    game.output_view_h = 720
    game.screen = SimpleNamespace(
        blit=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("direct render must not blit the full canvas")
        )
    )
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    blit_ms, flip_ms = game._present()

    assert blit_ms >= 0
    assert flip_ms >= 0


def test_unchanged_frame_skips_display_work(monkeypatch):
    game = ThiefGame.__new__(ThiefGame)
    monkeypatch.setattr(
        pygame.display,
        "flip",
        lambda: (_ for _ in ()).throw(
            AssertionError("unchanged frame must not flip")
        ),
    )

    assert game._present([]) == (0.0, 0.0)
    assert game.render_mode == "static-frozen"
    assert game.updated_pixel_ratio == 0.0


def test_direct_render_updates_only_dirty_rectangles(monkeypatch):
    game = ThiefGame.__new__(ThiefGame)
    game.canvas = object()
    game.present_surface = None
    game.direct_render = True
    game.view_x = 10
    game.view_y = 20
    game.output_view_w = 1280
    game.output_view_h = 720
    game.screen = SimpleNamespace(
        get_rect=lambda: pygame.Rect(0, 0, 1280, 720),
        blit=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("direct render must not blit the full canvas")
        ),
    )
    updated = []
    monkeypatch.setattr(
        pygame.display,
        "update",
        lambda rects: updated.extend(rects),
    )
    monkeypatch.setattr(
        pygame.display,
        "flip",
        lambda: (_ for _ in ()).throw(
            AssertionError("dirty frame must not flip")
        ),
    )

    game._present([pygame.Rect(5, 6, 30, 40)])

    assert updated == [pygame.Rect(15, 26, 30, 40)]
    assert game.render_mode == "dirty-rect"
    assert game.dirty_rect_count == 1
    assert 0 < game.updated_pixel_ratio < 1


def test_dirty_update_falls_back_to_flip_when_driver_rejects_it(monkeypatch):
    game = ThiefGame.__new__(ThiefGame)
    game.canvas = object()
    game.present_surface = None
    game.direct_render = True
    game.view_x = 0
    game.view_y = 0
    game.output_view_w = 1280
    game.output_view_h = 720
    game.screen = SimpleNamespace(
        get_rect=lambda: pygame.Rect(0, 0, 1280, 720),
        blit=lambda *_args, **_kwargs: None,
    )
    flips = []
    monkeypatch.setattr(
        pygame.display,
        "update",
        lambda _rects: (_ for _ in ()).throw(pygame.error("unsupported")),
    )
    monkeypatch.setattr(pygame.display, "flip", lambda: flips.append(True))

    game._present([pygame.Rect(5, 6, 30, 40)])

    assert flips == [True]
    assert game.render_mode == "full-render"
    assert game.updated_pixel_ratio == 100.0