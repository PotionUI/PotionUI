from src.plugin_api.cloud import CloudHttp, CloudRequest, LocalMedia
from src.plugin_api.cloud_testing import FakeClock

from backend.config import BflConfig
from backend.provider import BflProvider

from .fixtures import KEY, PNG


def build(fixture, clock=None, **config):
    settings = {"id": "bfl-1", "name": "BFL", "api_key": KEY, "base_url": f"{fixture.api_url}/v1", **config}
    cfg = BflConfig(**settings)
    http = CloudHttp(
        BflProvider.api_base_url(cfg),
        auth_headers=BflProvider.auth_headers(cfg),
        timeout_s=10,
        clock=FakeClock(),
        allow_private_targets=True,
    )
    return BflProvider(cfg, http, clock=clock or FakeClock()), http


async def spec_of(provider, model_id):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def request_for(provider, model_id="flux-2-pro", task="txt2img", **fields):
    fields.setdefault("prompt", "a lighthouse")
    return CloudRequest(model=await spec_of(provider, model_id), task=task, client_reference="gen-1", **fields)


def pictures(tmp_path, count):
    media = []
    for index in range(count):
        path = tmp_path / f"ref-{index}.png"
        path.write_bytes(PNG + bytes([index]))
        media.append(LocalMedia(path, "image/png", path.stat().st_size))
    return media


def submits(fixture):
    return [r for r in fixture.requests if r["method"] == "POST"]


def polls(fixture):
    return [r for r in fixture.requests if r["path"] == "/v1/get_result"]


async def drive(provider, job, limit=10):
    status = None
    for _ in range(limit):
        status = await provider.poll(job)
        if status.state in ("succeeded", "failed", "cancelled", "expired"):
            return status
    return status
