import pytest

from src.plugin_api.cloud import CloudRequest, LocalMedia

from .fixtures import PNG
from .test_openrouter_provider import build


@pytest.fixture
async def provider(openrouter):
    made, http = build(openrouter)
    try:
        yield made
    finally:
        await http.close()


async def specs_by_id(provider):
    return {spec.provider_model_id: spec for spec in await provider.discover()}


def frame(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(PNG)
    return LocalMedia(path=path, media_type="image/png", size=len(PNG))


async def test_a_model_that_lists_one_frame_rate_declares_it_for_the_director(provider, openrouter):
    openrouter.video_models["data"][0]["supported_fps"] = [30]

    spec = (await specs_by_id(provider))["vendor-v/clip-pro"]

    assert spec.director == {"limits": {"default_fps": 30}}


async def test_without_a_single_known_frame_rate_nothing_is_declared(provider, openrouter):
    openrouter.video_models["data"][0]["supported_fps"] = [24, 30]

    specs = await specs_by_id(provider)

    assert specs["vendor-v/clip-pro"].director == {} and specs["vendor-v/clip-lite"].director == {}


async def test_the_plugin_file_can_tune_one_models_director_capabilities(provider, monkeypatch, tmp_path):
    from backend import provider as provider_module

    catalog = tmp_path / "cloud_models.yml"
    catalog.write_text(
        "suggested: []\n"
        "director:\n"
        "  vendor-v/clip-pro:\n"
        "    limits: {default_fps: 25}\n"
        "    modes: {director: {max_segments: 3}}\n"
        "  vendor-v/unknown: {limits: {default_fps: 12}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_module, "SUGGESTIONS_FILE", catalog)

    specs = await specs_by_id(provider)

    assert specs["vendor-v/clip-pro"].director == {"limits": {"default_fps": 25}, "modes": {"director": {"max_segments": 3}}}
    assert specs["vendor-v/clip-lite"].director == {}


async def test_the_shipped_plugin_file_is_readable_and_declares_no_unknown_models(provider):
    overrides = provider.director_overrides()

    assert isinstance(overrides, dict)
    assert all("/" in model_id for model_id in overrides)


async def test_a_director_shot_with_a_start_and_an_end_picture_sends_both_frame_images(provider, openrouter, tmp_path):
    spec = (await specs_by_id(provider))["vendor-v/clip-pro"]
    request = CloudRequest(
        model=spec, task="img2video", prompt="the door opens", client_reference="gen-1",
        params={"duration_s": 4},
        inputs={"first_frame": [frame(tmp_path, "a.png")], "last_frame": [frame(tmp_path, "b.png")]},
    )

    await provider.submit(request)

    body = [r for r in openrouter.requests if r["path"] == "/videos"][-1]["body"]
    assert body["duration"] == 4
    assert [item["frame_type"] for item in body["frame_images"]] == ["first_frame", "last_frame"]
    assert all(item["image_url"]["url"].startswith("data:image/png;base64,") for item in body["frame_images"])
