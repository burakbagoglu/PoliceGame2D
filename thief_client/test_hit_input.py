from lib.hit_input import HitInput, KeyboardHitInput


def test_piezo_samples_are_bounded_and_peak_resets():
    hit_input = HitInput(debug=False)
    hit_input._record_piezo_sample(120)
    hit_input._record_piezo_sample(5000)

    first = hit_input.get_telemetry()
    second = hit_input.get_telemetry()

    assert first["latest"] == 4095
    assert first["peak"] == 4095
    assert first["samples"] == [120, 4095]
    assert second["peak"] == 4095


def test_keyboard_input_implements_telemetry_interface():
    hit_input = KeyboardHitInput()
    hit_input.simulate_hit()

    assert hit_input.get_hit() is True
    assert hit_input.get_telemetry()["hit_count"] == 1
