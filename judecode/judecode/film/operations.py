"""Runpod operations specialist: create, run, retrieve, and delete one Pod lease."""
from __future__ import annotations

import hashlib
import mimetypes
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

from judecode import config
from judecode.film.errors import FilmError
from judecode.film.runpod import RunpodAPIError, RunpodClient, choose_gpu
from judecode.film.store import FilmStore

POD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,100}$")
OWNER_PREFIX = "JUDE_FILM_OWNER_ID"
WORKER_TOKEN = "JUDE_FILM_WORKER_TOKEN"
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
POLL_INTERVAL_SECONDS = 5


class WorkerRequestError(FilmError):
    """Worker/proxy failure. POST timeouts are uncertain and must not be repeated."""

    def __init__(self, message: str, uncertain: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


class RunpodOperationsAgent:
    """Deterministic operations role used by the creative film director."""

    def __init__(self, store: FilmStore | None = None, client: RunpodClient | None = None,
                 worker_transport=None, sleep=time.sleep, clock=time.time):
        self.store = store or FilmStore()
        self.client = client or RunpodClient()
        self.worker_transport = worker_transport
        self.sleep = sleep
        self.clock = clock

    def gpu_catalog(self) -> list[dict]:
        return self.client.gpu_catalog()

    def quote(self, models: list[dict], max_runtime_seconds: int | None = None,
              max_cost_usd: float | None = None) -> dict:
        runtime = config.RUNPOD_FILM_MAX_RUNTIME_SECONDS if max_runtime_seconds is None else max_runtime_seconds
        cap = config.RUNPOD_FILM_MAX_COST_USD if max_cost_usd is None else max_cost_usd
        if type(runtime) is not int or not 60 <= runtime <= 6 * 3600:
            raise FilmError("Runpod runtime limit must be from 60 seconds to 6 hours")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
            raise FilmError("Runpod cost ceiling must be a positive number")
        hard_max = config.RUNPOD_FILM_HARD_MAX_COST_USD
        if cap > hard_max:
            raise FilmError(f"Requested job ceiling exceeds the configured hard limit of ${hard_max:.2f}")
        disk_limit = config.RUNPOD_FILM_MAX_DISK_GB
        if type(disk_limit) is not int or not 40 <= disk_limit <= 500:
            raise FilmError("RUNPOD_FILM_MAX_DISK_GB must be from 40 to 500")
        gpus = self.client.gpu_catalog()
        return choose_gpu(models, gpus, runtime, float(cap), disk_limit)

    @staticmethod
    def _worker_image() -> str:
        image = config.FILM_WORKER_IMAGE.strip()
        if not image or any(ch.isspace() for ch in image) or any(ch in image for ch in "'$`;&|\n\r"):
            raise FilmError("Set JUDECODE_FILM_WORKER_IMAGE to the published Linux/amd64 worker image before creating a Pod")
        last_component = image.rsplit("/", 1)[-1]
        if ("@sha256:" not in image and ":" not in last_component) or last_component.endswith(":latest"):
            raise FilmError("Use a version-tagged or sha256-pinned worker image, not an untagged or :latest image")
        return image

    @staticmethod
    def _proxy_url(pod_id: str) -> str:
        if not isinstance(pod_id, str) or not POD_ID_RE.fullmatch(pod_id):
            raise FilmError("Runpod returned an invalid Pod ID; refusing to contact a proxy")
        return f"https://{pod_id}-8000.proxy.runpod.net"

    def _save(self, project: dict) -> None:
        self.store.save(project)

    def _worker(self, project: dict, method: str, path: str, *, json_body=None,
                files=None, allow_404: bool = False):
        run = project.get("run") or {}
        base = run.get("proxy_url")
        if not isinstance(base, str) or not base.startswith("https://"):
            raise FilmError("Runpod worker URL is missing; no generation was submitted")
        token = self.store.read_worker_token(project["id"], run.get("run_id", ""))
        url = base + path
        try:
            with httpx.Client(timeout=30, follow_redirects=False, transport=self.worker_transport) as client:
                response = client.request(method, url, json=json_body, files=files,
                                          headers={"Authorization": "Bearer " + token})
            if allow_404 and response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise WorkerRequestError(
                    f"Runpod worker returned HTTP {response.status_code}; no paid request was automatically repeated",
                    uncertain=method.upper() == "POST" and response.status_code >= 500,
                    status_code=response.status_code,
                )
            if not response.content:
                return {}
            result = response.json()
            if not isinstance(result, dict):
                raise WorkerRequestError("Runpod worker returned an unexpected response")
            return result
        except WorkerRequestError:
            raise
        except (httpx.RequestError, ValueError):
            raise WorkerRequestError(
                "Runpod worker/proxy request failed; a submitted job may still be running",
                uncertain=method.upper() == "POST",
            ) from None

    def _worker_health(self, project: dict) -> bool:
        result = self._worker(project, "GET", "/healthz")
        return result.get("status") == "ready" and result.get("cuda") is True

    def _pod_by_owner(self, owner_id: str) -> list[dict]:
        matches = []
        for pod in self.client.list_pods():
            env = pod.get("env") or {}
            if isinstance(env, dict) and env.get(OWNER_PREFIX) == owner_id:
                matches.append(pod)
        return matches

    def _create(self, project: dict, resources: dict) -> dict:
        self._worker_image()
        run = project["run"]
        token = secrets.token_urlsafe(48)
        token_path = self.store.write_worker_token(project["id"], run["run_id"], token)
        pod_name = "jude-film-" + run["run_id"][:20]
        run.update(status="create_pending", pod_name=pod_name, worker_token_path=token_path,
                   cleanup_status="pending", started_at=self.clock())
        project["status"] = "starting"
        self._save(project)
        body = {
            "name": pod_name,
            "image": self._worker_image(),
            "cloud": "SECURE",
            "gpu": {
                "id": resources["gpu_id"],
                "count": 1,
                "minCudaVersion": resources["min_cuda_version"],
                "minRamPerGpu": resources["system_ram_floor_gb_estimate"],
                "minVcpuCountPerGpu": 8,
            },
            "disk": resources["ephemeral_disk_gb"],
            "ports": ["8000/http"],
            "env": {
                OWNER_PREFIX: run["run_id"],
                WORKER_TOKEN: token,
                "HF_HOME": "/tmp/jude-film/hf",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            },
            "startSsh": False,
            "startJupyter": False,
        }
        try:
            pod = self.client.create_pod(body)
        except RunpodAPIError as exc:
            if not exc.uncertain:
                run.update(status="not_created", cleanup_status="not_required")
                self.store.remove_worker_token(project["id"], run["run_id"])
                self._save(project)
                raise
            # Never retry a timed-out create. Reconcile its unique owner marker first.
            matches = self._pod_by_owner(run["run_id"])
            if len(matches) != 1:
                run.update(status="create_unknown", cleanup_status="unknown", create_uncertain=True)
                self._save(project)
                raise FilmError("Runpod Pod creation outcome is uncertain; no second create was sent. Use film_cleanup to reconcile this lease.") from None
            pod = matches[0]
        pod_id = pod.get("id")
        if not isinstance(pod_id, str) or not POD_ID_RE.fullmatch(pod_id):
            run.update(status="create_unknown", cleanup_status="unknown", create_uncertain=True)
            self._save(project)
            raise FilmError("Runpod returned no usable Pod ID; creation was not retried and the lease needs reconciliation")
        run.update(pod_id=pod_id, proxy_url=self._proxy_url(pod_id), status=pod.get("status", "PROVISIONING"),
                   cleanup_status="active", hourly_rate_usd=resources["secure_price_per_hour_usd"])
        project["status"] = "producing"
        self._save(project)
        return pod

    def _remaining(self, project: dict) -> float:
        run = project.get("run") or {}
        start = run.get("started_at", run.get("created_at", self.clock()))
        limit = run.get("max_runtime_seconds", config.RUNPOD_FILM_MAX_RUNTIME_SECONDS)
        remaining = float(limit) - (self.clock() - float(start))
        if remaining <= 0:
            raise FilmError("Runpod runtime cap reached; the agent is terminating its Pod")
        return remaining

    def _wait_for_worker(self, project: dict) -> None:
        deadline = self.clock() + min(900, self._remaining(project))
        while self.clock() < deadline:
            run = project["run"]
            pod = self.client.get_pod(run["pod_id"])
            status = pod.get("status")
            run["status"] = status
            self._save(project)
            if status in ("ERROR", "EXITED", "TERMINATED"):
                raise FilmError(f"Runpod Pod entered {status}; no new Pod or generation was started")
            if status == "RUNNING":
                try:
                    if self._worker_health(project):
                        return
                except WorkerRequestError:
                    pass  # The HTTP proxy can return 502 briefly during service startup.
            self.sleep(min(POLL_INTERVAL_SECONDS, max(0.1, deadline - self.clock())))
        raise FilmError("Runpod worker did not become CUDA-ready before the bounded startup timeout")

    def _upload_reference(self, project: dict, scene: dict) -> str:
        run = project["run"]
        key = str(scene["number"])
        saved = run.setdefault("reference_assets", {}).get(key)
        if saved:
            return saved
        path = Path(scene.get("reference_image", "")).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise FilmError(f"Scene {scene['number']} needs a local PNG/JPEG/WebP reference image for its selected image-to-video model")
        if not 0 < path.stat().st_size <= 20 * 1024 * 1024:
            raise FilmError("Reference image must be no larger than 20 MiB")
        content_type = mimetypes.guess_type(path.name)[0]
        if content_type not in ("image/png", "image/jpeg", "image/webp"):
            raise FilmError("Unsupported reference image type")
        try:
            with path.open("rb") as stream:
                result = self._worker(project, "POST", "/v1/assets", files={"file": (path.name, stream, content_type)})
        except WorkerRequestError as exc:
            if exc.uncertain:
                raise FilmError("Reference upload outcome is uncertain; no clip job was submitted. Inspect the project before continuing.") from None
            raise
        asset_id = result.get("asset_id")
        if not isinstance(asset_id, str) or not re.fullmatch(r"[a-f0-9]{32}", asset_id):
            raise FilmError("Runpod worker returned an invalid reference asset ID")
        run["reference_assets"][key] = asset_id
        self._save(project)
        return asset_id

    def _job_body(self, scene: dict, model: dict, kind: str, prompt: str, request_id: str,
                  project: dict, image_asset_id: str | None = None) -> dict:
        return {
            "request_id": request_id,
            "kind": kind,
            "model_id": model["id"],
            "revision": model["revision"],
            "pipeline": model["pipeline"],
            "library": model["library"],
            "prompt": prompt,
            "duration_seconds": scene["duration"] if kind == "video" else None,
            "aspect_ratio": project["aspect_ratio"],
            "image_asset_id": image_asset_id,
        }

    def _get_or_submit_job(self, project: dict, scene: dict, asset_key: str, model: dict,
                           kind: str, prompt: str, image_asset_id: str | None = None) -> dict:
        asset = scene.setdefault("assets", {}).setdefault(asset_key, {})
        if asset.get("status") == "downloaded" and Path(asset.get("path", "")).is_file():
            return asset
        if asset.get("status") == "failed":
            raise FilmError(f"Scene {scene['number']} {asset_key} generation failed; no automatic paid retry")
        request_id = asset.get("request_id")
        if request_id:
            result = self._worker(project, "GET", "/v1/jobs/by-request/" + quote(request_id, safe=""), allow_404=True)
            if result is None:
                asset["status"] = "unknown"
                self._save(project)
                raise FilmError("The saved generation request has no recoverable receipt; it will not be resubmitted automatically")
        else:
            request_id = uuid.uuid4().hex
            asset.update(request_id=request_id, status="submitting", kind=kind, model_id=model["id"])
            self._save(project)  # Durable idempotency key before any generation request.
            body = self._job_body(scene, model, kind, prompt, request_id, project, image_asset_id)
            try:
                result = self._worker(project, "POST", "/v1/jobs", json_body=body)
            except WorkerRequestError as exc:
                if not exc.uncertain:
                    asset["status"] = "failed"
                    self._save(project)
                    raise
                # POST is never repeated. Ask the worker's idempotency index for the outcome.
                result = self._worker(project, "GET", "/v1/jobs/by-request/" + request_id, allow_404=True)
                if result is None:
                    asset["status"] = "unknown"
                    self._save(project)
                    raise FilmError("Generation submission timed out with unknown outcome; it was not repeated. The Pod will be terminated.") from None
        job_id = result.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            asset["status"] = "unknown"
            self._save(project)
            raise FilmError("Runpod worker returned no usable job receipt; no paid request was repeated")
        asset.update(job_id=job_id, status=result.get("status", "queued"), request_id=request_id)
        self._save(project)
        return asset

    def _poll_job(self, project: dict, asset: dict) -> dict:
        deadline = self.clock() + self._remaining(project)
        while self.clock() < deadline:
            result = self._worker(project, "GET", "/v1/jobs/" + quote(asset["job_id"], safe=""), allow_404=True)
            if result is None:
                asset["status"] = "unknown"
                self._save(project)
                raise FilmError("Runpod worker lost the saved job receipt; no new job will be submitted")
            status = result.get("status")
            asset["status"] = status
            if result.get("error_code"):
                asset["error_code"] = result["error_code"]
            self._save(project)
            if status == "completed" and result.get("artifact"):
                asset["remote_sha256"] = result.get("sha256")
                return result
            if status == "failed":
                asset["status"] = "failed"
                self._save(project)
                raise FilmError(f"Runpod worker reported {asset.get('error_code', 'GENERATION_FAILED')} for scene {asset.get('scene', '')}; no automatic retry")
            if status not in ("queued", "running"):
                asset["status"] = "unknown"
                self._save(project)
                raise FilmError("Runpod worker returned an unknown job status; no new job will be submitted")
            self.sleep(min(POLL_INTERVAL_SECONDS, max(0.1, deadline - self.clock())))
        raise FilmError("Runpod generation exceeded the local runtime cap; the Pod will be terminated")

    def _download(self, project: dict, scene: dict, asset: dict, kind: str) -> dict:
        suffix = ".mp4" if kind == "video" else ".wav"
        expected_types = {"video/mp4", "application/octet-stream"} if kind == "video" else {"audio/wav", "audio/x-wav", "application/octet-stream"}
        target = self.store.asset_dir(project["id"]) / f"scene-{scene['number']:02d}-{kind}{suffix}"
        temporary = target.with_name(target.name + ".partial")
        run = project["run"]
        token = self.store.read_worker_token(project["id"], run["run_id"])
        url = run["proxy_url"] + "/v1/jobs/" + quote(asset["job_id"], safe="") + "/artifact"
        last_error = "Media download failed"
        downloaded = None
        for attempt in range(3):
            temporary.unlink(missing_ok=True)
            digest = hashlib.sha256()
            total = 0
            try:
                with httpx.Client(timeout=90, follow_redirects=False, transport=self.worker_transport) as client:
                    with client.stream("GET", url, headers={"Authorization": "Bearer " + token}) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if content_type not in expected_types:
                            raise FilmError("Runpod worker returned an unexpected artifact type")
                        with temporary.open("xb") as stream:
                            for block in response.iter_bytes(chunk_size=1024 * 1024):
                                total += len(block)
                                if total > MAX_ARTIFACT_BYTES:
                                    raise FilmError("Generated asset exceeds the 1 GiB local download limit")
                                digest.update(block)
                                stream.write(block)
                            stream.flush()
                if total <= 0:
                    raise FilmError("Runpod returned an empty media artifact")
                actual_sha = digest.hexdigest()
                expected_sha = asset.get("remote_sha256")
                if expected_sha and actual_sha != expected_sha:
                    raise FilmError("Downloaded media checksum did not match the Runpod worker receipt")
                with temporary.open("rb") as stream:
                    header = stream.read(12)
                if kind == "video" and (len(header) < 8 or header[4:8] != b"ftyp"):
                    raise FilmError("Downloaded clip is not a valid MP4 container")
                if kind != "video" and (len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE"):
                    raise FilmError("Downloaded voice/sound is not a valid WAV container")
                temporary.replace(target)
                downloaded = (actual_sha, total)
                break
            except FilmError as exc:
                last_error = str(exc)
                temporary.unlink(missing_ok=True)
                if "exceeds the 1 GiB" in last_error:
                    break
            except (httpx.HTTPError, OSError):
                last_error = "Media transfer failed"
                temporary.unlink(missing_ok=True)
            if attempt < 2:
                self.sleep(attempt + 1)
        if downloaded is None:
            asset["status"] = "download_failed"
            asset["download_error"] = last_error
            self._save(project)
            raise FilmError(f"Media download failed after three safe GET attempts ({last_error}); no generation request was repeated") from None
        actual_sha, total = downloaded
        asset.update(status="downloaded", path=str(target), sha256=actual_sha, size_bytes=total)
        self._save(project)
        return asset

    def _generate_one(self, project: dict, scene: dict, key: str, model: dict,
                      kind: str, prompt: str, image_asset_id: str | None = None) -> dict:
        asset = self._get_or_submit_job(project, scene, key, model, kind, prompt, image_asset_id)
        if asset.get("status") == "downloaded" and Path(asset.get("path", "")).is_file():
            return asset
        completed = self._poll_job(project, asset)
        asset["remote_sha256"] = completed.get("sha256")
        asset["scene"] = scene["number"]
        self._save(project)
        return self._download(project, scene, asset, kind)

    def _terminate(self, project: dict) -> bool:
        run = project.get("run") or {}
        if run.get("cleanup_status") == "not_required":
            return True
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            return True
        pod_id = run.get("pod_id")
        candidates = []
        if isinstance(pod_id, str) and POD_ID_RE.fullmatch(pod_id):
            try:
                pod = self.client.get_pod(pod_id)
            except RunpodAPIError as exc:
                if exc.status_code == 404:
                    pod = None
                else:
                    run["cleanup_status"] = "cleanup_pending"
                    self._save(project)
                    return False
            if pod is not None:
                env = pod.get("env") or {}
                if not isinstance(env, dict) or env.get(OWNER_PREFIX) != run_id:
                    run["cleanup_status"] = "owner_mismatch"
                    self._save(project)
                    return False
                candidates = [pod]
        else:
            try:
                for _ in range(3):
                    candidates = self._pod_by_owner(run_id)
                    if candidates:
                        break
                    self.sleep(2)
            except FilmError:
                run["cleanup_status"] = "cleanup_pending"
                self._save(project)
                return False
            if not candidates and (run.get("status") in ("create_pending", "create_unknown") or run.get("create_uncertain")):
                # The create request may still materialize after a control-plane timeout.
                # Keep the lease recoverable instead of claiming teardown was confirmed.
                run["cleanup_status"] = "cleanup_pending"
                run["status"] = "cleanup_pending"
                self._save(project)
                return False

        for pod in candidates:
            candidate_id = pod.get("id")
            if not isinstance(candidate_id, str) or not POD_ID_RE.fullmatch(candidate_id):
                continue
            for attempt in range(3):
                try:
                    self.client.delete_pod(candidate_id)
                except RunpodAPIError as exc:
                    if exc.status_code == 404:
                        break
                deadline = self.clock() + 30
                terminated = False
                while self.clock() < deadline:
                    try:
                        current = self.client.get_pod(candidate_id)
                    except RunpodAPIError as exc:
                        if exc.status_code == 404:
                            terminated = True
                            break
                        self.sleep(2)
                        continue
                    if current.get("status") == "TERMINATED":
                        terminated = True
                        break
                    self.sleep(2)
                if terminated:
                    break
                if attempt == 2:
                    run["cleanup_status"] = "cleanup_pending"
                    run["status"] = "cleanup_pending"
                    self._save(project)
                    return False
        run.update(cleanup_status="terminated", status="TERMINATED", pod_id=None, proxy_url=None)
        self.store.remove_worker_token(project["id"], run_id)
        self._save(project)
        return True

    def cleanup(self, project_id: str) -> dict:
        """Delete only Pod(s) marked with this film project's unique owner ID."""
        project = self.store.load(project_id)
        run = project.get("run") or {}
        if not run.get("run_id"):
            return {"project_id": project_id, "cleanup_status": "no_pod"}
        cleaned = self._terminate(project)
        return {
            "project_id": project_id,
            "cleanup_status": (project.get("run") or {}).get("cleanup_status"),
            "pod_id": (project.get("run") or {}).get("pod_id"),
            "terminated": cleaned,
        }

    def produce(self, project: dict, model_details: dict[str, dict], *, max_runtime_seconds: int | None = None,
                max_cost_usd: float | None = None, retry_failed: bool = False) -> dict:
        """Generate all missing clips/voice/sound, download, then terminate the Pod."""
        if not model_details.get("video"):
            raise FilmError("A verified Hugging Face video model is required")
        models = list(model_details.values())
        run = project.get("run") or {}
        if run.get("cleanup_status") in ("cleanup_pending", "unknown", "owner_mismatch"):
            raise FilmError("A previous Pod lease is unresolved; call film_cleanup before another production step")
        resume = bool(run.get("pod_id") and run.get("cleanup_status") != "terminated")
        failed_assets = []
        unresolved_assets = []
        for scene in project["scenes"]:
            for key, asset in (scene.get("assets") or {}).items():
                status = asset.get("status")
                if status in ("failed", "download_failed"):
                    failed_assets.append((scene.get("number"), key))
                elif status in ("unknown", "submitting", "queued", "running", "completed") and not resume:
                    unresolved_assets.append((scene.get("number"), key, status))
        if unresolved_assets:
            raise FilmError("A saved paid job has no live Pod to reconcile; no new Pod was created. Inspect film_status and Runpod before deciding what to do.")
        if failed_assets and not retry_failed:
            raise FilmError("This film has a failed generation/download; set retry_failed=true only after the user authorizes another paid generation")
        if resume:
            self._worker_image()  # Still verify the required runtime configuration before attaching.
            resources = run.get("resources") or {}
            if not resources:
                raise FilmError("Saved Runpod lease has no resource plan; inspect state before resuming")
            saved_models = run.get("models") or {}
            for key, selected in model_details.items():
                previous = saved_models.get(key)
                if not isinstance(previous, dict) or previous.get("id") != selected.get("id") or previous.get("revision") != selected.get("revision"):
                    raise FilmError("Selected Hugging Face model revision differs from the active Pod lease; refusing to mix model revisions")
        else:
            resources = self.quote(models, max_runtime_seconds, max_cost_usd)
            self._worker_image()

        run = project.get("run") or {}
        if not resume:
            if run.get("cleanup_status") in ("cleanup_pending", "unknown", "owner_mismatch"):
                raise FilmError("A previous Pod lease is unresolved; call film_cleanup before starting another run")
            project["run"] = {"run_id": uuid.uuid4().hex, "status": "planned", "resources": resources,
                               "created_at": self.clock(), "max_runtime_seconds": resources["max_runtime_seconds"],
                               "reference_assets": {}, "models": model_details}
            self._save(project)

        failure: BaseException | None = None
        cleaned = False
        try:
            if not resume:
                self._create(project, resources)
            else:
                self.store.read_worker_token(project["id"], run["run_id"])
            self._wait_for_worker(project)
            project["status"] = "producing"
            self._save(project)
            video_model = model_details["video"]
            voice_model = model_details.get("voice")
            sound_model = model_details.get("sound")
            for scene in project["scenes"]:
                if scene.get("mode") == "still":
                    raise FilmError("The new Runpod director creates moving video only; still/slideshow scenes are not supported")
                reference_id = None
                if video_model["pipeline"] == "image-to-video":
                    reference_id = self._upload_reference(project, scene)
                prompt = "\n".join(part for part in (
                    project["continuity"],
                    scene["prompt"],
                    ("Beat: " + scene["beat"]) if scene.get("beat") else "",
                    ("Camera: " + scene["camera"]) if scene.get("camera") else "",
                    ("End state: " + scene["end_state"]) if scene.get("end_state") else "",
                    "No captions or written text. Preserve character identity, clothing, props, setting and visual style.",
                ) if part)
                if retry_failed:
                    for asset_key in ("video", "voice", "sound"):
                        old_asset = scene.get("assets", {}).get(asset_key, {})
                        if old_asset.get("status") in ("failed", "download_failed"):
                            scene["assets"].pop(asset_key, None)
                    self._save(project)
                self._generate_one(project, scene, "video", video_model, "video", prompt, reference_id)
                if scene.get("narration"):
                    if not voice_model:
                        raise FilmError("The storyboard includes voiceover but no verified Hugging Face text-to-speech model was selected")
                    self._generate_one(project, scene, "voice", voice_model, "voice", scene["narration"])
                if scene.get("sound_prompt"):
                    if not sound_model:
                        raise FilmError("The storyboard includes sound effects/music but no verified Hugging Face text-to-audio model was selected")
                    self._generate_one(project, scene, "sound", sound_model, "sound", scene["sound_prompt"])
            run = project["run"]
            run["outputs_downloaded"] = True
            project["status"] = "assets_downloaded"
            self._save(project)
        except BaseException as exc:
            failure = exc
            project["status"] = "failed" if isinstance(exc, Exception) else "interrupted"
            self._save(project)
        finally:
            # Stop billing on every normal error/success path. A hard host crash is
            # recoverable via the durable owner marker and film_cleanup.
            try:
                cleaned = self._terminate(project)
            except Exception:
                cleaned = False
            project["run"]["cleanup_status"] = "terminated" if cleaned else "cleanup_pending"
            self._save(project)

        if failure is not None:
            if not cleaned and isinstance(failure, Exception):
                raise FilmError(f"{failure}; in addition, Runpod termination is not confirmed. Call film_cleanup immediately.") from None
            raise failure
        if not cleaned:
            raise FilmError("Media was downloaded, but Runpod did not confirm Pod termination; call film_cleanup immediately and check the Runpod billing page")
        return {
            "project_id": project["id"],
            "status": project["status"],
            "runpod": {
                "gpu": resources.get("gpu_name"),
                "gpu_rate_per_hour_usd": resources.get("secure_price_per_hour_usd"),
                "estimated_gpu_cost_usd": resources.get("estimated_gpu_cost_usd"),
                "runtime_cap_seconds": resources.get("max_runtime_seconds"),
                "cleanup_status": project["run"]["cleanup_status"],
                "storage": "ephemeral only; no persistent volume",
            },
        }
