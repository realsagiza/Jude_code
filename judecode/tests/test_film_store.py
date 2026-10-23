import json
import os
import stat
from pathlib import Path

import pytest

from judecode.film.errors import FilmError
from judecode.film.store import FilmStore


def project(project_id="a" * 32):
    return {"id": project_id, "title": "Test", "status": "planned", "scenes": []}


def test_private_checkpoint_and_separate_worker_token(tmp_path):
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    path = store.create(saved)
    assert store.load(saved["id"])["title"] == "Test"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "state").st_mode) == 0o700
    token_path = store.write_worker_token(saved["id"], "b" * 32, "t" * 64)
    assert store.read_worker_token(saved["id"], "b" * 32) == "t" * 64
    assert "t" * 64 not in json.dumps(store.load(saved["id"]))
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600
    store.remove_worker_token(saved["id"], "b" * 32)
    with pytest.raises(FilmError, match="unavailable"):
        store.read_worker_token(saved["id"], "b" * 32)


def test_project_id_path_traversal_is_rejected(tmp_path):
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    with pytest.raises(FilmError, match="Invalid film project ID"):
        store.load("../secret")


def test_token_write_is_exclusive_and_does_not_follow_symlink(tmp_path):
    store = FilmStore(tmp_path / "state", tmp_path / "outputs")
    saved = project()
    store.create(saved)
    run_id = "c" * 32
    path = store.write_worker_token(saved["id"], run_id, "x" * 64)
    with pytest.raises(FilmError, match="already exists"):
        store.write_worker_token(saved["id"], run_id, "y" * 64)
    assert Path(path).read_text(encoding="ascii") == "x" * 64
