from unittest.mock import Mock

import pytest

from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.fixed_prompt import FixedPrompt
from tests.features.generation.test_orchestrator_fixed_prompt import (
    FILM,
    NEGATIVE,
    PREFIX,
    SUFFIX,
    assert_user_version,
    bind_form_passthrough,
    make_orchestrator,
    make_request,
    pipeline_prompts,
    repo,
    start,
)

CAPABILITIES = {
    "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {}},
    "limits": {"default_duration": 5, "default_fps": 24, "max_duration": 15},
    "preset_modes": ["video"],
    "simple_modes": {"txt2video": {"composition": "t2v"}},
    "preset_mode_overrides": {"txt2video": {"modes": {"i2v": None, "flf": None, "director": None}}},
}
FORM = {"quantity": 1, "seed": 5, "duration": 5}


def build(fixed=FILM, batch=False):
    orchestrator, applied, processed = make_orchestrator(fixed=fixed, template_vars={"video_director": CAPABILITIES}, batch=batch)
    template = orchestrator.preset_template_loader.load_preset_by_id.return_value
    template.prompt_resources = {}
    template.tags = []
    orchestrator.settings.get_file_storage_directory = Mock(return_value="/storage")
    return orchestrator, applied, processed


def request(prompts, mode="txt2video", form=None, **kwargs):
    return make_request(prompts, form_data=dict(form or FORM), mode=mode, **kwargs)


async def test_a_mirrors_fixed_text_reaches_the_generator_in_a_simple_mode_and_stays_hidden():
    orchestrator, applied, _ = build()

    await start(orchestrator, request([{"positive": "a red fox", "negative": "blurry"}]))

    expected = [{"positive": f"{PREFIX} a red fox {SUFFIX}", "negative": f"blurry, {NEGATIVE}"}]
    assert pipeline_prompts(orchestrator) == expected
    assert applied.saved["g1"] == expected
    assert "g1" in orchestrator._fixed_prompt_runs
    form = orchestrator.pipeline_builder.build_pipeline.call_args.kwargs["form_data"]
    assert form["video_director"]["mode"] == "t2v"
    assert_user_version(form)


async def test_a_mirror_limited_to_another_mode_does_not_apply():
    orchestrator, applied, _ = build(fixed=FixedPrompt(prefix=PREFIX, modes=["video"]))

    await start(orchestrator, request([{"positive": "a red fox", "negative": ""}]))

    assert pipeline_prompts(orchestrator) == [{"positive": "a red fox", "negative": ""}]
    assert applied.saved == {}


async def test_a_mirror_listing_the_simple_mode_applies():
    orchestrator, _, _ = build(fixed=FixedPrompt(prefix=PREFIX, modes=["txt2video"]))

    await start(orchestrator, request([{"positive": "a red fox", "negative": ""}]))

    assert pipeline_prompts(orchestrator)[0]["positive"] == f"{PREFIX} a red fox"


async def test_the_director_mode_keeps_leaving_its_document_alone():
    orchestrator, applied, _ = build()
    document = {"schema_version": 1, "mode": "t2v", "settings": {"fps": 24, "duration": 5, "seed": 1},
                "segments": [{"id": "seg-1", "prompt": "a red fox", "negative_prompt": "", "start": 0, "end": 5}],
                "media": [], "audio": [], "ic_lora": []}

    await start(orchestrator, request([{"positive": "a red fox", "negative": ""}], mode="video",
                                      form={"quantity": 1, "seed": 5, "video_director": document}))

    assert_user_version(pipeline_prompts(orchestrator))
    assert applied.saved == {}
    assert orchestrator._fixed_prompt_runs == {}


async def test_a_templated_prompt_is_expanded_per_clip_in_a_simple_mode():
    orchestrator, _, _ = build(fixed=FixedPrompt())

    await start(orchestrator, request(
        [{"positive": "a {red|blue|green|black} fox", "negative": ""}], form={**FORM, "quantity": 2},
    ))

    prompts = pipeline_prompts(orchestrator)
    assert len(prompts) == 2
    assert all("{" not in pair["positive"] and "|" not in pair["positive"] for pair in prompts)


async def test_a_plain_prompt_gives_every_clip_the_same_text():
    orchestrator, _, _ = build(fixed=FixedPrompt())

    await start(orchestrator, request([{"positive": "a red fox", "negative": ""}], form={**FORM, "quantity": 2}))

    assert [pair["positive"] for pair in pipeline_prompts(orchestrator)] == ["a red fox", "a red fox"]


async def test_batch_prompts_are_not_refused_by_the_director_rule_in_a_simple_mode():
    orchestrator, _, _ = build(fixed=FixedPrompt(), batch=True)

    await start(orchestrator, request(
        [{"positive": "a red fox", "negative": ""}, {"positive": "a grey wolf", "negative": ""}], batch=True,
    ))

    assert [pair["positive"] for pair in pipeline_prompts(orchestrator)] == ["a red fox", "a grey wolf"]


async def test_access_is_per_preset_so_a_denied_user_cannot_name_a_simple_mode():
    orchestrator, _, _ = build(fixed=FixedPrompt())
    orchestrator.database_preset_repository = Mock()
    orchestrator.database_preset_repository.get_available_preset_ids_for_user.return_value = ["someone-elses"]
    orchestrator.database_preset_repository.get_preset_form_overrides.return_value = {}
    orchestrator.user_repository = Mock()
    orchestrator.user_repository.get_by_id.return_value = Mock(account_type="user")

    with pytest.raises(PresetNotFoundException):
        await start(orchestrator, request([{"positive": "a red fox", "negative": ""}]))

    orchestrator.pipeline_builder.build_pipeline.assert_not_called()
