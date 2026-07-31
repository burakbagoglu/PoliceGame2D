import json
import time
import zipfile

import pytest
from PIL import Image

from photo_manager import PhotoSessionManager


def fake_capture(path):
    Image.new("RGB", (960, 540), (40, 120, 210)).save(path, "JPEG", quality=90)


def wait_for_photos(manager, session_id, count, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = manager.get_session(session_id)
        if len(session["photos"]) >= count:
            return session
        time.sleep(0.02)
    return manager.get_session(session_id)


def test_capture_requires_explicit_consent(tmp_path):
    manager = PhotoSessionManager(tmp_path, capture_backend=fake_capture)

    with pytest.raises(ValueError, match="onay"):
        manager.start_session("Grup A", capture_enabled=True, consent_confirmed=False)


def test_session_capture_is_persisted_once_per_screen(tmp_path):
    manager = PhotoSessionManager(tmp_path, capture_backend=fake_capture)
    session = manager.start_session(
        "Ece'nin doğum günü",
        capture_enabled=True,
        consent_confirmed=True,
        child_count=5,
        duration_minutes=35,
        screen_targets={screen_id: 12 for screen_id in range(1, 9)},
    )

    assert manager.capture_screen(1) is True
    assert manager.capture_screen(1) is False
    captured = wait_for_photos(manager, session["id"], 1)

    assert len(captured["photos"]) == 1
    assert captured["photos"][0]["screen_id"] == 1
    assert manager.get_photo_path(session["id"], captured["photos"][0]["filename"]).is_file()
    assert manager.get_photo_path(session["id"], captured["photos"][0]["thumbnail"]).is_file()
    assert manager.capture_screen(1) is False
    manager.shutdown()


def test_sale_update_zip_and_delete(tmp_path):
    manager = PhotoSessionManager(tmp_path, capture_backend=fake_capture)
    session = manager.start_session("Satış Oturumu", capture_enabled=True, consent_confirmed=True)
    manager.capture_screen(3)
    captured = wait_for_photos(manager, session["id"], 1)
    manager.end_session("manual", completed=False)

    updated = manager.update_sale(
        session["id"], sold=True, sale_price=250.5, customer_name="Aile 1"
    )
    zip_path, download_name = manager.build_download_zip(session["id"])

    assert updated["sold"] is True
    assert updated["sale_price"] == 250.5
    assert download_name.endswith(".zip")
    with zipfile.ZipFile(zip_path) as archive:
        assert "oturum.json" in archive.namelist()
        assert captured["photos"][0]["filename"] in archive.namelist()
        metadata = json.loads(archive.read("oturum.json"))
        assert metadata["name"] == "Satış Oturumu"
    zip_path.unlink()

    manager.delete_session(session["id"])
    assert manager.list_sessions() == []
    manager.shutdown()


def test_invalid_paths_are_rejected(tmp_path):
    manager = PhotoSessionManager(tmp_path, capture_backend=fake_capture)
    session = manager.start_session("Güvenlik")
    manager.end_session("manual", completed=False)

    with pytest.raises(ValueError):
        manager.get_photo_path(session["id"], "../session.json")
    with pytest.raises(ValueError):
        manager.get_session("../../etc")

    manager.shutdown()
