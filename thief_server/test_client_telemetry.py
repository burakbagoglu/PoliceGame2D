from client_telemetry import ClientTelemetryStore


def test_heartbeat_is_sanitized_and_listed_online():
    store = ClientTelemetryStore(offline_after_seconds=15)
    result = store.update(2, {
        "fps": 31.26,
        "memory_mb": 123.45,
        "cpu_temp_c": 54.38,
        "frame_time_p95_ms": 35.46,
        "performance_profile": "pi_zero_2w",
        "quality_level": "low",
        "render_width": 1280,
        "render_height": 720,
        "serial_connected": True,
        "piezo": {"latest": 90, "peak": 410, "samples": [1, 2, 5000]},
    })

    assert result["online"] is True
    assert result["fps"] == 31.3
    assert result["frame_time_p95_ms"] == 35.5
    assert result["performance_profile"] == "pi_zero_2w"
    assert result["quality_level"] == "low"
    assert result["render_width"] == 1280
    assert result["render_height"] == 720
    assert result["piezo"]["samples"] == [1, 2, 4095]
    listing = store.list(num_screens=3)
    assert listing["online_count"] == 1
    assert listing["clients"][1]["screen_id"] == 2


def test_missing_heartbeats_are_offline():
    store = ClientTelemetryStore()
    listing = store.list(num_screens=2)

    assert listing["online_count"] == 0
    assert all(client["online"] is False for client in listing["clients"])
