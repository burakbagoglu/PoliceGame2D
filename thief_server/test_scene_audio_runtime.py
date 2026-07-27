"""Merkezi sahne ses timeline runtime testleri."""

from scene_audio_runtime import SceneAudioRuntime


def make_document(loop=False):
    return {
        "scenes": {
            "intro": {
                "duration": 2.0,
                "loop_timeline": loop,
                "audio_cues": [
                    {"id": "start", "time": 0.0, "sound": "start", "loop": loop},
                    {"id": "hit", "time": 1.0, "sound": "hit"},
                ],
            },
            "waiting": {"duration": 5.0, "audio_cues": []},
        }
    }


def test_cues_fire_once_even_with_repeated_client_polls():
    played = []
    stops = []
    runtime = SceneAudioRuntime(lambda cue: played.append(cue["id"]) or True, lambda: stops.append(True))

    runtime.tick("intro", make_document(), now=10.0)
    runtime.tick("intro", make_document(), now=10.4)
    runtime.tick("intro", make_document(), now=11.1)
    runtime.tick("intro", make_document(), now=11.5)

    assert played == ["start", "hit"]
    assert len(stops) == 1


def test_scene_change_stops_loops_and_loop_timeline_replays_cues():
    played = []
    stops = []
    runtime = SceneAudioRuntime(lambda cue: played.append(cue["id"]) or True, lambda: stops.append(True))

    runtime.tick("intro", make_document(loop=True), now=20.0)
    runtime.tick("intro", make_document(loop=True), now=22.1)
    runtime.tick("waiting", make_document(loop=True), now=22.2)

    assert played == ["start"]
    assert len(stops) == 2