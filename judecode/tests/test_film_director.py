import json
from pathlib import Path

import pytest

from judecode import config
from judecode.film import director
from judecode.film.errors import FilmError
from judecode.film.store import FilmStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = FilmStore(tmp_path / "state", tmp_path / "outputs")
    monkeypatch.setattr(director, "_store", lambda: instance)
    return instance


def test_film_plan_persists_directed_moving_scenes_without_runpod(store):
    result = director.film_plan(
        "Cat home",
        "A short family film",
        "Orange cat, red scarf, evening light",
        "Org/video",
        [{"prompt": "cat finds the road", "duration": 5, "beat": "the clue appears",
          "camera": "close-up then tracking", "end_state": "cat turns toward home",
          "narration": "กลับบ้านกันครับ"}],
        voice_model_id="Org/voice",
    )
    saved = store.load(result["project_id"])
    assert result["status"] == "planned"
    assert saved["models"] == {"video": "Org/video", "voice": "Org/voice", "sound": None}
    assert saved["scenes"][0]["end_state"] == "cat turns toward home"
    assert saved["scenes"][0]["duration"] == 5
    assert "run" not in saved


def test_storyboard_requires_models_for_narration_and_sound(store):
    with pytest.raises(FilmError, match="text-to-speech"):
        director.film_plan("x", "brief", "bible", "Org/video", [{"prompt": "x", "narration": "สวัสดี"}])
    with pytest.raises(FilmError, match="text-to-audio"):
        director.film_plan("x", "brief", "bible", "Org/video", [{"prompt": "x", "sound_prompt": "rain"}])


def test_model_research_can_run_read_only_without_runpod_key(monkeypatch):
    class Hub:
        def __init__(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def search(self, kind, query, limit):
            return {"kind": kind, "query": query, "models": [{"id": "Org/video", "eligible": True}]}

    monkeypatch.setattr(director, "HuggingFaceCatalog", Hub)
    monkeypatch.setattr(config, "RUNPOD_API_KEY", "")
    result = director.film_research("video", "small text-to-video", 5)
    assert result["models"][0]["eligible"] is True
    assert result["runpod"]["configured"] is False
    assert "RUNPOD_API_KEY" in result["runpod"]["note"]


def test_status_hides_private_token_path_and_tool_registration_uses_new_agent(store):
    result = director.film_plan("x", "brief", "bible", "Org/video", [{"prompt": "walk"}])
    project = store.load(result["project_id"])
    project["run"] = {"run_id": "a" * 32, "worker_token_path": "/private/token",
                      "reference_assets": {"1": "asset"}, "cleanup_status": "active"}
    store.save(project)
    status = director.film_status(result["project_id"])
    assert "worker_token_path" not in status["run"]
    assert "run_id" not in status["run"]
    assert "reference_assets" not in status["run"]
    names = [item["function"]["name"] for item in director.FILM_TOOL_DEFINITIONS]
    assert set(names) == {"film_research", "film_plan", "film_produce", "film_status", "film_cleanup"}
    assert json.loads(json.dumps(status))["project_id"] == result["project_id"]


def test_image_to_video_reference_preflight_happens_before_operations(monkeypatch, store):
    class Hub:
        def __init__(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def inspect(self, model_id, kind):
            return {"id": model_id, "revision": "a" * 40, "pipeline": "image-to-video", "library": "diffusers",
                    "weight_bytes": 1024, "estimated_vram_gb": 24, "license": "apache-2.0"}

    class Operations:
        def __init__(self, *args, **kwargs):
            pytest.fail("No Runpod operation should start when the reference image is missing")

    monkeypatch.setattr(director, "HuggingFaceCatalog", Hub)
    monkeypatch.setattr(director, "RunpodOperationsAgent", Operations)
    plan = director.film_plan("x", "brief", "bible", "Org/video", [{"prompt": "walk"}])
    with pytest.raises(FilmError, match="reference image"):
        director.film_produce(plan["project_id"])


def test_produce_delegates_runpod_assets_then_assembles_locally(monkeypatch, store, tmp_path):
    class Hub:
        def __init__(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def inspect(self, model_id, kind):
            return {"id": model_id, "revision": "a" * 40, "pipeline": "text-to-video", "library": "diffusers",
                    "weight_bytes": 1024, "estimated_vram_gb": 16, "license": "apache-2.0"}

    events = []
    local_clip = tmp_path / "scene.mp4"
    local_clip.write_bytes(b"clip")

    class Operations:
        def __init__(self, store=None):
            self.store = store
        def produce(self, project, model_details, **kwargs):
            assert model_details["video"]["revision"] == "a" * 40
            project["scenes"][0]["assets"]["video"] = {"status": "downloaded", "path": str(local_clip)}
            project["status"] = "assets_downloaded"
            self.store.save(project)
            events.append("runpod_download_and_cleanup")
            return {"runpod": {"cleanup_status": "terminated"}}

    def local_assemble(scenes, aspect_ratio, output):
        events.append("local_assemble")
        Path(output).write_bytes(b"mp4")
        return {"files": [str(output)], "duration_seconds": 5, "resolution": "854x480", "quality_warnings": []}

    monkeypatch.setattr(director, "HuggingFaceCatalog", Hub)
    monkeypatch.setattr(director, "RunpodOperationsAgent", Operations)
    monkeypatch.setattr(director, "assemble", local_assemble)
    plan = director.film_plan("x", "brief", "bible", "Org/video", [{"prompt": "walk", "duration": 5}])
    result = director.film_produce(plan["project_id"])
    assert result["status"] == "completed"
    assert result["runpod"]["used"] is True
    assert events == ["runpod_download_and_cleanup", "local_assemble"]
    assert Path(result["final"]["files"][0]).read_bytes() == b"mp4"
