import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app


class InlineExecutor:
    def submit(self, function, *args):
        function(*args)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDE_FILM_WORKER_TOKEN", "t" * 64)
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(app, "ASSETS", tmp_path / "assets")
    monkeypatch.setattr(app, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(app, "DB", tmp_path / "jobs.sqlite3")
    app.ASSETS.mkdir()
    app.ARTIFACTS.mkdir()
    monkeypatch.setattr(app, "EXECUTOR", InlineExecutor())
    monkeypatch.setattr(app, "_process", lambda *_: None)
    with TestClient(app.app) as value:
        yield value


def headers():
    return {"Authorization": "Bearer " + "t" * 64}


def test_all_worker_routes_require_per_pod_token(client):
    response = client.get("/healthz")
    assert response.status_code == 401
    assert client.get("/healthz", headers=headers()).status_code == 200


def test_job_submission_is_idempotent_by_request_id(client):
    body = {"request_id": "a" * 32, "kind": "video", "model_id": "Org/video",
            "revision": "b" * 40, "pipeline": "text-to-video", "library": "diffusers",
            "prompt": "cat walks", "duration_seconds": 2, "aspect_ratio": "16:9"}
    first = client.post("/v1/jobs", json=body, headers=headers())
    second = client.post("/v1/jobs", json=body, headers=headers())
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    with sqlite3.connect(app.DB) as db:
        assert db.execute("select count(*) from jobs").fetchone()[0] == 1


@pytest.mark.parametrize("patch", [
    {"library": "custom"},
    {"pipeline": "text-to-image"},
    {"model_id": "../../etc/passwd"},
])
def test_worker_rejects_unsupported_or_unsafe_model_request(client, patch):
    body = {"request_id": "c" * 32, "kind": "video", "model_id": "Org/video",
            "revision": "b" * 40, "pipeline": "text-to-video", "library": "diffusers",
            "prompt": "cat walks", "duration_seconds": 2}
    body.update(patch)
    response = client.post("/v1/jobs", json=body, headers=headers())
    assert response.status_code == 422


def test_reference_upload_checks_image_contents_and_size(client):
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(image, format="PNG")
    response = client.post("/v1/assets", files={"file": ("reference.png", image.getvalue(), "image/png")}, headers=headers())
    assert response.status_code == 200
    asset_id = response.json()["asset_id"]
    assert (app.ASSETS / (asset_id + ".img")).is_file()
    invalid = client.post("/v1/assets", files={"file": ("reference.png", b"not a PNG", "image/png")}, headers=headers())
    assert invalid.status_code == 415
