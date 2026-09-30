import httpx

from src.features.downloads import queue as queue_module
from src.features.downloads.queue import DownloadQueue


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"siblings": [{"rfilename": "a/model.safetensors", "size": 5}, {"rfilename": "readme.md"}]}


def _queue():
    instance = DownloadQueue.__new__(DownloadQueue)
    instance._hf_token = lambda: "tok"
    return instance


def test_listing_uses_shared_ssl_context_and_follows_redirects(monkeypatch):
    sentinel = object()
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr(queue_module, "client_ssl_context", lambda: sentinel)
    monkeypatch.setattr(httpx, "get", fake_get)

    files = _queue()._enumerate_hf_repo("org/repo", None, ["*.safetensors"])

    assert seen["kwargs"]["verify"] is sentinel
    assert seen["kwargs"]["follow_redirects"] is True
    assert seen["kwargs"]["headers"] == {"Authorization": "Bearer tok"}
    assert seen["kwargs"]["params"] == {"blobs": "true"}
    assert files == [("a/model.safetensors", 5, "https://huggingface.co/org/repo/resolve/main/a/model.safetensors")]
