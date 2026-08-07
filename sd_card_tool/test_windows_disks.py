import json

from sd_card_tool.windows_disks import parse_disk_json


def test_only_safe_removable_disks_are_returned():
    payload = json.dumps([
        {"Number": 0, "FriendlyName": "Windows SSD", "SerialNumber": "SYS", "BusType": "NVMe", "Size": 500_000_000_000, "IsBoot": True, "IsSystem": True, "OperationalStatus": "Online"},
        {"Number": 2, "FriendlyName": "SD Reader", "SerialNumber": "CARD1", "BusType": "USB", "Size": 32_000_000_000, "IsBoot": False, "IsSystem": False, "OperationalStatus": "Online"},
        {"Number": 3, "FriendlyName": "Tiny", "SerialNumber": "", "BusType": "SD", "Size": 500_000_000, "IsBoot": False, "IsSystem": False, "OperationalStatus": "Online"},
    ])
    devices = parse_disk_json(payload)
    assert [device.number for device in devices] == [2]
    assert devices[0].device_path == r"\\.\PhysicalDrive2"
    assert "32.0 GB" in devices[0].display_name


def test_single_disk_json_object_is_supported():
    payload = json.dumps({"Number": 5, "FriendlyName": "Card", "SerialNumber": None, "BusType": "MMC", "Size": 16_000_000_000, "IsBoot": False, "IsSystem": False, "OperationalStatus": "Online"})
    assert parse_disk_json(payload)[0].number == 5