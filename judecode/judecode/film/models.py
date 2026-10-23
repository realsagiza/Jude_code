"""Read-only Hugging Face model discovery and compatibility checks.

Model-card instructions and repository Python are never executed. The runtime only
accepts public repositories with an explicit license and safetensors weights.
"""
from __future__ import annotations

import math
import re
from urllib.parse import quote

import httpx

from judecode import config
from judecode.film.errors import FilmError

HF_API = "https://huggingface.co/api"
VIDEO_PIPELINES = ("text-to-video", "image-to-video")
VOICE_PIPELINES = ("text-to-speech",)
SOUND_PIPELINES = ("text-to-audio",)
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
WEIGHT_SUFFIXES = (".safetensors",)


def _expected_library(pipeline: str) -> tuple[str, ...]:
    if pipeline in VIDEO_PIPELINES:
        return ("diffusers",)
    if pipeline in VOICE_PIPELINES:
        return ("transformers",)
    if pipeline in SOUND_PIPELINES:
        return ("transformers", "diffusers")
    return ()


def _license(row: dict) -> str | None:
    card = row.get("cardData") or {}
    value = card.get("license") if isinstance(card, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    for tag in row.get("tags", []):
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.removeprefix("license:")
    return None


def _languages(row: dict) -> list[str]:
    card = row.get("cardData") or {}
    values = card.get("language", []) if isinstance(card, dict) else []
    if isinstance(values, str):
        values = [values]
    tags = row.get("tags", [])
    found = {x.lower() for x in values if isinstance(x, str)}
    if isinstance(tags, list):
        found.update(x[9:].lower() for x in tags if isinstance(x, str) and x.startswith("language:") and len(x) > 9)
    return sorted(found)


def estimated_vram_gb(pipeline: str, parameter_count: int | None,
                      weight_bytes: int | None = None,
                      parameter_dtypes: dict | None = None) -> int | None:
    """Return a conservative planning estimate, not a measured requirement.

    The Hub's parameter metadata can omit components (for example a separate text
    encoder). Once the repo tree is available, include the complete safetensors size
    as a conservative upper-bound signal as well.
    """
    if type(parameter_count) is not int or parameter_count <= 0:
        return None
    weight_gib = parameter_count * 2 / (1024 ** 3)  # fp16 parameter-count estimate
    if type(weight_bytes) is int and weight_bytes > 0:
        # A repo whose indexed weights are all F32 is loaded as fp16 by this worker.
        # If the Hub metadata is absent/mixed, assume no size reduction.
        dtypes = parameter_dtypes if isinstance(parameter_dtypes, dict) else {}
        positive_dtypes = {name.upper() for name, count in dtypes.items() if type(count) is int and count > 0}
        fp16_factor = 0.5 if positive_dtypes == {"F32"} else 1.0
        weight_gib = max(weight_gib, weight_bytes * fp16_factor / (1024 ** 3))
    if pipeline in VIDEO_PIPELINES:
        return max(16, math.ceil(weight_gib * 1.6 + 8))
    return max(8, math.ceil(weight_gib * 1.25 + 2))


class HuggingFaceCatalog:
    """Small HF Hub API client for model shopping and immutable revisions."""

    def __init__(self, token: str | None = None, transport=None):
        self.token = config.HF_TOKEN if token is None else token
        headers = {"User-Agent": "JudeCode-FilmAgent/1.0"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        self.client = httpx.Client(timeout=30, follow_redirects=False, headers=headers, transport=transport)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _get(self, path: str, params: dict | None = None):
        try:
            response = self.client.get(HF_API + path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise FilmError(f"Hugging Face returned HTTP {exc.response.status_code}; check the model ID, access and rate limit.") from None
        except (httpx.RequestError, ValueError):
            raise FilmError("Could not read Hugging Face model metadata. No model code was executed and no Runpod Pod was created.") from None

    @staticmethod
    def _summary(row: dict, pipeline: str) -> dict:
        model_id = row.get("id")
        card = row.get("cardData") or {}
        card = card if isinstance(card, dict) else {}
        safetensors = row.get("safetensors") or {}
        params = safetensors.get("total") if isinstance(safetensors, dict) else None
        parameter_dtypes = safetensors.get("parameters", {}) if isinstance(safetensors, dict) else {}
        parameter_dtypes = parameter_dtypes if isinstance(parameter_dtypes, dict) else {}
        library = row.get("library_name") or card.get("library_name")
        gated = row.get("gated")
        private = row.get("private")
        license_id = _license(row)
        compatible = library in _expected_library(pipeline)
        ready = (compatible and gated is False and private is False and bool(license_id) and bool(row.get("sha"))
                 and type(params) is int and params > 0)
        return {
            "id": model_id,
            "revision": row.get("sha"),
            "pipeline": pipeline,
            "library": library,
            "license": license_id,
            "gated": gated,
            "private": private,
            "downloads": row.get("downloads", 0),
            "likes": row.get("likes", 0),
            "languages": _languages(row),
            "parameters": params if type(params) is int else None,
            "parameter_dtypes": parameter_dtypes,
            "estimated_vram_gb": estimated_vram_gb(pipeline, params, parameter_dtypes=parameter_dtypes),
            "vram_estimate_basis": "Search-stage parameter-count heuristic only; full repository weight files are checked again before GPU selection.",
            "compatible_runtime": compatible,
            "eligible": ready,
            "url": f"https://huggingface.co/{model_id}" if isinstance(model_id, str) else None,
            "eligibility_note": None if ready else "Needs a supported library, explicitly public access, an explicit license, a pinned revision and an HF parameter count for VRAM sizing.",
        }

    def search(self, kind: str, query: str = "", limit: int = 8) -> dict:
        tasks = {
            "video": VIDEO_PIPELINES,
            "voice": VOICE_PIPELINES,
            "sound": SOUND_PIPELINES,
        }
        if kind not in tasks:
            raise FilmError("kind must be video, voice or sound")
        if not isinstance(query, str) or len(query) > 200:
            raise FilmError("Search query must be text of at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise FilmError("limit must be an integer from 1 to 20")

        fields = ["downloads", "likes", "pipeline_tag", "tags", "library_name", "gated", "private", "cardData", "safetensors", "sha"]
        rows: dict[str, tuple[dict, str]] = {}
        for pipeline in tasks[kind]:
            params = {
                "pipeline_tag": pipeline,
                "sort": "downloads",
                "direction": "-1",
                "limit": limit,
                "expand[]": fields,
            }
            if query.strip():
                params["search"] = query.strip()
            result = self._get("/models", params)
            if not isinstance(result, list):
                raise FilmError("Hugging Face returned an unexpected model list")
            for row in result:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    rows.setdefault(row["id"], (row, pipeline))

        models = [self._summary(row, pipeline) for row, pipeline in rows.values()]
        models.sort(key=lambda item: (item["eligible"], item["downloads"] or 0), reverse=True)
        return {
            "source": "Hugging Face Hub API",
            "kind": kind,
            "query": query,
            "models": models[:limit],
            "note": "Search-stage VRAM is a parameter-count heuristic; production checks the pinned repo's full safetensors file sizes before GPU selection. No estimate proves GPU compatibility. Model-card code is not run; gated, private, unlicensed or unsupported repositories cannot be launched.",
        }

    def inspect(self, model_id: str, kind: str) -> dict:
        if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
            raise FilmError("Invalid Hugging Face model ID; use an exact author/model repository ID")
        tasks = {"video": VIDEO_PIPELINES, "voice": VOICE_PIPELINES, "sound": SOUND_PIPELINES}
        if kind not in tasks:
            raise FilmError("kind must be video, voice or sound")

        fields = ["downloads", "likes", "pipeline_tag", "tags", "library_name", "gated", "private", "cardData", "safetensors", "sha", "siblings"]
        encoded_id = quote(model_id, safe="/")
        row = self._get("/models/" + encoded_id, {"expand[]": fields})
        if not isinstance(row, dict) or row.get("id") != model_id:
            raise FilmError("Hugging Face did not return the requested model repository")
        pipeline = row.get("pipeline_tag")
        if pipeline not in tasks[kind]:
            raise FilmError(f"Model {model_id} does not advertise a supported {kind} pipeline")

        summary = self._summary(row, pipeline)
        if summary["gated"] is not False:
            raise FilmError(f"Model {model_id} is gated or its access status is unknown; accept its license manually before use")
        if summary["private"] is not False:
            raise FilmError(f"Model {model_id} is private or its visibility is unknown; only public HF models are supported")
        if not summary["license"]:
            raise FilmError(f"Model {model_id} has no explicit license in its Hugging Face metadata; refusing automatic use")
        if not summary["compatible_runtime"]:
            raise FilmError(f"Model {model_id} uses an unsupported runtime; no custom model code will be executed")
        revision = summary["revision"]
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{7,64}", revision):
            raise FilmError(f"Model {model_id} has no usable immutable revision")

        tree_path = f"/models/{encoded_id}/tree/{quote(revision, safe='')}"
        # HF's tree endpoint caps this page size; refuse a page at the cap rather
        # than underestimate a very large repository's disk requirement.
        files = self._get(tree_path, {"recursive": "true", "expand": "true", "limit": 100})
        if not isinstance(files, list):
            raise FilmError("Hugging Face returned an unexpected repository file list")
        if len(files) >= 100:
            raise FilmError(f"Model {model_id} has too many repository files for safe automatic sizing")
        weights = [item for item in files if isinstance(item, dict)
                   and item.get("type") == "file"
                   and isinstance(item.get("path"), str)
                   and item["path"].lower().endswith(WEIGHT_SUFFIXES)
                   and type(item.get("size")) is int and item["size"] > 0]
        weight_bytes = sum(item["size"] for item in weights)
        if not weights or weight_bytes <= 0:
            raise FilmError(f"Model {model_id} has no downloadable safetensors weights; refusing pickle/custom-code loading")
        return {
            **summary,
            "weight_bytes": weight_bytes,
            "weight_gib": round(weight_bytes / (1024 ** 3), 2),
            "weight_files": len(weights),
            "estimated_vram_gb": estimated_vram_gb(pipeline, summary["parameters"], weight_bytes,
                                                    summary["parameter_dtypes"]),
            "vram_estimate_basis": "HF parameter count plus total safetensors size adjusted for indexed dtype and fp16 loader; includes pipeline headroom. Conservative planning only, not a GPU measurement",
            "model_card": f"https://huggingface.co/{model_id}/tree/{revision}",
        }
