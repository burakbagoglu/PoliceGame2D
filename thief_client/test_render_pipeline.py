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
    game.screen = SimpleNamespace(
        blit=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("direct render must not blit the full canvas")
        )
    )
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    blit_ms, flip_ms = game._present()

    assert blit_ms >= 0
    assert flip_ms >= 0