"""Authenticated, single-GPU Hugging Face inference service for a disposable Pod.

Only installed Diffusers/Transformers pipeline classes are loaded. Repository Python
and model-card setup commands are never executed (trust_remote_code remains disabled).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get("JUDE_FILM_TMP", "/tmp/jude-film"))
ASSETS = ROOT / "assets"
ARTIFACTS = ROOT / "artifacts"
DB = ROOT / "jobs.sqlite3"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PROMPT_CHARS = 12000
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
ID_RE = re.compile(r"^[a-f0-9]{32}$")
SHA_RE = re.compile(r"^[a-f0-9]{7,64}$")
VIDEO_TASKS = {"text-to-video", "image-to-video"}
VOICE_TASKS = {"text-to-speech"}
SOUND_TASKS = {"text-to-audio"}
SAFE_ERROR_CODES = {
    "CUDA_UNAVAILABLE", "REFERENCE_IMAGE_MISSING", "VIDEO_OUTPUT_MISSING", "VIDEO_OUTPUT_EMPTY",
    "AUDIO_OUTPUT_MISSING", "AUDIO_OUTPUT_INVALID", "AUDIO_SAMPLE_RATE_MISSING", "AUDIO_OUTPUT_EMPTY",
}

DB_LOCK = threading.RLock()
MODEL_LOCK = threading.RLock()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jude-film")
_loaded_key: tuple[str, str, str] | None = None
_loaded_pipeline = None

app = FastAPI(title="Jude Film Worker", docs_url=None, redoc_url=None, openapi_url=None)


def _ensure_dirs() -> None:
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    ASSETS.mkdir(parents=True, exist_ok=True, mode=0o700)
    ARTIFACTS.mkdir(parents=True, exist_ok=True, mode=0o700)


class JobRequest(BaseModel):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kind: Literal["video", "voice", "sound"]
    model_id: str = Field(min_length=3, max_length=200)
    revision: str = Field(pattern=r"^[a-f0-9]{7,64}$")
    pipeline: str = Field(min_length=3, max_length=64)
    library: Literal["diffusers", "transformers"]
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    duration_seconds: int | None = Field(default=None, ge=1, le=15)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    image_asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def _connect():
    _ensure_dirs()
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        status TEXT NOT NULL,
        artifact TEXT,
        digest TEXT,
        error_code TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    connection.commit()
    return connection


def _authorized(request: Request) -> None:
    token = os.environ.get("JUDE_FILM_WORKER_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    expected = "Bearer " + token
    if len(token) < 40 or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "job_id": row["id"],
        "request_id": row["request_id"],
        "status": row["status"],
        "artifact": bool(row["artifact"]),
        "sha256": row["digest"],
        "error_code": row["error_code"],
    }


def _get_job(job_id: str) -> dict | None:
    if not ID_RE.fullmatch(job_id):
        return None
    with DB_LOCK, closing(_connect()) as connection:
        return _row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def _get_by_request(request_id: str) -> dict | None:
    if not ID_RE.fullmatch(request_id):
        return None
    with DB_LOCK, closing(_connect()) as connection:
        return _row(connection.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone())


def _set_status(job_id: str, status: str, artifact: str | None = None,
                digest: str | None = None, error_code: str | None = None) -> None:
    import time

    with DB_LOCK, closing(_connect()) as connection:
        connection.execute(
            "UPDATE jobs SET status=?, artifact=?, digest=?, error_code=?, updated_at=? WHERE id=?",
            (status, artifact, digest, error_code, time.time(), job_id),
        )
        connection.commit()


def _validate_job(body: JobRequest) -> None:
    if not MODEL_RE.fullmatch(body.model_id):
        raise HTTPException(status_code=422, detail="Invalid model repository ID")
    supported = {
        "video": (VIDEO_TASKS, "diffusers"),
        "voice": (VOICE_TASKS, "transformers"),
        "sound": (SOUND_TASKS, None),
    }
    tasks, required_library = supported[body.kind]
    if body.pipeline not in tasks or (required_library and body.library != required_library):
        raise HTTPException(status_code=422, detail="Unsupported task/runtime combination")
    if body.kind == "video" and body.duration_seconds is None:
        raise HTTPException(status_code=422, detail="Video duration is required")
    if body.kind == "video" and body.pipeline == "image-to-video" and not body.image_asset_id:
        raise HTTPException(status_code=422, detail="Image-to-video requires a reference image asset")
    if body.kind == "video" and body.pipeline == "text-to-video" and body.image_asset_id:
        raise HTTPException(status_code=422, detail="Text-to-video does not accept a reference image")
    if body.kind != "video" and body.image_asset_id:
        raise HTTPException(status_code=422, detail="Audio generation does not accept an image")


def _load_pipeline(model_id: str, revision: str, pipeline_tag: str, library: str):
    global _loaded_key, _loaded_pipeline
    key = (model_id, revision, pipeline_tag)
    with MODEL_LOCK:
        if _loaded_key == key and _loaded_pipeline is not None:
            return _loaded_pipeline
        _unload_pipeline()
        if library == "diffusers":
            import torch
            from diffusers import DiffusionPipeline

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA_UNAVAILABLE")
            loaded = DiffusionPipeline.from_pretrained(
                model_id,
                revision=revision,
                torch_dtype=torch.float16,
                use_safetensors=True,
                local_files_only=False,
            )
            if hasattr(loaded, "enable_model_cpu_offload"):
                loaded.enable_model_cpu_offload()
            else:
                loaded.to("cuda")
        else:
            import torch
            from transformers import pipeline as transformers_pipeline

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA_UNAVAILABLE")
            loaded = transformers_pipeline(
                task=pipeline_tag,
                model=model_id,
                revision=revision,
                device=0,
                trust_remote_code=False,
                model_kwargs={"use_safetensors": True},
            )
        _loaded_key, _loaded_pipeline = key, loaded
        return loaded


def _unload_pipeline() -> None:
    global _loaded_key, _loaded_pipeline
    if _loaded_pipeline is not None:
        del _loaded_pipeline
        _loaded_pipeline = None
        _loaded_key = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def _video(body: dict, output: Path) -> None:
    import torch
    from diffusers.utils import export_to_video
    from PIL import Image

    pipeline = _load_pipeline(body["model_id"], body["revision"], body["pipeline"], body["library"])
    try:
        params = inspect.signature(type(pipeline).__call__).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {"prompt": body["prompt"]}
    ratio_dimensions = {"16:9": (854, 480), "9:16": (480, 854), "1:1": (480, 480)}
    width, height = ratio_dimensions[body["aspect_ratio"]]
    if "num_frames" in params:
        kwargs["num_frames"] = min(240, body["duration_seconds"] * 8)
    if "height" in params:
        kwargs["height"] = height
    if "width" in params:
        kwargs["width"] = width
    if body["pipeline"] == "image-to-video":
        source = ASSETS / (body["image_asset_id"] + ".img")
        if not source.is_file():
            raise RuntimeError("REFERENCE_IMAGE_MISSING")
        with Image.open(source) as image:
            kwargs["image"] = image.convert("RGB")
            result = pipeline(**kwargs)
    else:
        result = pipeline(**kwargs)
    frames = getattr(result, "frames", None)
    if frames is None and isinstance(result, dict):
        frames = result.get("frames")
        if frames is None:
            frames = result.get("videos")
    if frames is None or (hasattr(frames, "__len__") and len(frames) == 0):
        raise RuntimeError("VIDEO_OUTPUT_MISSING")
    if isinstance(frames, (list, tuple)) and frames and isinstance(frames[0], (list, tuple)):
        frames = frames[0]
    export_to_video(frames, str(output), fps=8)
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("VIDEO_OUTPUT_EMPTY")
    del result
    torch.cuda.empty_cache()


def _audio(body: dict, output: Path) -> None:
    import numpy as np
    import soundfile as sf

    pipeline = _load_pipeline(body["model_id"], body["revision"], body["pipeline"], body["library"])
    result = pipeline(body["prompt"])
    if isinstance(result, dict):
        audio = result.get("audio")
        if audio is None:
            audio = result.get("audio_values")
        if audio is None:
            audio = result.get("audios")
        sample_rate = result.get("sampling_rate")
    else:
        audio = getattr(result, "audio", None)
        if audio is None:
            audio = getattr(result, "audio_values", None)
        if audio is None:
            audio = getattr(result, "audios", None)
        sample_rate = getattr(result, "sampling_rate", None)
    if audio is None:
        raise RuntimeError("AUDIO_OUTPUT_MISSING")
    if hasattr(audio, "detach"):
        audio = audio.detach().float().cpu().numpy()
    audio = np.asarray(audio)
    if audio.ndim > 1:
        audio = audio.squeeze()
    if audio.ndim != 1 or not audio.size:
        raise RuntimeError("AUDIO_OUTPUT_INVALID")
    if not isinstance(sample_rate, int) or sample_rate < 8000:
        configs = [getattr(getattr(pipeline, "model", None), "config", None),
                   getattr(getattr(pipeline, "vocoder", None), "config", None),
                   getattr(pipeline, "config", None)]
        for model_config in configs:
            sample_rate = getattr(model_config, "sampling_rate", None) or getattr(model_config, "sample_rate", None)
            if isinstance(sample_rate, int) and sample_rate >= 8000:
                break
    if not isinstance(sample_rate, int) or sample_rate < 8000:
        raise RuntimeError("AUDIO_SAMPLE_RATE_MISSING")
    sf.write(str(output), audio, sample_rate, format="WAV", subtype="PCM_16")
    if not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError("AUDIO_OUTPUT_EMPTY")


def _process(job_id: str, body: dict) -> None:
    try:
        _ensure_dirs()
        _set_status(job_id, "running")
        output = ARTIFACTS / (job_id + (".mp4" if body["kind"] == "video" else ".wav"))
        if body["kind"] == "video":
            _video(body, output)
        else:
            _audio(body, output)
        digest = hashlib.sha256()
        with output.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        _set_status(job_id, "completed", output.name, digest.hexdigest())
    except Exception as exc:
        # Keep private library paths, prompts, tokens and raw model errors out of API output.
        code = str(exc) if str(exc) in SAFE_ERROR_CODES else type(exc).__name__.upper()
        for suffix in (".mp4", ".wav"):
            (ARTIFACTS / (job_id + suffix)).unlink(missing_ok=True)
        _set_status(job_id, "failed", error_code=code)


@app.get("/healthz")
def healthz(request: Request):
    _authorized(request)
    try:
        import torch

        cuda = torch.cuda.is_available()
    except ImportError:
        cuda = False
    return {"status": "ready", "cuda": cuda}


@app.post("/v1/assets")
async def upload_asset(request: Request, file: UploadFile = File(...)):
    _authorized(request)
    _ensure_dirs()
    name = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()
    allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    suffix = allowed.get(content_type)
    if not suffix or not name.endswith(suffix if suffix != ".jpg" else (".jpg", ".jpeg")):
        raise HTTPException(status_code=415, detail="Only PNG, JPEG and WebP references are accepted")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Reference image exceeds 20 MiB")
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            if (image.format not in ("PNG", "JPEG", "WEBP")
                    or image.width > 8192 or image.height > 8192 or image.width * image.height > 50_000_000):
                raise ValueError()
            image.verify()
    except (ImportError, OSError, ValueError):
        raise HTTPException(status_code=415, detail="Reference image is invalid") from None
    asset_id = uuid.uuid4().hex
    path = ASSETS / (asset_id + ".img")
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return {"asset_id": asset_id, "bytes": len(data)}


@app.post("/v1/jobs")
def submit_job(body: JobRequest, request: Request):
    _authorized(request)
    _validate_job(body)
    import time

    now = time.time()
    job_id = uuid.uuid4().hex
    with DB_LOCK, closing(_connect()) as connection:
        existing = connection.execute("SELECT * FROM jobs WHERE request_id=?", (body.request_id,)).fetchone()
        if existing:
            return _row(existing)
        connection.execute(
            "INSERT INTO jobs (id, request_id, payload, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, body.request_id, json.dumps(body.model_dump()), "queued", now, now),
        )
        connection.commit()
    EXECUTOR.submit(_process, job_id, body.model_dump())
    return {"job_id": job_id, "request_id": body.request_id, "status": "queued", "artifact": False}


@app.get("/v1/jobs/by-request/{request_id}")
def job_by_request(request_id: str, request: Request):
    _authorized(request)
    result = _get_by_request(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    _authorized(request)
    result = _get_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/v1/jobs/{job_id}/artifact")
def job_artifact(job_id: str, request: Request):
    _authorized(request)
    result = _get_job(job_id)
    if not result or result["status"] != "completed" or not result["artifact"]:
        raise HTTPException(status_code=404, detail="Completed artifact not found")
    path = ARTIFACTS / result["artifact"]
    if path.parent.resolve() != ARTIFACTS.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media_type = "video/mp4" if path.suffix == ".mp4" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=path.name)
