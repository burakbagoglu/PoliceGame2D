"""
Raspberry Pi 4 üzerinde merkezi müzik ve efekt oynatma.

Ses çıkışı client'larda değil, Pi 4 server'ın 3.5 mm analog jakındadır. Modül;
pygame/SDL ses cihazı bulunamadığında server'ı durdurmadan sessiz moda geçer.
Harici ses dosyaları opsiyoneldir; dosya yoksa telifsiz sentetik efektler ve
basit bir oyun müziği çalışma anında üretilir.
"""
from __future__ import annotations

import math
import os
import re
import sys
import threading
from array import array
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    import pygame
except ImportError:  # Server ses paketi kurulmadan da API ayağa kalkabilsin.
    pygame = None


ToneSequence = Sequence[Tuple[float, float]]


DEFAULT_AUDIO_CONFIG = {
    "enabled": True,
    "device_name": "auto-analog",
    "frequency": 44100,
    "output_channels": 2,
    "buffer": 512,
    "mixer_channels": 8,
    "master_volume": 0.80,
    "music_volume": 0.35,
    "sfx_volume": 0.90,
    "music_file": "",
    "hit_sound_file": "",
    "start_sound_file": "",
    "success_sound_file": "",
    "end_sound_file": "",
}


class AudioManager:
    """Pi 4 analog jakı üzerinden server-side ses oynatıcı."""

    def __init__(
        self,
        config: Optional[dict] = None,
        base_dir: Optional[str] = None,
        pygame_module=None,
        device_provider: Optional[Callable[[], List[str]]] = None,
    ):
        merged = DEFAULT_AUDIO_CONFIG.copy()
        merged.update(config or {})

        env_enabled = os.environ.get("THIEF_AUDIO_ENABLED")
        if env_enabled is not None:
            merged["enabled"] = env_enabled.lower() in ("1", "true", "yes", "on")
        env_device = os.environ.get("THIEF_AUDIO_DEVICE")
        if env_device:
            merged["device_name"] = env_device

        self.base_dir = os.path.abspath(base_dir or os.path.dirname(__file__))
        self.enabled = bool(merged["enabled"])
        self.device_name = str(merged["device_name"] or "auto-analog")
        self.frequency = max(8000, int(merged["frequency"]))
        self.output_channels = 1 if int(merged["output_channels"]) == 1 else 2
        self.buffer = max(128, int(merged["buffer"]))
        self.mixer_channels = max(4, int(merged["mixer_channels"]))
        self.master_volume = self._clamp_volume(merged["master_volume"])
        self.music_volume = self._clamp_volume(merged["music_volume"])
        self.sfx_volume = self._clamp_volume(merged["sfx_volume"])
        self.file_config = {
            "music": str(merged.get("music_file", "") or ""),
            "hit": str(merged.get("hit_sound_file", "") or ""),
            "start": str(merged.get("start_sound_file", "") or ""),
            "success": str(merged.get("success_sound_file", "") or ""),
            "end": str(merged.get("end_sound_file", "") or ""),
            "countdown": str(merged.get("countdown_sound_file", "") or ""),
            "go": str(merged.get("go_sound_file", "") or ""),
        }

        self._pygame = pygame if pygame_module is None else pygame_module
        self._device_provider = device_provider or self._discover_output_devices
        self._lock = threading.RLock()

        self.available = False
        self.active_device: Optional[str] = None
        self.detected_devices: List[str] = []
        self.last_error: Optional[str] = None
        self.music_playing = False
        self.using_fallback_music = False
        self.using_fallback_sfx: Dict[str, bool] = {}
        self.hit_count = 0

        self._sounds: Dict[str, object] = {}
        self._scene_sound_cache: Dict[str, object] = {}
        self._scene_loop_channels: List[object] = []
        self._fallback_music = None
        self._music_channel = None
        self._music_file_path: Optional[str] = None
        self._external_music_loaded = False
        self._alsa_device_override: Optional[str] = None

    @staticmethod
    def _clamp_volume(value) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _discover_output_devices() -> List[str]:
        try:
            from pygame._sdl2 import audio as sdl2_audio

            return list(sdl2_audio.get_audio_device_names(False))
        except Exception:
            return []

    def _select_device(self, devices: List[str]) -> Optional[str]:
        requested = self.device_name.strip()
        requested_lower = requested.lower()

        if requested_lower in ("", "default", "system-default"):
            return None

        if requested_lower.startswith(("hw:", "plughw:")):
            self._alsa_device_override = requested
            os.environ["AUDIODEV"] = requested
            return None

        if requested_lower in (
            "auto",
            "auto-analog",
            "analog",
            "headphone",
            "headphones",
            "jack",
            "3.5mm",
        ):
            for device in devices:
                if self._is_analog_device(device):
                    return device
            alsa_device = self._discover_alsa_analog_device()
            if alsa_device:
                self._alsa_device_override = alsa_device
                os.environ["AUDIODEV"] = alsa_device
                return None
            if devices:
                raise RuntimeError(
                    "Pi 4 analog ses çıkışı bulunamadı. Algılanan cihazlar: "
                    + ", ".join(devices)
                )
            # Bazı SDL/ALSA sürümlerinde listeleme desteklenmez. Bu durumda
            # sistem varsayılanını dene; setup_server.sh analog çıkışı seçer.
            return None

        if requested_lower in ("auto-usb", "usb"):
            for device in devices:
                if "usb" in device.lower():
                    return device
            if devices:
                raise RuntimeError(
                    "USB ses kartı bulunamadı. Algılanan cihazlar: "
                    + ", ".join(devices)
                )
            alsa_device = self._discover_alsa_usb_device()
            if alsa_device:
                self._alsa_device_override = alsa_device
                os.environ["AUDIODEV"] = alsa_device
            return None

        for device in devices:
            if requested_lower == device.lower():
                return device
        for device in devices:
            if requested_lower in device.lower():
                return device

        if devices:
            raise RuntimeError(
                f"İstenen ses cihazı bulunamadı: {requested}. "
                f"Algılananlar: {', '.join(devices)}"
            )
        return requested

    @staticmethod
    def _is_analog_device(device: str) -> bool:
        """HDMI'yi elemeden Pi'nin dahili analog cihaz adını tanı."""
        normalized = device.lower()
        if "hdmi" in normalized or "usb" in normalized:
            return False
        return any(
            hint in normalized
            for hint in ("headphone", "analog", "bcm2835", "onboard", "built-in")
        )

    @classmethod
    def _discover_alsa_analog_device(cls) -> Optional[str]:
        """SDL listeleyemezse /proc/asound/cards içinden analog kartı bul."""
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as f:
                cards = f.read()
        except OSError:
            return None

        for line in cards.splitlines():
            if not cls._is_analog_device(line):
                continue
            match = re.match(r"\s*(\d+)\s+\[([^\]]+)\]\s*:", line)
            if match:
                card_id = match.group(2).strip()
                return f"plughw:{card_id or match.group(1)},0"
        return None

    @staticmethod
    def _discover_alsa_usb_device() -> Optional[str]:
        """SDL cihaz listesi yoksa /proc/asound/cards üzerinden USB kartı bul."""
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as f:
                cards = f.read()
        except OSError:
            return None

        for line in cards.splitlines():
            if "usb-audio" not in line.lower() and "usb" not in line.lower():
                continue
            match = re.match(r"\s*(\d+)\s+\[", line)
            if match:
                return f"plughw:{match.group(1)},0"
        return None

    def initialize(self) -> bool:
        """Mixer'ı ve sesleri yükle. Hata durumunda exception dışarı taşımaz."""
        with self._lock:
            if not self.enabled:
                self.last_error = None
                self.available = False
                return False
            if self._pygame is None:
                self.last_error = "pygame kurulu değil"
                self.available = False
                return False

            try:
                self.detected_devices = self._device_provider()
                self._alsa_device_override = None
                selected_device = self._select_device(self.detected_devices)

                if self._pygame.mixer.get_init():
                    self._pygame.mixer.quit()

                init_kwargs = {
                    "frequency": self.frequency,
                    "size": -16,
                    "channels": self.output_channels,
                    "buffer": self.buffer,
                }
                if selected_device:
                    init_kwargs["devicename"] = selected_device

                self._pygame.mixer.init(**init_kwargs)
                self._pygame.mixer.set_num_channels(self.mixer_channels)
                self._pygame.mixer.set_reserved(1)
                self._music_channel = self._pygame.mixer.Channel(0)
                self.active_device = (
                    selected_device
                    or self._alsa_device_override
                    or "system-default"
                )
                self.available = True
                self.last_error = None

                self._load_audio_assets()
                self._apply_volumes()
                return True
            except Exception as exc:
                self.available = False
                self.active_device = None
                self.last_error = str(exc)
                return False

    def shutdown(self):
        with self._lock:
            self.stop_music(fade_ms=0)
            self.stop_scene_audio()
            if self._pygame is not None:
                try:
                    if self._pygame.mixer.get_init():
                        self._pygame.mixer.quit()
                except Exception:
                    pass
            self.available = False
            self.active_device = None

    def configure(
        self,
        *,
        enabled: bool,
        master_volume: float,
        music_volume: float,
        sfx_volume: float,
    ) -> dict:
        """Dashboard'dan gelen çalışma zamanı ayarlarını uygula."""
        with self._lock:
            self.enabled = bool(enabled)
            self.master_volume = self._clamp_volume(master_volume)
            self.music_volume = self._clamp_volume(music_volume)
            self.sfx_volume = self._clamp_volume(sfx_volume)

            if not self.enabled:
                self.stop_music(fade_ms=100)
            elif not self.available:
                # Ses cihazı server açılışında henüz hazır olmayabilir.
                self.initialize()

            self._apply_volumes()
            return self.get_status()

    def _resolve_file(self, configured_path: str) -> Optional[str]:
        if not configured_path:
            return None
        path = configured_path
        if not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        return path if os.path.isfile(path) else None

    def _load_audio_assets(self):
        self._sounds.clear()
        self.using_fallback_sfx.clear()

        fallback_sequences = {
            "hit": [(880, 0.045), (660, 0.055), (440, 0.070)],
            "start": [(440, 0.09), (660, 0.09), (880, 0.16)],
            "success": [(523.25, 0.10), (659.25, 0.10), (783.99, 0.10), (1046.5, 0.24)],
            "end": [(392, 0.13), (330, 0.13), (262, 0.26)],
            "countdown_3": [(523.25, 0.11), (659.25, 0.08)],
            "countdown_2": [(659.25, 0.11), (783.99, 0.08)],
            "countdown_1": [(783.99, 0.11), (1046.50, 0.12)],
            "go": [(523.25, 0.07), (783.99, 0.07), (1174.66, 0.20)],
        }

        for name, fallback in fallback_sequences.items():
            config_name = "countdown" if name.startswith("countdown_") else name
            path = self._resolve_file(self.file_config[config_name])
            if path:
                self._sounds[name] = self._pygame.mixer.Sound(path)
                self.using_fallback_sfx[name] = False
            else:
                pcm = self._sequence_pcm(fallback, volume=0.42)
                self._sounds[name] = self._pygame.mixer.Sound(buffer=pcm)
                self.using_fallback_sfx[name] = True

        self._music_file_path = self._resolve_file(self.file_config["music"])
        self._external_music_loaded = False
        if self._music_file_path:
            self._pygame.mixer.music.load(self._music_file_path)
            self._external_music_loaded = True
            self._fallback_music = None
            self.using_fallback_music = False
        else:
            notes = [
                (220.00, 0.18), (0, 0.04), (261.63, 0.18), (0, 0.04),
                (329.63, 0.18), (0, 0.04), (261.63, 0.18), (0, 0.04),
                (246.94, 0.18), (0, 0.04), (293.66, 0.18), (0, 0.04),
                (369.99, 0.18), (0, 0.04), (293.66, 0.18), (0, 0.04),
                (261.63, 0.18), (0, 0.04), (329.63, 0.18), (0, 0.04),
                (392.00, 0.18), (0, 0.04), (329.63, 0.18), (0, 0.04),
                (246.94, 0.18), (0, 0.04), (293.66, 0.18), (0, 0.04),
                (329.63, 0.18), (0, 0.04), (220.00, 0.30), (0, 0.10),
            ]
            self._fallback_music = self._pygame.mixer.Sound(
                buffer=self._sequence_pcm(notes, volume=0.18)
            )
            self.using_fallback_music = True

    def _sequence_pcm(
        self,
        sequence: ToneSequence,
        volume: float,
    ) -> bytes:
        samples = array("h")
        attack_samples = max(1, int(self.frequency * 0.008))
        release_samples = max(1, int(self.frequency * 0.025))

        for frequency, duration in sequence:
            frame_count = max(1, int(self.frequency * duration))
            for index in range(frame_count):
                if frequency <= 0:
                    sample = 0
                else:
                    attack = min(1.0, index / attack_samples)
                    release = min(1.0, (frame_count - index) / release_samples)
                    envelope = min(attack, release)
                    fundamental = math.sin(
                        2.0 * math.pi * frequency * index / self.frequency
                    )
                    harmonic = 0.20 * math.sin(
                        4.0 * math.pi * frequency * index / self.frequency
                    )
                    sample = int(
                        32767 * volume * envelope * (fundamental + harmonic) / 1.2
                    )

                for _ in range(self.output_channels):
                    samples.append(sample)

        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _apply_volumes(self):
        if not self.available:
            return
        sfx_level = self.master_volume * self.sfx_volume
        music_level = self.master_volume * self.music_volume

        try:
            for sound in self._sounds.values():
                sound.set_volume(sfx_level)
            if self._fallback_music is not None:
                self._fallback_music.set_volume(music_level)
            self._pygame.mixer.music.set_volume(music_level)
        except Exception as exc:
            self.last_error = str(exc)

    def _play_sfx(self, name: str) -> bool:
        if not self.enabled or not self.available:
            return False
        sound = self._sounds.get(name)
        if sound is None:
            return False
        try:
            sound.play()
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.available = False
            return False

    def play_hit(self) -> bool:
        with self._lock:
            played = self._play_sfx("hit")
            if played:
                self.hit_count += 1
            return played

    def play_music(self) -> bool:
        with self._lock:
            if not self.enabled or not self.available:
                return False
            try:
                if self._external_music_loaded:
                    self._pygame.mixer.music.play(loops=-1)
                elif self._fallback_music is not None and self._music_channel is not None:
                    self._music_channel.play(self._fallback_music, loops=-1)
                else:
                    return False
                self.music_playing = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.available = False
                self.music_playing = False
                return False

    def stop_music(self, fade_ms: int = 250):
        with self._lock:
            if self._pygame is None:
                self.music_playing = False
                return
            try:
                if self._external_music_loaded:
                    if fade_ms > 0:
                        self._pygame.mixer.music.fadeout(fade_ms)
                    else:
                        self._pygame.mixer.music.stop()
                if self._music_channel is not None:
                    if fade_ms > 0:
                        self._music_channel.fadeout(fade_ms)
                    else:
                        self._music_channel.stop()
            except Exception:
                pass
            self.music_playing = False

    def start_game(self):
        with self._lock:
            self._play_sfx("start")
            self.play_music()

    def begin_countdown(self):
        """Intro kartı görünürken müziği kes ve açılış efektini çal."""
        with self._lock:
            self.stop_music(fade_ms=80)
            return self._play_sfx("start")

    def play_countdown(self, value: int) -> bool:
        with self._lock:
            if value not in (1, 2, 3):
                return False
            return self._play_sfx(f"countdown_{value}")

    def begin_gameplay(self):
        """1'den sonra güçlü başlangıç efekti ve müziği başlat."""
        with self._lock:
            self._play_sfx("go")
            return self.play_music()

    def end_game(self, completed: bool):
        with self._lock:
            self.stop_music()
            self._play_sfx("success" if completed else "end")

    def play_scene_cue(
        self,
        *,
        sound_name: str = "",
        asset_path: Optional[str] = None,
        volume: float = 1.0,
        loop: bool = False,
        fade_in_ms: int = 0,
        fade_out_ms: int = 0,
        max_duration_ms: int = 0,
        pan: float = 0.0,
    ) -> bool:
        """Yayınlanmış sahnenin timeline cue kaydını merkezi analog çıkışta çal."""
        with self._lock:
            if not self.enabled or not self.available:
                return False
            try:
                sound = None
                if asset_path and os.path.isfile(asset_path):
                    absolute = os.path.abspath(asset_path)
                    sound = self._scene_sound_cache.get(absolute)
                    if sound is None:
                        sound = self._pygame.mixer.Sound(absolute)
                        self._scene_sound_cache[absolute] = sound
                elif sound_name:
                    sound = self._sounds.get(sound_name)
                if sound is None:
                    return False
                sound.set_volume(self.master_volume * self.sfx_volume)
                fade_in_ms = max(0, min(60_000, int(fade_in_ms)))
                if fade_in_ms:
                    channel = sound.play(loops=-1 if loop else 0, fade_ms=fade_in_ms)
                else:
                    channel = sound.play(loops=-1 if loop else 0)
                if channel is None:
                    return False
                cue_volume = self._clamp_volume(volume)
                pan = max(-1.0, min(1.0, float(pan)))
                left = cue_volume * (1.0 - max(0.0, pan))
                right = cue_volume * (1.0 + min(0.0, pan))
                channel.set_volume(left, right)
                if loop:
                    self._scene_loop_channels.append(channel)
                if max_duration_ms > 0:
                    timer = threading.Timer(
                        max_duration_ms / 1000.0,
                        self._fade_scene_channel,
                        args=(channel, sound, max(0, int(fade_out_ms))),
                    )
                    timer.daemon = True
                    timer.start()
                return True
            except Exception as exc:
                self.last_error = str(exc)
                return False

    @staticmethod
    def _fade_scene_channel(channel, expected_sound, fade_out_ms: int):
        try:
            if channel.get_sound() is expected_sound:
                if fade_out_ms > 0:
                    channel.fadeout(fade_out_ms)
                else:
                    channel.stop()
        except Exception:
            pass
    def stop_scene_audio(self):
        with self._lock:
            for channel in self._scene_loop_channels:
                try:
                    channel.fadeout(100)
                except Exception:
                    pass
            self._scene_loop_channels.clear()
    def test_sound(self, sound_type: str) -> bool:
        with self._lock:
            if sound_type == "music":
                if self.music_playing:
                    self.stop_music()
                    return True
                return self.play_music()
            if sound_type not in ("hit", "start", "success", "end", "go"):
                return False
            return self._play_sfx(sound_type)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "available": self.available,
                "device_requested": self.device_name,
                "device_active": self.active_device,
                "detected_devices": list(self.detected_devices),
                "master_volume": self.master_volume,
                "music_volume": self.music_volume,
                "sfx_volume": self.sfx_volume,
                "music_playing": self.music_playing,
                "using_fallback_music": self.using_fallback_music,
                "using_fallback_sfx": dict(self.using_fallback_sfx),
                "hit_count": self.hit_count,
                "last_error": self.last_error,
            }
