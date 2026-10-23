"""Runpod REST v2 client and live GPU/resource selection."""
from __future__ import annotations

import math
from urllib.parse import quote

import httpx

from judecode import config
from judecode.film.errors import FilmError

RUNPOD_API = "https://api.runpod.io/v2"
GIB = 1024 ** 3


class RunpodAPIError(FilmError):
    """A sanitized Runpod API failure; POST outcomes may be uncertain."""

    def __init__(self, message: str, status_code: int | None = None, uncertain: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.uncertain = uncertain


class RunpodClient:
    """Narrow REST v2 client for only catalog, pod create/read/delete operations."""

    def __init__(self, api_key: str | None = None, base_url: str = RUNPOD_API, transport=None):
        self.api_key = config.RUNPOD_API_KEY if api_key is None else api_key
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        if not self.api_key:
            raise FilmError("Set RUNPOD_API_KEY in Jude Code's local environment before using Runpod")
        uncertain = method.upper() in ("POST", "PATCH", "DELETE")
        try:
            with httpx.Client(timeout=30, follow_redirects=False, transport=self.transport) as client:
                response = client.request(
                    method,
                    self.base_url + path,
                    params=params,
                    json=body,
                    headers={"Authorization": "Bearer " + self.api_key},
                )
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            result = response.json()
            if not isinstance(result, dict):
                raise RunpodAPIError("Runpod returned an unexpected response shape", uncertain=uncertain)
            return result
        except RunpodAPIError:
            raise
        except httpx.HTTPStatusError as exc:
            raise RunpodAPIError(
                f"Runpod API returned HTTP {exc.response.status_code}; check account access, capacity and billing",
                status_code=exc.response.status_code,
                uncertain=uncertain and exc.response.status_code >= 500,
            ) from None
        except (httpx.RequestError, ValueError):
            raise RunpodAPIError(
                "Runpod request timed out or returned invalid JSON; resource outcome may be unknown",
                uncertain=uncertain,
            ) from None

    def gpu_catalog(self, min_cuda_version: str | None = None) -> list[dict]:
        floor = min_cuda_version or config.RUNPOD_FILM_MIN_CUDA_VERSION
        result = self.request("GET", "/catalog/gpus", params={
            "include": "AVAILABILITY",
            "product": "POD",
            "cloud": "SECURE",
            "minCudaVersion": floor,
        })
        gpus = result.get("gpus")
        if not isinstance(gpus, list):
            raise FilmError("Runpod GPU catalog returned an unexpected response")
        return [gpu for gpu in gpus if isinstance(gpu, dict)]

    def create_pod(self, body: dict) -> dict:
        return self.request("POST", "/pods", body=body)

    def get_pod(self, pod_id: str) -> dict:
        return self.request("GET", "/pods/" + quote(pod_id, safe=""))

    def delete_pod(self, pod_id: str) -> dict:
        return self.request("DELETE", "/pods/" + quote(pod_id, safe=""))

    def list_pods(self) -> list[dict]:
        rows = []
        cursor = None
        for _ in range(20):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = self.request("GET", "/pods", params=params)
            batch = result.get("pods")
            if not isinstance(batch, list):
                raise FilmError("Runpod Pod list returned an unexpected response")
            rows.extend(pod for pod in batch if isinstance(pod, dict))
            pagination = result.get("pagination") or {}
            if not isinstance(pagination, dict) or pagination.get("hasNextPage") is not True:
                break
            next_cursor = pagination.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise FilmError("Runpod Pod pagination could not be continued safely")
            cursor = next_cursor
        else:
            raise FilmError("Runpod Pod list exceeded the safe pagination limit")
        return rows


def choose_gpu(models: list[dict], gpus: list[dict], max_runtime_seconds: int,
               max_cost_usd: float, max_disk_gb: int) -> dict:
    """Choose the least expensive in-stock Secure GPU meeting metadata estimates."""
    if not models:
        raise FilmError("At least one Hugging Face model is required before choosing a GPU")
    vram_values = [model.get("estimated_vram_gb") for model in models]
    if any(type(value) is not int or value <= 0 for value in vram_values):
        raise FilmError("The selected model's VRAM estimate is unknown; no Pod was created")
    needed_vram = max(vram_values)
    weight_bytes = [model.get("weight_bytes") for model in models]
    if any(type(value) is not int or value <= 0 for value in weight_bytes):
        raise FilmError("Could not verify selected model weight sizes; no Pod was created")
    disk_gb = max(40, math.ceil(sum(weight_bytes) / GIB * 1.35 + 20))
    if disk_gb > max_disk_gb:
        raise FilmError(f"Selected model weights need about {disk_gb} GiB of ephemeral disk, above the configured {max_disk_gb} GiB limit")
    system_ram_gb = max(32, math.ceil(sum(weight_bytes) / GIB * 1.25))
    if system_ram_gb > 512:
        raise FilmError("Selected model set exceeds the worker's supported host-RAM planning limit")

    candidates = []
    for gpu in gpus:
        memory = gpu.get("memory")
        secure_price = (gpu.get("price") or {}).get("secure")
        availability = gpu.get("availability")
        if (gpu.get("manufacturer") != "NVIDIA" or gpu.get("secure") is not True
                or type(memory) is not int or memory < needed_vram
                or not isinstance(secure_price, (int, float)) or secure_price <= 0
                or availability not in ("LOW", "MEDIUM", "HIGH")):
            continue
        gpu_id = gpu.get("id")
        if not isinstance(gpu_id, str) or not gpu_id:
            continue
        estimate = float(secure_price) * max_runtime_seconds / 3600
        candidates.append((float(secure_price), gpu, estimate))
    if not candidates:
        raise FilmError(f"No currently available Secure GPU has at least {needed_vram} GiB VRAM under the CUDA floor")
    candidates.sort(key=lambda row: row[0])
    rate, gpu, estimate = candidates[0]
    if estimate > max_cost_usd:
        raise FilmError(
            f"Estimated GPU compute cost at the configured {max_runtime_seconds // 60}-minute runtime cap is ${estimate:.2f}, "
            f"above the ${max_cost_usd:.2f} job ceiling. No Pod was created."
        )
    return {
        "gpu_id": gpu["id"],
        "gpu_name": gpu.get("name", gpu["id"]),
        "gpu_memory_gb": gpu["memory"],
        "gpu_availability": gpu.get("availability"),
        "secure_price_per_hour_usd": rate,
        "estimated_gpu_cost_usd": round(estimate, 4),
        "estimate_basis": "Secure GPU catalog rate multiplied by the local wall-clock runtime cap; storage/invoice totals may differ.",
        "required_vram_gb_estimate": needed_vram,
        "system_ram_floor_gb_estimate": system_ram_gb,
        "ephemeral_disk_gb": disk_gb,
        "min_cuda_version": config.RUNPOD_FILM_MIN_CUDA_VERSION,
        "max_runtime_seconds": max_runtime_seconds,
    }
