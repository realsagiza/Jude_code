import httpx
import pytest

from judecode.film.errors import FilmError
from judecode.film.models import HuggingFaceCatalog, estimated_vram_gb


def row(model_id="Org/video", *, pipeline="text-to-video", library="diffusers", gated=False,
        private=False, license_id="apache-2.0"):
    return {
        "id": model_id,
        "sha": "a" * 40,
        "pipeline_tag": pipeline,
        "library_name": library,
        "gated": gated,
        "private": private,
        "downloads": 500,
        "likes": 12,
        "cardData": {"license": license_id, "language": ["en", "th"]},
        "tags": [f"license:{license_id}", "language:th"],
        "safetensors": {"total": 1_000_000_000},
    }


def test_model_search_filters_to_supported_task_and_ranks_eligible():
    def handler(request):
        task = request.url.params["pipeline_tag"]
        if task == "text-to-video":
            return httpx.Response(200, json=[row(), row("Org/unsupported", library="custom")])
        return httpx.Response(200, json=[row("Org/i2v", pipeline="image-to-video")])

    with HuggingFaceCatalog(token="", transport=httpx.MockTransport(handler)) as hub:
        result = hub.search("video", "cat", limit=5)
    by_id = {item["id"]: item for item in result["models"]}
    assert by_id["Org/video"]["eligible"] is True
    assert by_id["Org/video"]["languages"] == ["en", "th"]
    assert by_id["Org/unsupported"]["eligible"] is False
    assert by_id["Org/i2v"]["pipeline"] == "image-to-video"
    assert "No estimate proves GPU compatibility" in result["note"]


def test_inspect_pins_revision_and_totals_safetensors():
    model = row()

    def handler(request):
        if request.url.path.endswith("/tree/" + "a" * 40):
            assert request.url.params["limit"] == "100"
            return httpx.Response(200, json=[
                {"type": "file", "path": "weights-1.safetensors", "size": 100},
                {"type": "file", "path": "weights-2.safetensors", "size": 300},
                {"type": "file", "path": "readme.md", "size": 10000},
            ])
        assert request.url.path == "/api/models/Org/video"
        return httpx.Response(200, json=model)

    with HuggingFaceCatalog(token="", transport=httpx.MockTransport(handler)) as hub:
        info = hub.inspect("Org/video", "video")
    assert info["revision"] == "a" * 40
    assert info["weight_bytes"] == 400
    assert info["weight_files"] == 2
    assert info["license"] == "apache-2.0"


@pytest.mark.parametrize("changes, message", [
    ({"gated": True}, "gated"),
    ({"private": True}, "private"),
    ({"license_id": ""}, "license"),
    ({"library": "remote-code"}, "unsupported runtime"),
])
def test_inspect_rejects_gated_unlicensed_or_unsupported_before_tree(changes, message):
    value = row(**changes)

    def handler(request):
        assert "/tree/" not in request.url.path
        return httpx.Response(200, json=value)

    with HuggingFaceCatalog(token="", transport=httpx.MockTransport(handler)) as hub:
        with pytest.raises(FilmError, match=message):
            hub.inspect("Org/video", "video")


def test_inspect_rejects_repository_at_hf_tree_page_limit():
    def handler(request):
        if "/tree/" in request.url.path:
            return httpx.Response(200, json=[{"type": "file", "path": f"{i}.safetensors", "size": 1} for i in range(100)])
        return httpx.Response(200, json=row())

    with HuggingFaceCatalog(token="", transport=httpx.MockTransport(handler)) as hub:
        with pytest.raises(FilmError, match="too many repository files"):
            hub.inspect("Org/video", "video")


def test_parameters_have_conservative_vram_estimates():
    assert estimated_vram_gb("text-to-video", 1_000_000_000) >= 16
    assert estimated_vram_gb("text-to-speech", 1_000_000_000) >= 8
    assert estimated_vram_gb("text-to-video", None) is None
