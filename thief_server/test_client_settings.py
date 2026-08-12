import json

from client_settings import ClientSettingsStore


def test_settings_are_versioned_persisted_and_delivered(tmp_path):
    path = tmp_path / "client_settings.json"
    store = ClientSettingsStore(path)
    first = store.set(3, {"fps": 30, "render_width": 1280})

    assert first["revision"] == 1
    assert store.payload(3, 0) == {
        "changed": True,
        "revision": 1,
        "settings": {"fps": 30, "render_width": 1280},
    }
    assert store.payload(3, 1) == {"changed": False, "revision": 1}

    reloaded = ClientSettingsStore(path)
    assert reloaded.get(3) == first
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_each_screen_has_an_independent_revision(tmp_path):
    store = ClientSettingsStore(tmp_path / "settings.json")
    assert store.set(1, {"fps": 30})["revision"] == 1
    assert store.set(1, {"fps": 28})["revision"] == 2
    assert store.set(2, {"fps": 24})["revision"] == 1
