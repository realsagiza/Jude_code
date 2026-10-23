import hashlib
import json

import httpx
import pytest

from judecode import config
from judecode.film.errors import FilmError
from judecode.film.operations import OWNER_PREFIX, RunpodOperationsAgent
from judecode.film.store import FilmStore

MP4 = b"\x00\x00\x00\x18ftypisomjude-test-clip"


class FakeRunpod:
    def __init__(self, events=None, price=0.25, owner_override=None):
        self.events = events if events is not None else []
        self.price = price
        self.owner_override = owner_override
        self.pod = None
        self.create_calls = 0
        self.delete_calls = 0

    def gpu_catalog(self):
        return [{"id": "NVIDIA Test 80GB", "name": "Test GPU", "manufacturer": "NVIDIA",
                 "memory": 80, "secure": True, "price": {"secure": self.price}, "availability": "HIGH"}]

    def create_pod(self, body):
        self.create_calls += 1
        self.events.append("pod_create")
        self.body = body
        self.pod = {"id": "pod_test1234", "status": "PROVISIONING", "env": body["env"]}
        return {"id": self.pod["id"], "status": "PROVISIONING"}

    def get_pod(self, pod_id):
        env = dict(self.pod["env"])
        if self.owner_override:
            env[OWNER_PREFIX] = self.owner_override
        status = "TERMINATED" if self.pod.get("terminated") else "RUNNING"
        return {"id": pod_id, "status": status, "env": env}

    def delete_pod(self, pod_id):
        self.delete_calls += 1
        self.events.append("pod_delete")
        self.pod["terminated"] = True
        return {}

    def list_pods(self):
        return [self.pod] if self.pod else []


def project(pid="a" * 32):
    return {
        "id": pid,
        "title": "test",
        "continuity": "same character",
        "aspect_ratio": "16:9",
        "status": "planned",
        "scenes": [{"number": 1, "prompt": "a cat walks", "duration": 2, "assets": {}}],
    }


def video_model():
    return {"id": "Org/video", "revision": "a" * 40, "pipeline": "text-to-video",
            "library": "diffusers", "weight_bytes": 1024 ** 3, "estimated_vram_gb": 24,
            "license": "apache-2.0"}


def setup_config(monkeypatch):
    monkeypatch.setattr(config, "FILM_WORKER_IMAGE", "registry.test/jude-film:1")
    monkeypatch.setattr(config, "RUNPOD_FILM_MAX_RUNTIME_SECONDS", 3600)
    monkeypatch.setattr(config, "RUNPOD_FILM_MAX_COST_USD", 5.0)
    monkeypatch.setattr(config, "RUNPOD_FILM_HARD_MAX_COST_USD", 25.0)
    monkeypatch.setattr(config, "RUNPOD_FILM_MAX_DISK_GB", 200)
    monkeypatch.setattr(config, "RUNPOD_FILM_MIN_CUDA_VERSION", "12.8")


def test_production_downloads_assets_before_terminating_owned_pod(tmp_path, monkeypatch):
    setup_config(monkeypatch)
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    events = []
    fake_pod = FakeRunpod(events)
    job_id = "b" * 32
    checksum = hashlib.sha256(MP4).hexdigest()

    def worker(request):
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.headers["authorization"] != "Bearer runpod-api-key"
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ready", "cuda": True})
        if request.method == "POST" and request.url.path == "/v1/jobs":
            events.append("job_submit")
            payload = json.loads(request.content)
            assert payload["revision"] == "a" * 40
            assert payload["kind"] == "video"
            return httpx.Response(200, json={"job_id": job_id, "request_id": payload["request_id"], "status": "queued"})
        if request.method == "GET" and request.url.path == f"/v1/jobs/{job_id}":
            return httpx.Response(200, json={"job_id": job_id, "status": "completed", "artifact": True, "sha256": checksum})
        if request.method == "GET" and request.url.path == f"/v1/jobs/{job_id}/artifact":
            events.append("asset_download")
            return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})
        pytest.fail(f"Unexpected worker request: {request.method} {request.url.path}")

    operations = RunpodOperationsAgent(store=store, client=fake_pod,
                                        worker_transport=httpx.MockTransport(worker), sleep=lambda _: None)
    result = operations.produce(saved, {"video": video_model()})
    assert result["runpod"]["cleanup_status"] == "terminated"
    assert saved["scenes"][0]["assets"]["video"]["status"] == "downloaded"
    from pathlib import Path
    assert Path(saved["scenes"][0]["assets"]["video"]["path"]).read_bytes() == MP4
    assert events.index("asset_download") < events.index("pod_delete")
    assert fake_pod.create_calls == fake_pod.delete_calls == 1
    assert fake_pod.body["ports"] == ["8000/http"]
    assert fake_pod.body["startSsh"] is False and fake_pod.body["startJupyter"] is False
    assert "mounts" not in fake_pod.body
    assert "RUNPOD_API_KEY" not in fake_pod.body["env"]


def test_uncertain_paid_job_is_not_resubmitted_and_pod_is_cleaned(tmp_path, monkeypatch):
    setup_config(monkeypatch)
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    fake_pod = FakeRunpod()
    posts = []

    def worker(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ready", "cuda": True})
        if request.method == "POST" and request.url.path == "/v1/jobs":
            posts.append(request)
            return httpx.Response(502, json={"error": "unknown"})
        if request.method == "GET" and "/v1/jobs/by-request/" in request.url.path:
            return httpx.Response(404, json={"detail": "not found"})
        pytest.fail(f"Unexpected worker request: {request.method} {request.url.path}")

    operations = RunpodOperationsAgent(store=store, client=fake_pod,
                                        worker_transport=httpx.MockTransport(worker), sleep=lambda _: None)
    with pytest.raises(FilmError, match="not repeated"):
        operations.produce(saved, {"video": video_model()})
    assert len(posts) == 1
    assert fake_pod.delete_calls == 1
    assert saved["scenes"][0]["assets"]["video"]["status"] == "unknown"
    assert saved["run"]["cleanup_status"] == "terminated"


def test_artifact_get_retries_without_resubmitting_paid_job(tmp_path, monkeypatch):
    setup_config(monkeypatch)
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    events = []
    fake_pod = FakeRunpod(events)
    checksum = hashlib.sha256(MP4).hexdigest()
    posts = []
    downloads = []

    def worker(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ready", "cuda": True})
        if request.method == "POST" and request.url.path == "/v1/jobs":
            posts.append(request)
            return httpx.Response(200, json={"job_id": "e" * 32, "status": "queued"})
        if request.method == "GET" and request.url.path == f"/v1/jobs/{'e' * 32}":
            return httpx.Response(200, json={"status": "completed", "artifact": True, "sha256": checksum})
        if request.method == "GET" and request.url.path.endswith("/artifact"):
            downloads.append(request)
            events.append("asset_download")
            if len(downloads) == 1:
                return httpx.Response(503)
            return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})
        pytest.fail(f"Unexpected worker request: {request.method} {request.url.path}")

    operations = RunpodOperationsAgent(store=store, client=fake_pod,
                                        worker_transport=httpx.MockTransport(worker), sleep=lambda _: None)
    operations.produce(saved, {"video": video_model()})
    assert len(posts) == 1
    assert len(downloads) == 2
    assert events.index("asset_download") < events.index("pod_delete")
    assert saved["scenes"][0]["assets"]["video"]["status"] == "downloaded"


def test_cost_preflight_blocks_before_create(tmp_path, monkeypatch):
    setup_config(monkeypatch)
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    fake_pod = FakeRunpod(price=10.0)
    operations = RunpodOperationsAgent(store=store, client=fake_pod, sleep=lambda _: None)
    with pytest.raises(FilmError, match=r"above the \$5.00 job ceiling"):
        operations.produce(saved, {"video": video_model()})
    assert fake_pod.create_calls == 0


def test_cleanup_refuses_pod_with_different_owner_marker(tmp_path, monkeypatch):
    setup_config(monkeypatch)
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    run_id = "c" * 32
    token = "t" * 64
    store.write_worker_token(saved["id"], run_id, token)
    fake_pod = FakeRunpod(owner_override="d" * 32)
    fake_pod.pod = {"id": "pod_test1234", "status": "RUNNING", "env": {OWNER_PREFIX: run_id}}
    saved["run"] = {"run_id": run_id, "pod_id": "pod_test1234", "cleanup_status": "active"}
    store.save(saved)
    result = RunpodOperationsAgent(store=store, client=fake_pod, sleep=lambda _: None).cleanup(saved["id"])
    assert result["terminated"] is False
    assert result["cleanup_status"] == "owner_mismatch"
    assert fake_pod.delete_calls == 0
