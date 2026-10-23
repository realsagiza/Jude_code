import httpx
import pytest

from judecode.film.errors import FilmError
from judecode.film.runpod import RunpodClient, choose_gpu


def gpu(name, memory, price, availability="HIGH"):
    return {"id": name, "name": name, "manufacturer": "NVIDIA", "memory": memory,
            "secure": True, "price": {"secure": price}, "availability": availability}


def models(weight_bytes=1024 ** 3, vram=20):
    return [{"id": "Org/video", "weight_bytes": weight_bytes, "estimated_vram_gb": vram}]


def test_choose_gpu_selects_lowest_secure_price_with_stock_and_headroom():
    options = [gpu("too-small", 16, 0.10), gpu("out-of-stock", 80, 0.20, "NONE"),
               gpu("fast-expensive", 80, 2.0), gpu("reasonable", 24, 0.50)]
    result = choose_gpu(models(), options, max_runtime_seconds=3600, max_cost_usd=5, max_disk_gb=200)
    assert result["gpu_id"] == "reasonable"
    assert result["estimated_gpu_cost_usd"] == 0.5
    assert result["ephemeral_disk_gb"] == 40
    assert "invoice totals may differ" in result["estimate_basis"]


def test_no_stock_or_capacity_fails_before_pod_create():
    with pytest.raises(FilmError, match="No currently available Secure GPU"):
        choose_gpu(models(), [gpu("small", 16, 0.2)], 3600, 5, 200)


def test_hard_runtime_cost_ceiling_prevents_expensive_gpu():
    with pytest.raises(FilmError, match=r"above the \$1.00 job ceiling"):
        choose_gpu(models(), [gpu("big", 80, 2)], 3600, 1, 200)


def test_disk_capacity_is_checked_from_hf_weight_bytes():
    with pytest.raises(FilmError, match="ephemeral disk"):
        choose_gpu(models(weight_bytes=200 * 1024 ** 3, vram=80), [gpu("big", 96, 1)], 3600, 5, 200)


def test_gpu_catalog_uses_current_secure_pod_stock_query():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer runpod-test"
        return httpx.Response(200, json={"gpus": [gpu("RTX", 48, 1)]})

    client = RunpodClient(api_key="runpod-test", transport=httpx.MockTransport(handler))
    result = client.gpu_catalog()
    assert result[0]["id"] == "RTX"
    assert seen[0].url.path == "/v2/catalog/gpus"
    assert seen[0].url.params["product"] == "POD"
    assert seen[0].url.params["cloud"] == "SECURE"
    assert seen[0].url.params["minCudaVersion"] == "12.8"


def test_pod_list_follows_rest_v2_pagination_cursor():
    seen = []

    def handler(request):
        seen.append(request.url.params.get("cursor"))
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json={"pods": [{"id": "pod_first"}],
                                             "pagination": {"hasNextPage": True, "nextCursor": "cursor-2"}})
        assert request.url.params["cursor"] == "cursor-2"
        return httpx.Response(200, json={"pods": [{"id": "pod_second"}],
                                         "pagination": {"hasNextPage": False, "nextCursor": None}})

    client = RunpodClient(api_key="runpod-test", transport=httpx.MockTransport(handler))
    assert [pod["id"] for pod in client.list_pods()] == ["pod_first", "pod_second"]
    assert seen == [None, "cursor-2"]


def test_runpod_client_fails_closed_without_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    client = RunpodClient(api_key="", transport=httpx.MockTransport(handler))
    with pytest.raises(FilmError, match="RUNPOD_API_KEY"):
        client.gpu_catalog()
    assert calls == []
