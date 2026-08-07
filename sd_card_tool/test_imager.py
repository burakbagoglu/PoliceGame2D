from pathlib import Path

import pytest

from sd_card_tool.imager import build_imager_arguments, parse_progress, validate_image_source
from sd_card_tool.windows_disks import DiskDevice


def disk(**overrides):
    values = dict(number=4, friendly_name="SD", serial_number="A", bus_type="USB", size=32_000_000_000, is_boot=False, is_system=False, operational_status="Online")
    values.update(overrides)
    return DiskDevice(**values)


def test_imager_arguments_keep_system_drive_override_disabled(tmp_path):
    image = tmp_path / "os.img"
    image.write_bytes(b"image")
    script = tmp_path / "firstrun.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    args = build_imager_arguments(str(image), disk(), script)
    assert args[:4] == ["--cli", "--disable-telemetry", "--first-run-script", str(script.resolve())]
    assert args[-1] == r"\\.\PhysicalDrive4"
    assert "--enable-writing-system-drives" not in args


def test_system_disk_is_rejected(tmp_path):
    script = tmp_path / "firstrun.sh"
    script.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="sistem"):
        build_imager_arguments("https://example.com/os.img", disk(is_system=True), script)


def test_progress_parser():
    assert parse_progress("Writing: [---->] 42 %") == ("Yaziliyor", 42)
    assert parse_progress("Verifying: 100 %") == ("Dogrulaniyor", 100)
    assert parse_progress("Preparing destination") is None

def test_remote_image_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_image_source("http://example.com/os.img")
    assert validate_image_source("https://example.com/os.img").startswith("https://")
