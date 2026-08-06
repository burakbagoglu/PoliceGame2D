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


def test_active_session_is_restored_after_manager_restart(tmp_path):
    first = PhotoSessionManager(tmp_path, capture_backend=fake_capture)
    session = first.start_session(
        "Elektrik Kesintisi",
        capture_enabled=True,
        consent_confirmed=True,
    )
    first.shutdown()

    restored = PhotoSessionManager(tmp_path, capture_backend=fake_capture)
    restored.initialize()

    assert restored.current_session_id == session["id"]
    assert restored.get_current()["status"] == "active"
    restored.shutdown()


def test_retention_cleanup_skips_active_and_sold_sessions(tmp_path):
    manager = PhotoSessionManager(
        tmp_path,
        camera_config={"retention_days": 1, "protect_sold": True},
        capture_backend=fake_capture,
    )
    old = manager.start_session("Eski")
    manager.end_session("manual", completed=False)
    old_metadata = manager._read_metadata(old["id"])
    old_metadata["ended_at"] = "2020-01-01T00:00:00+00:00"
    manager._write_metadata(old_metadata)

    sold = manager.start_session("Satılan")
    manager.end_session("manual", completed=False)
    sold_metadata = manager._read_metadata(sold["id"])
    sold_metadata["ended_at"] = "2020-01-01T00:00:00+00:00"
    sold_metadata["sold"] = True
    manager._write_metadata(sold_metadata)

    active = manager.start_session("Aktif")
    preview = manager.cleanup_expired(dry_run=True)

    assert preview["candidates"] == [old["id"]]
    result = manager.cleanup_expired(dry_run=False)
    assert result["removed"] == [old["id"]]
    assert manager.get_session(sold["id"])["sold"] is True
    assert manager.get_session(active["id"])["status"] == "active"
    manager.shutdown()

def test_initialize_applies_automatic_retention_cleanup(tmp_path):
    first = PhotoSessionManager(
        tmp_path,
        camera_config={"retention_days": 1, "auto_cleanup": False},
        capture_backend=fake_capture,
    )
    expired = first.start_session("Suresi Dolan")
    first.end_session("manual", completed=False)
    metadata = first._read_metadata(expired["id"])
    metadata["ended_at"] = "2020-01-01T00:00:00+00:00"
    first._write_metadata(metadata)
    first.shutdown()

    restored = PhotoSessionManager(
        tmp_path,
        camera_config={"retention_days": 1, "auto_cleanup": True},
        capture_backend=fake_capture,
    )
    restored.initialize()

    assert restored.list_sessions() == []
    restored.shutdown()
