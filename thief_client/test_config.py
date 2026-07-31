"""Client configuration and Pi Zero 2 W profile tests."""

from lib.config import GameConfig


def test_pi_zero_profile_is_the_default(monkeypatch):
    monkeypatch.delenv("THIEF_PERFORMANCE_PROFILE", raising=False)

    config = GameConfig.from_dict({})

    assert config.performance_profile == "pi_zero_2w"
    assert (config.render_width, config.render_height) == (1280, 720)
    assert config.adaptive_quality is True
    assert config.min_fps == 24.0


def test_balanced_profile_selects_its_internal_resolution(monkeypatch):
    monkeypatch.setenv("THIEF_PERFORMANCE_PROFILE", "balanced")

    config = GameConfig.from_dict({})

    assert config.performance_profile == "balanced"
    assert (config.render_width, config.render_height) == (1600, 900)
    assert config.min_fps == 26.0


def test_explicit_render_settings_are_clamped(monkeypatch):
    monkeypatch.delenv("THIEF_PERFORMANCE_PROFILE", raising=False)

    config = GameConfig.from_dict({
        "render_width": 100,
        "render_height": 99999,
        "min_fps": 999,
    })

    assert config.render_width == 320
    assert config.render_height == 1080
    assert config.min_fps == 60.0