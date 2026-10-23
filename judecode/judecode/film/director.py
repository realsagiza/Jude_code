"""Creative film director tools backed only by Hugging Face and Runpod."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from judecode import config
from judecode.film.assembler import assemble
from judecode.film.errors import FilmError
from judecode.film.models import HuggingFaceCatalog, MODEL_ID
from judecode.film.operations import RunpodOperationsAgent
from judecode.film.store import FilmStore

_RATIO = {"16:9", "9:16", "1:1"}
_TEXT_KEYS = ("beat", "camera", "end_state", "reference_image", "narration", "sound_prompt")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store() -> FilmStore:
    return FilmStore()


def film_research(kind: str, query: str = "", limit: int = 8) -> dict:
    """Search public HF models and, when configured, live Secure GPU catalog data."""
    with HuggingFaceCatalog() as hub:
        results = hub.search(kind, query, limit)
    runpod = {"configured": bool(config.RUNPOD_API_KEY), "gpus": []}
    if config.RUNPOD_API_KEY:
        try:
            gpus = RunpodOperationsAgent().gpu_catalog()
            usable = [gpu for gpu in gpus if gpu.get("manufacturer") == "NVIDIA"
                      and gpu.get("secure") is True and gpu.get("availability") in ("LOW", "MEDIUM", "HIGH")
                      and isinstance((gpu.get("price") or {}).get("secure"), (int, float))]
            usable.sort(key=lambda gpu: (gpu["price"]["secure"], -gpu.get("memory", 0)))
            runpod["gpus"] = [{key: gpu.get(key) for key in ("id", "name", "memory", "availability")}
                               | {"secure_price_per_hour_usd": (gpu.get("price") or {}).get("secure")}
                               for gpu in usable[:12]]
            runpod["cuda_floor"] = config.RUNPOD_FILM_MIN_CUDA_VERSION
            runpod["cloud"] = "SECURE"
        except FilmError as exc:
            runpod["error"] = str(exc)
    else:
        runpod["note"] = "Set RUNPOD_API_KEY to read current Pod GPU stock/prices. Hugging Face model discovery is still available."
    return {**results, "runpod": runpod}


def film_plan(title: str, brief: str, continuity: str, video_model_id: str,
              scenes: list[dict], aspect_ratio: str = "16:9",
              voice_model_id: str = "", sound_model_id: str = "") -> dict:
    """Persist a moving-video storyboard; planning itself does not create a Pod."""
    values = (title, brief, continuity, video_model_id)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise FilmError("Provide title, creative brief, continuity bible and a selected Hugging Face video model ID")
    if len(title) > 200 or len(brief) > 6000 or len(continuity) > 4000:
        raise FilmError("Title, brief or continuity bible exceeds its safe length")
    if not MODEL_ID.fullmatch(video_model_id):
        raise FilmError("Use an exact Hugging Face author/model ID for the video model")
    if voice_model_id and not MODEL_ID.fullmatch(voice_model_id):
        raise FilmError("Use an exact Hugging Face author/model ID for the voice model")
    if sound_model_id and not MODEL_ID.fullmatch(sound_model_id):
        raise FilmError("Use an exact Hugging Face author/model ID for the sound model")
    if aspect_ratio not in _RATIO or not isinstance(scenes, list) or not 1 <= len(scenes) <= 30:
        raise FilmError("Use 1–30 scenes and aspect ratio 16:9, 9:16 or 1:1")

    clean = []
    for number, raw in enumerate(scenes, start=1):
        if not isinstance(raw, dict):
            raise FilmError(f"Scene {number} must be an object")
        prompt = raw.get("prompt")
        duration = raw.get("duration", 5)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise FilmError(f"Scene {number} needs a visual/action prompt of at most 4000 characters")
        if type(duration) is not int or not 1 <= duration <= 15:
            raise FilmError(f"Scene {number} duration must be an integer from 1 to 15 seconds")
        scene = {"number": number, "prompt": prompt.strip(), "duration": duration, "assets": {}}
        for key in _TEXT_KEYS:
            value = raw.get(key, "")
            if not isinstance(value, str) or len(value) > (4000 if key == "narration" else 1000):
                raise FilmError(f"Scene {number} field {key} must be text within the supported length")
            if value.strip():
                scene[key] = value.strip()
        if scene.get("narration") and not voice_model_id:
            raise FilmError("Choose a Hugging Face text-to-speech model for scenes with narration")
        if scene.get("sound_prompt") and not sound_model_id:
            raise FilmError("Choose a Hugging Face text-to-audio model for scenes with sound_prompt")
        clean.append(scene)

    project_id = uuid.uuid4().hex
    project = {
        "id": project_id,
        "title": title.strip(),
        "brief": brief.strip(),
        "continuity": continuity.strip(),
        "aspect_ratio": aspect_ratio,
        "models": {"video": video_model_id, "voice": voice_model_id or None, "sound": sound_model_id or None},
        "scenes": clean,
        "status": "planned",
        "quality": {"status": "unreviewed", "warnings": []},
        "created_at": _now_iso(),
    }
    path = _store().create(project)
    return {"project_id": project_id, "project_path": path, "status": "planned",
            "scenes": len(clean), "next": "Call film_produce after model/license review; this tool did not create a Pod."}


def _required_models(project: dict) -> dict[str, str]:
    required = {"video": project["models"]["video"]}
    if any(scene.get("narration") for scene in project["scenes"]):
        model = project["models"].get("voice")
        if not model:
            raise FilmError("Storyboard needs narration but no speech model is saved")
        required["voice"] = model
    if any(scene.get("sound_prompt") for scene in project["scenes"]):
        model = project["models"].get("sound")
        if not model:
            raise FilmError("Storyboard needs generated sound but no sound model is saved")
        required["sound"] = model
    return required


def _assets_ready(project: dict) -> bool:
    for scene in project["scenes"]:
        required = ["video"]
        if scene.get("narration"):
            required.append("voice")
        if scene.get("sound_prompt"):
            required.append("sound")
        for key in required:
            asset = scene.get("assets", {}).get(key, {})
            if asset.get("status") != "downloaded" or not Path(asset.get("path", "")).is_file():
                return False
    return True


def _assemble_project(project: dict, store: FilmStore) -> dict:
    scenes = []
    for scene in project["scenes"]:
        assets = scene.get("assets", {})
        item = {"video": assets["video"]["path"], "duration": scene["duration"]}
        if scene.get("narration"):
            item["narration"] = assets["voice"]["path"]
        if scene.get("sound_prompt"):
            item["sound"] = assets["sound"]["path"]
        scenes.append(item)
    result = assemble(scenes, project["aspect_ratio"], store.final_path(project["id"]))
    project["final"] = result
    project["status"] = "completed"
    project["quality"] = {
        "status": "unreviewed",
        "warnings": result["quality_warnings"],
        "note": "Render/assembly completed locally. Watch and listen to the MP4 before claiming creative quality or continuity.",
    }
    project["completed_at"] = _now_iso()
    store.save(project)
    return result


def film_produce(project_id: str, max_runtime_seconds: int | None = None,
                 max_cost_usd: float | None = None, retry_failed: bool = False) -> dict:
    """Run generation on one disposable Pod, download first, terminate, then assemble locally."""
    if type(retry_failed) is not bool:
        raise FilmError("retry_failed must be a boolean")
    store = _store()
    project = store.load(project_id)
    if project.get("status") == "completed" and project.get("final"):
        final_path = project["final"].get("files", [""])[0]
        if final_path and Path(final_path).is_file():
            return film_status(project_id)
    if _assets_ready(project):
        run = project.get("run") or {}
        if run.get("run_id") and run.get("cleanup_status") not in (None, "terminated", "not_required"):
            cleanup = RunpodOperationsAgent(store=store).cleanup(project_id)
            if not cleanup.get("terminated"):
                raise FilmError("Assets are local, but the Runpod Pod is not confirmed terminated; call film_cleanup before assembly")
            project = store.load(project_id)
        try:
            result = _assemble_project(project, store)
        except FilmError:
            project["status"] = "assembly_failed"
            store.save(project)
            raise
        return {"project_id": project_id, "status": project["status"], "final": result,
                "runpod": {"used": False, "reason": "All required assets were already downloaded locally"}}

    saved_models = project.get("production_models")
    active_run = project.get("run") or {}
    if active_run.get("pod_id") and active_run.get("cleanup_status") != "terminated":
        model_details = active_run.get("models") or saved_models
        if not isinstance(model_details, dict):
            raise FilmError("Saved active Pod has no model revision record; inspect before resuming")
    elif saved_models:
        model_details = saved_models
    else:
        required = _required_models(project)
        model_details = {}
        with HuggingFaceCatalog() as hub:
            for key, model_id in required.items():
                model_details[key] = hub.inspect(model_id, key)
        project["production_models"] = model_details
        store.save(project)

    if model_details.get("video", {}).get("pipeline") == "image-to-video":
        for scene in project["scenes"]:
            reference = Path(scene.get("reference_image", "")).expanduser().resolve()
            if not reference.is_file() or reference.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                raise FilmError(f"Scene {scene['number']} needs a local PNG/JPEG/WebP reference image before a Pod is created")
            if not 0 < reference.stat().st_size <= 20 * 1024 * 1024:
                raise FilmError(f"Scene {scene['number']} reference image must be no larger than 20 MiB")
            try:
                from PIL import Image

                with Image.open(reference) as image:
                    if (image.format not in ("PNG", "JPEG", "WEBP")
                            or image.width > 8192 or image.height > 8192 or image.width * image.height > 50_000_000):
                        raise ValueError()
                    image.verify()
            except (ImportError, OSError, ValueError):
                raise FilmError(f"Scene {scene['number']} reference file is not a valid PNG/JPEG/WebP image") from None

    operations = RunpodOperationsAgent(store=store)
    try:
        result = operations.produce(project, model_details, max_runtime_seconds=max_runtime_seconds,
                                    max_cost_usd=max_cost_usd, retry_failed=retry_failed)
    except Exception:
        # Operations has already attempted Pod termination and checkpointed every asset.
        raise
    if not _assets_ready(project):
        raise FilmError("Runpod job returned without every required local asset; the Pod was terminated and no final MP4 was assembled")
    try:
        final = _assemble_project(project, store)
    except FilmError:
        project["status"] = "assembly_failed"
        store.save(project)
        raise
    return {"project_id": project_id, "status": project["status"], "final": final,
            "runpod": {"used": True, **result.get("runpod", {})},
            "quality": project["quality"]}


def film_status(project_id: str) -> dict:
    """Read project state without calling HF, Runpod or a model API."""
    store = _store()
    project = store.load(project_id)
    public = copy.deepcopy(project)
    run = public.get("run")
    if isinstance(run, dict):
        run.pop("worker_token_path", None)
        run.pop("run_id", None)
        run.pop("reference_assets", None)
        run.pop("proxy_url", None)
    return {
        **public,
        "project_id": project_id,
        "project_path": str(store.path(project_id)),
        "next": "film_produce" if not _assets_ready(project) else ("watch/listen to final" if project.get("status") == "completed" else "assemble with film_produce"),
    }


def film_cleanup(project_id: str) -> dict:
    """Terminate only the Pod carrying this project's unique Runpod owner marker."""
    return RunpodOperationsAgent(store=_store()).cleanup(project_id)


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}}


_STRING = {"type": "string"}
FILM_TOOL_DEFINITIONS = [
    _tool("film_research", "Search public Hugging Face video/voice/sound models and live Runpod Secure GPU stock/prices. Read-only; model-card code is never run.",
          {"kind": {"type": "string", "enum": ["video", "voice", "sound"]}, "query": _STRING,
           "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["kind"]),
    _tool("film_plan", "Create a durable moving-video storyboard. Choose model IDs from film_research; this tool does not spend or create a Pod. Voice/sound models are needed only for scenes with narration/sound_prompt.",
          {"title": _STRING, "brief": _STRING, "continuity": _STRING, "video_model_id": _STRING,
           "voice_model_id": _STRING, "sound_model_id": _STRING,
           "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
           "scenes": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"type": "object",
             "properties": {"prompt": _STRING, "duration": {"type": "integer", "minimum": 1, "maximum": 15},
               "beat": _STRING, "camera": _STRING, "end_state": _STRING, "narration": _STRING,
               "sound_prompt": _STRING, "reference_image": _STRING},
             "required": ["prompt"], "additionalProperties": False}}},
          ["title", "brief", "continuity", "video_model_id", "scenes"]),
    _tool("film_produce", "Billable: create one Secure Runpod Pod, generate missing clips/voice/sound, download and verify them locally, terminate the Pod, then assemble the MP4 locally. Uses a bounded configured cost/runtime ceiling; never resubmits uncertain jobs. Set retry_failed=true only after the user authorizes another paid generation.",
          {"project_id": _STRING, "max_runtime_seconds": {"type": "integer", "minimum": 60, "maximum": 21600},
           "max_cost_usd": {"type": "number", "exclusiveMinimum": 0}, "retry_failed": {"type": "boolean"}},
          ["project_id"]),
    _tool("film_status", "Read saved storyboard, local asset paths, job receipts and cleanup status without contacting Runpod or spending.",
          {"project_id": _STRING}, ["project_id"]),
    _tool("film_cleanup", "Terminate only the Runpod Pod owned by this film project. Use after interruption or cleanup_pending; never deletes unrelated Pods.",
          {"project_id": _STRING}, ["project_id"]),
]
FILM_FUNCTIONS = {item["function"]["name"]: globals()[item["function"]["name"]] for item in FILM_TOOL_DEFINITIONS}


def execute_film_tool(name: str, params: dict) -> str:
    try:
        return json.dumps(FILM_FUNCTIONS[name](**params), ensure_ascii=False)
    except (FilmError, OSError, ValueError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, FilmError) else "Invalid film arguments or local file operation failed"
        return f"Error executing tool '{name}': {message}"
