from __future__ import annotations

import ctypes
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from .windows_disks import DiskDevice


_PROGRESS_RE = re.compile(r"\b(Writing|Verifying)\b.*?\b(\d{1,3})\s*%", re.IGNORECASE)


def is_windows_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def discover_imager() -> Optional[Path]:
    names = ("rpi-imager.exe", "imager.exe", "rpi-imager", "imager")
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relative_candidates = (
        Path("Raspberry Pi Imager") / "rpi-imager.exe",
        Path("Raspberry Pi Imager") / "imager.exe",
        Path("Programs") / "Raspberry Pi Imager" / "rpi-imager.exe",
        Path("Programs") / "Raspberry Pi Imager" / "imager.exe",
    )
    for root in filter(None, roots):
        for relative in relative_candidates:
            candidate = Path(root) / relative
            if candidate.is_file():
                return candidate.resolve()
    return None


def validate_image_source(value: str) -> str:
    value = str(value or "").strip()
    if value.lower().startswith("https://"):
        return value
    if value.lower().startswith("http://"):
        raise ValueError("Uzak OS imaji guvenlik icin HTTPS kullanmali.")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError("OS imaj dosyasi bulunamadi.")
    if path.suffix.lower() not in {".img", ".zip", ".xz", ".gz", ".zst"}:
        raise ValueError("OS imaji .img, .zip, .xz, .gz veya .zst olmali.")
    return str(path)


def build_imager_arguments(
    image_source: str,
    target: DiskDevice,
    first_run_script: str | Path,
) -> list[str]:
    if not target.safe_for_imaging:
        raise ValueError("Hedef disk cikarilabilir ve sistem diski olmayan bir aygit olmali.")
    script = Path(first_run_script).resolve()
    if not script.is_file():
        raise ValueError("Ilk acilis scripti bulunamadi.")
    return [
        "--cli",
        "--disable-telemetry",
        "--first-run-script",
        str(script),
        validate_image_source(image_source),
        target.device_path,
    ]


def parse_progress(line: str) -> tuple[str, int] | None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    phase = "Yaziliyor" if match.group(1).lower() == "writing" else "Dogrulaniyor"
    return phase, min(100, max(0, int(match.group(2))))