import base64
import math

import pytest

from src.plugin_api.cloud import CloudError

from backend.mapping import dimensions

from .fixtures import KEY, PNG
from .support import build, pictures, request_for, submits


async def test_txt2img_sends_the_documented_body_to_the_model_endpoint_with_the_key(provider, bfl):
    request = await request_for(
        provider, "flux-2-pro", seed=7,
        params={"aspect_ratio": "16:9", "resolution": "1K", "output_format": "png", "enhance_prompt": False, "x.safety_tolerance": 3},
    )

    job = await provider.submit(request)

    call = submits(bfl)[-1]
    assert call["path"] == "/v1/flux-2-pro"
    assert call["headers"]["x-key"] == KEY
    assert call["body"] == {
        "prompt": "a lighthouse", "width": 1360, "height": 768, "seed": 7,
        "output_format": "png", "disable_pup": True, "safety_tolerance": 3,
    }
    assert job.job_id == "task-1" and job.result is None and job.poll_after_s == 1.0


async def test_flex_sends_steps_guidance_and_prompt_upsampling(provider, bfl):
    request = await request_for(
        provider, "flux-2-flex",
        params={"aspect_ratio": "1:1", "resolution": "2K", "guidance": 4.5, "steps": 30.0, "enhance_prompt": True},
    )

    await provider.submit(request)

    body = submits(bfl)[-1]["body"]
    assert (body["width"], body["height"]) == (2048, 2048)
    assert body["guidance"] == 4.5 and body["steps"] == 30 and isinstance(body["steps"], int)
    assert body["prompt_upsampling"] is True and "disable_pup" not in body


async def test_ultra_sends_an_aspect_ratio_and_raw_mode(provider, bfl):
    await provider.submit(await request_for(provider, "flux-pro-1.1-ultra", params={"aspect_ratio": "21:9", "x.raw": True}))

    body = submits(bfl)[-1]["body"]
    assert body == {"prompt": "a lighthouse", "aspect_ratio": "21:9", "raw": True}
    assert submits(bfl)[-1]["path"] == "/v1/flux-pro-1.1-ultra"


async def test_kontext_edit_sends_each_reference_as_base64_in_its_own_field(provider, bfl, tmp_path):
    refs = pictures(tmp_path, 3)
    request = await request_for(
        provider, "flux-kontext-pro", task="img_edit", prompt="make it night", seed=11,
        params={"aspect_ratio": "auto", "enhance_prompt": True}, inputs={"reference": refs},
    )

    await provider.submit(request)

    call = submits(bfl)[-1]
    body = call["body"]
    assert call["path"] == "/v1/flux-kontext-pro"
    assert [base64.b64decode(body[name]) for name in ("input_image", "input_image_2", "input_image_3")] == [
        PNG + bytes([0]), PNG + bytes([1]), PNG + bytes([2]),
    ]
    assert "input_image_4" not in body and "aspect_ratio" not in body and "width" not in body
    assert body["seed"] == 11 and body["prompt_upsampling"] is True and body["prompt"] == "make it night"


async def test_kontext_edit_keeps_a_chosen_aspect_ratio(provider, bfl, tmp_path):
    request = await request_for(
        provider, "flux-kontext-max", task="img_edit", params={"aspect_ratio": "4:3"}, inputs={"reference": pictures(tmp_path, 1)},
    )

    await provider.submit(request)

    body = submits(bfl)[-1]["body"]
    assert body["aspect_ratio"] == "4:3" and "input_image" in body


async def test_flux2_edit_with_auto_size_lets_the_first_picture_decide(provider, bfl, tmp_path):
    request = await request_for(
        provider, "flux-2-pro-preview", task="img_edit", params={"aspect_ratio": "auto", "resolution": "2K"},
        inputs={"reference": pictures(tmp_path, 2)},
    )

    await provider.submit(request)

    body = submits(bfl)[-1]["body"]
    assert "width" not in body and "height" not in body
    assert {"input_image", "input_image_2"} <= set(body)


async def test_flux3_edit_sends_an_images_list_and_no_seed_or_format(provider, bfl, tmp_path):
    request = await request_for(
        provider, "flux-3-image", task="img_edit", seed=5,
        params={"aspect_ratio": "auto", "resolution": "2k", "x.grounding": False}, inputs={"reference": pictures(tmp_path, 2)},
    )

    await provider.submit(request)

    body = submits(bfl)[-1]["body"]
    assert [base64.b64decode(item) for item in body["images"]] == [PNG + bytes([0]), PNG + bytes([1])]
    assert body["aspect_ratio"] == "auto" and body["resolution"] == "2k" and body["grounding"] is False
    assert not {"seed", "output_format", "input_image", "user"} & set(body)


async def test_a_text_to_image_run_never_sends_pictures(provider, bfl, tmp_path):
    await provider.submit(await request_for(provider, "flux-kontext-pro", inputs={"reference": pictures(tmp_path, 1)}))

    assert "input_image" not in submits(bfl)[-1]["body"]


async def test_more_pictures_than_the_model_takes_is_refused_before_sending(provider, bfl, tmp_path):
    request = await request_for(provider, "flux-2-klein-4b", task="img_edit", inputs={"reference": pictures(tmp_path, 5)})

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "invalid_request" and raised.value.request_sent is False
    assert submits(bfl) == []


async def test_no_user_id_is_sent_by_default(provider, bfl):
    await provider.submit(await request_for(provider, user_ref="anon-123"))

    assert "user" not in submits(bfl)[-1]["body"]


async def test_the_anonymous_user_id_is_sent_only_when_the_admin_turns_it_on(bfl):
    made, http = build(bfl, send_user_hash=True)
    try:
        await made.submit(await request_for(made, user_ref="anon-123"))
    finally:
        await http.close()

    assert submits(bfl)[-1]["body"]["user"] == "anon-123"


async def test_pictures_are_read_off_the_event_loop_thread(provider, monkeypatch, tmp_path):
    import threading

    import backend.provider as provider_module

    real = provider_module.build_body
    threads = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(provider_module, "build_body", spy)

    await provider.submit(await request_for(provider, "flux-kontext-pro", task="img_edit", inputs={"reference": pictures(tmp_path, 1)}))

    assert threads and threads[0] != threading.get_ident()


RAW_FLUX2 = {"step": 16, "min_side": 64, "max_side": 2048}
RAW_FLUX1 = {"step": 32, "min_side": 256, "max_side": 1440}


@pytest.mark.parametrize("raw,limit", [(RAW_FLUX2, 1024 * 1024), (RAW_FLUX1, 1024 * 1024)])
@pytest.mark.parametrize("ratio", ["21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16", "9:21"])
def test_sizes_are_on_the_grid_inside_the_bounds_and_close_to_the_ratio(raw, limit, ratio):
    width, height = dimensions(ratio, "1K", raw)
    wide, tall = (int(part) for part in ratio.split(":"))

    assert width % raw["step"] == 0 and height % raw["step"] == 0
    assert raw["min_side"] <= width <= raw["max_side"] and raw["min_side"] <= height <= raw["max_side"]
    assert width * height <= limit
    assert math.isclose(width / height, wide / tall, rel_tol=0.08)


def test_two_k_reaches_four_megapixels_and_no_more():
    assert dimensions("1:1", "2K", RAW_FLUX2) == (2048, 2048)
    assert dimensions("16:9", "2K", RAW_FLUX2) == (2048, 1152)


@pytest.mark.parametrize("value", [None, "", "auto", "wide", "1:0"])
def test_no_size_is_sent_without_a_real_ratio(value):
    assert dimensions(value, "1K", RAW_FLUX2) is None
