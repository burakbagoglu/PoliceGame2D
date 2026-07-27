"""Server-side USB ses yöneticisi testleri."""
from types import SimpleNamespace

from audio_manager import AudioManager


class FakeSound:
    def __init__(self, path=None, buffer=None):
        self.path = path
        self.buffer = buffer
        self.volume = 1.0
        self.play_count = 0
        self.fade_count = 0

    def set_volume(self, volume, right=None):
        if right is None:
            self.volume = volume
        else:
            self.channel_volume = (volume, right)

    def play(self, loops=0, fade_ms=0):
        self.fade_ms = fade_ms
        self.play_count += 1
        self.loops = loops
        return self

    def fadeout(self, _milliseconds):
        self.fade_count += 1


class FakeMusic:
    def __init__(self):
        self.loaded = None
        self.volume = 1.0
        self.play_count = 0
        self.stop_count = 0
        self.fade_count = 0

    def load(self, path):
        self.loaded = path

    def set_volume(self, volume, right=None):
        if right is None:
            self.volume = volume
        else:
            self.channel_volume = (volume, right)

    def play(self, loops=0, fade_ms=0):
        self.fade_ms = fade_ms
        self.play_count += 1
        self.loops = loops

    def stop(self):
        self.stop_count += 1

    def fadeout(self, _milliseconds):
        self.fade_count += 1


class FakeChannel:
    def __init__(self):
        self.play_count = 0
        self.stop_count = 0
        self.fade_count = 0
        self.last_loops = None

    def play(self, _sound, loops=0):
        self.play_count += 1
        self.last_loops = loops

    def stop(self):
        self.stop_count += 1

    def fadeout(self, _milliseconds):
        self.fade_count += 1


class FakeMixer:
    def __init__(self):
        self.initialized = False
        self.init_kwargs = {}
        self.num_channels = 0
        self.reserved = 0
        self.music = FakeMusic()
        self.channel = FakeChannel()
        self.sounds = []

    def get_init(self):
        return self.initialized

    def init(self, **kwargs):
        self.initialized = True
        self.init_kwargs = kwargs

    def quit(self):
        self.initialized = False

    def set_num_channels(self, count):
        self.num_channels = count

    def set_reserved(self, count):
        self.reserved = count

    def Channel(self, _index):
        return self.channel

    def Sound(self, path=None, buffer=None):
        sound = FakeSound(path=path, buffer=buffer)
        self.sounds.append(sound)
        return sound


def make_manager(config=None, devices=None):
    mixer = FakeMixer()
    pygame_module = SimpleNamespace(mixer=mixer)
    base_config = {
        "enabled": True,
        "device_name": "auto-usb",
        "frequency": 8000,
        "output_channels": 1,
        "buffer": 256,
    }
    base_config.update(config or {})
    device_list = ["HDMI", "USB Audio Device"] if devices is None else devices
    manager = AudioManager(
        config=base_config,
        pygame_module=pygame_module,
        device_provider=lambda: list(device_list),
    )
    return manager, mixer


def test_initializes_preferred_usb_device_and_fallback_assets():
    manager, mixer = make_manager()

    assert manager.initialize() is True
    assert mixer.init_kwargs["devicename"] == "USB Audio Device"
    assert mixer.init_kwargs["frequency"] == 8000
    assert mixer.num_channels == 8
    assert mixer.reserved == 1
    assert manager.available is True
    assert manager.using_fallback_music is True
    assert all(manager.using_fallback_sfx.values())


def test_missing_usb_device_falls_back_to_silent_mode():
    manager, mixer = make_manager(devices=["HDMI Output"])

    assert manager.initialize() is False
    assert manager.available is False
    assert mixer.initialized is False
    assert "USB ses kartı bulunamadı" in manager.last_error


def test_alsa_proc_fallback_selects_usb_card(monkeypatch):
    manager, mixer = make_manager(devices=[])
    monkeypatch.setenv("AUDIODEV", "")
    monkeypatch.setattr(
        manager,
        "_discover_alsa_usb_device",
        lambda: "plughw:2,0",
    )

    assert manager.initialize() is True
    assert manager.active_device == "plughw:2,0"
    assert "devicename" not in mixer.init_kwargs


def test_apply_retries_after_usb_hotplug():
    devices = ["HDMI Output"]
    manager, _mixer = make_manager(devices=[])
    manager._device_provider = lambda: list(devices)
    assert manager.initialize() is False

    devices.append("USB Audio Device")
    status = manager.configure(
        enabled=True,
        master_volume=0.8,
        music_volume=0.4,
        sfx_volume=0.9,
    )

    assert status["available"] is True
    assert status["device_active"] == "USB Audio Device"


def test_game_music_hit_and_success_flow():
    manager, mixer = make_manager()
    manager.initialize()

    manager.start_game()
    assert manager.music_playing is True
    assert mixer.channel.play_count == 1
    assert mixer.channel.last_loops == -1
    assert manager._sounds["start"].play_count == 1

    assert manager.play_hit() is True
    assert manager.hit_count == 1
    assert manager._sounds["hit"].play_count == 1

    manager.end_game(completed=True)
    assert manager.music_playing is False
    assert manager._sounds["success"].play_count == 1


def test_countdown_and_gameplay_audio_flow():
    manager, _mixer = make_manager()
    manager.initialize()

    assert manager.begin_countdown() is True
    assert manager._sounds["start"].play_count == 1
    for value in (3, 2, 1):
        assert manager.play_countdown(value) is True
        assert manager._sounds[f"countdown_{value}"].play_count == 1

    assert manager.play_countdown(4) is False
    assert manager.begin_gameplay() is True
    assert manager._sounds["go"].play_count == 1
    assert manager.music_playing is True


def test_runtime_volume_and_disable():
    manager, _mixer = make_manager()
    manager.initialize()
    manager.play_music()

    status = manager.configure(
        enabled=False,
        master_volume=0.5,
        music_volume=0.4,
        sfx_volume=0.8,
    )

    assert status["enabled"] is False
    assert status["music_playing"] is False
    assert manager._sounds["hit"].volume == 0.4
    assert manager._fallback_music.volume == 0.2


def test_music_test_button_toggles_playback():
    manager, _mixer = make_manager()
    manager.initialize()

    assert manager.test_sound("music") is True
    assert manager.music_playing is True
    assert manager.test_sound("music") is True
    assert manager.music_playing is False


def test_disabled_audio_does_not_initialize():
    manager, mixer = make_manager(config={"enabled": False})

    assert manager.initialize() is False
    assert mixer.initialized is False
    assert manager.get_status()["enabled"] is False


def test_scene_cue_uses_central_mixer_and_stops_loop():
    manager, _mixer = make_manager()
    manager.initialize()

    assert manager.play_scene_cue(sound_name="hit", volume=0.5, loop=True) is True
    assert manager._sounds["hit"].loops == -1
    assert manager._sounds["hit"].volume == manager.master_volume * manager.sfx_volume
    assert manager._sounds["hit"].channel_volume == (0.5, 0.5)

    manager.stop_scene_audio()
    assert manager._sounds["hit"].fade_count == 1
