from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable
from pathlib import Path


_ALLOWED_BUS_TYPES = {"USB", "SD", "MMC"}


@dataclass(frozen=True)
class DiskDevice:
    number: int
    friendly_name: str
    serial_number: str
    bus_type: str
    size: int
    is_boot: bool
    is_system: bool
    operational_status: str

    @property
    def device_path(self) -> str:
        return rf"\\.\PhysicalDrive{self.number}"

    @property
    def size_gb(self) -> float:
        return self.size / 1_000_000_000

    @property
    def display_name(self) -> str:
        serial = f" · {self.serial_number}" if self.serial_number else ""
        return f"Disk {self.number} · {self.friendly_name} · {self.size_gb:.1f} GB · {self.bus_type}{serial}"

    @property
    def safe_for_imaging(self) -> bool:
        return (
            not self.is_boot
            and not self.is_system
            and self.bus_type.upper() in _ALLOWED_BUS_TYPES
            and self.size >= 1_000_000_000
        )


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_disk_json(payload: str) -> list[DiskDevice]:
    if not payload.strip():
        return []
    decoded = json.loads(payload)
    rows: Iterable[dict] = [decoded] if isinstance(decoded, dict) else decoded
    devices = []
    for row in rows:
        try:
            device = DiskDevice(
                number=int(row["Number"]),
                friendly_name=str(row.get("FriendlyName") or "Bilinmeyen aygit").strip(),
                serial_number=str(row.get("SerialNumber") or "").strip(),
                bus_type=str(row.get("BusType") or "").strip().upper(),
                size=int(row.get("Size") or 0),
                is_boot=_as_bool(row.get("IsBoot")),
                is_system=_as_bool(row.get("IsSystem")),
                operational_status=str(row.get("OperationalStatus") or "").strip(),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if device.safe_for_imaging:
            devices.append(device)
    return sorted(devices, key=lambda item: item.number)


def _powershell_executable() -> str:
    found = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if found:
        return found
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("Windows PowerShell bulunamadi.")


def list_removable_disks() -> list[DiskDevice]:
    script = r'''[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Disk | ForEach-Object {
        [PSCustomObject]@{
            Number = $_.Number
            FriendlyName = $_.FriendlyName
            SerialNumber = $_.SerialNumber
            BusType = $_.BusType.ToString()
            Size = $_.Size
            IsBoot = $_.IsBoot
            IsSystem = $_.IsSystem
            OperationalStatus = ($_.OperationalStatus -join ',')
        }
    } | ConvertTo-Json -Compress'''
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "Windows disk listesi okunamadi."
        raise RuntimeError(message)
    return parse_disk_json(completed.stdout.lstrip("\ufeff"))