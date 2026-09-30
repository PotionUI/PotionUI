from dataclasses import replace

import pytest

from src.features.cloud.policy import CloudGenerationPolicy, CloudPolicyViolation
from src.features.forms.binding import bind_form
from src.features.models.form_refs import make_model_ref
from src.features.models.repository import model_repo
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate
from src.features.cloud.testing.fake import fake_specs
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def field(name, type_, capability=None, default=None):
    return FieldTemplate(type=type_, name=name, default=default, capability=capability)


def preset(task):
    fields = [
        field("model", "model"),
        field("references", "image", {"model_field": "model", "input": "reference"}),
        field("first", "image", {"model_field": "model", "input": "first_frame"}),
    ]
    return PresetTemplate(
        id="preset_1", name="Cloud", version="1.0.0", path="/presets/preset_1", engine="cloud", driver="cloud.fake",
        modes={"txt2img": ModeTemplate(
            forms=[FormTemplate(name="custom", fields=fields, default=True, order=0)],
            pipes=[PipeTemplate(name="cloud_generate", configuration={"task": task})],
        )},
    )


def bound_for(env, template, model_id, **media):
    return bind_form(
        template, "txt2img", None, {"model": make_model_ref(model_id), **media}, "u1",
        cloud_capabilities=env.capabilities.spec_for,
    )


@pytest.fixture
def policy(refreshed):
    return CloudGenerationPolicy(refreshed.repository, model_repo)


async def enabled(env, provider_model_id):
    slug = env.slug_of(provider_model_id)
    await env.catalog.set_enabled("cloud-1", [slug], True)
    return env.model_row(slug).id


async def test_a_supported_task_and_media_pass(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("img_edit")
    bound = bound_for(refreshed, template, model_id, references=["a.png", "b.jpg"])

    policy.check(template, "txt2img", bound, refreshed.backend())


async def test_a_model_that_is_not_enabled_on_the_routed_backend_is_refused_plainly(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("txt2img")
    bound = bound_for(refreshed, template, model_id)
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], False)

    with pytest.raises(CloudPolicyViolation) as raised:
        policy.check(template, "txt2img", bound, refreshed.backend())

    assert str(raised.value) == "'Fake Image' is not enabled on Fake cloud. Ask an administrator to enable it."


async def test_a_model_the_provider_no_longer_offers_is_refused(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("txt2img")
    bound = bound_for(refreshed, template, model_id)
    refreshed.backend().provider.discover = returning([spec for spec in fake_specs() if spec.provider_model_id != IMAGE])
    await refreshed.catalog.refresh("cloud-1")

    with pytest.raises(CloudPolicyViolation, match="not enabled"):
        policy.check(template, "txt2img", bound, refreshed.backend())


async def test_a_model_enabled_on_another_backend_only_is_refused_for_this_one(refreshed, policy):
    await refreshed.add_backend("cloud-2", "Second account")
    await refreshed.catalog.refresh("cloud-2")
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-2", [slug], True)
    model_id = refreshed.model_row(slug).id
    template = preset("txt2img")
    bound = bound_for(refreshed, template, model_id)

    with pytest.raises(CloudPolicyViolation, match="not enabled on Fake cloud"):
        policy.check(template, "txt2img", bound, refreshed.backend("cloud-1"))
    policy.check(template, "txt2img", bound, refreshed.backend("cloud-2"))


async def test_a_task_the_model_cannot_do_is_refused(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("img2video")
    bound = bound_for(refreshed, template, model_id)

    with pytest.raises(CloudPolicyViolation) as raised:
        policy.check(template, "txt2img", bound, refreshed.backend())

    assert str(raised.value) == "'Fake Image' cannot do img2video. Choose a model that supports it."


async def test_a_mode_without_a_literal_task_skips_the_task_check(refreshed, policy):
    model_id = await enabled(refreshed, VIDEO)
    template = preset("{{ form.task }}")
    bound = bound_for(refreshed, template, model_id)

    policy.check(template, "txt2img", bound, refreshed.backend())


async def test_more_media_files_than_the_model_accepts_are_refused(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("img_edit")
    bound = bound_for(refreshed, template, model_id, references=[f"{i}.png" for i in range(5)])

    with pytest.raises(CloudPolicyViolation, match="accepts at most 4 reference file"):
        policy.check(template, "txt2img", bound, refreshed.backend())


async def test_too_few_media_files_are_refused(refreshed, policy):
    needy = replace(fake_specs()[0], provider_model_id="fake/needy-1", label="Needy", inputs=(
        replace(fake_specs()[0].inputs[0], min_items=1),
    ))
    refreshed.backend().provider.discover = returning([needy])
    await refreshed.catalog.refresh("cloud-1")
    model_id = await enabled(refreshed, "fake/needy-1")
    template = preset("img_edit")
    bound = bound_for(refreshed, template, model_id)

    with pytest.raises(CloudPolicyViolation, match="needs at least 1 reference file"):
        policy.check(template, "txt2img", bound, refreshed.backend())


async def test_a_media_format_the_model_does_not_take_is_refused(refreshed, policy):
    picky = replace(fake_specs()[0], provider_model_id="fake/picky-1", label="Picky", inputs=(
        replace(fake_specs()[0].inputs[0], formats=("png", "image/jpeg")),
    ))
    refreshed.backend().provider.discover = returning([picky])
    await refreshed.catalog.refresh("cloud-1")
    model_id = await enabled(refreshed, "fake/picky-1")
    template = preset("img_edit")

    policy.check(template, "txt2img", bound_for(refreshed, template, model_id, references=["a.PNG", "b.jpg", "c.jpeg"]), refreshed.backend())
    with pytest.raises(CloudPolicyViolation, match="'notes.gif' is not an accepted reference format"):
        policy.check(template, "txt2img", bound_for(refreshed, template, model_id, references=["notes.gif"]), refreshed.backend())


async def test_media_given_as_upload_objects_is_checked_by_its_path(refreshed, policy):
    picky = replace(fake_specs()[0], provider_model_id="fake/picky-2", label="Picky", inputs=(
        replace(fake_specs()[0].inputs[0], formats=("png",)),
    ))
    refreshed.backend().provider.discover = returning([picky])
    await refreshed.catalog.refresh("cloud-1")
    model_id = await enabled(refreshed, "fake/picky-2")
    template = preset("img_edit")
    bound = bound_for(refreshed, template, model_id, references=[{"path": "/x/a.webp", "relative_path": "a.webp"}])

    with pytest.raises(CloudPolicyViolation, match="a.webp"):
        policy.check(template, "txt2img", bound, refreshed.backend())


async def test_several_problems_are_reported_together(refreshed, policy):
    model_id = await enabled(refreshed, IMAGE)
    template = preset("img_edit")
    bound = bound_for(refreshed, template, model_id, references=[f"{i}.png" for i in range(6)])

    assert "accepts at most 4" in _message(policy, template, bound, refreshed)


async def test_a_form_without_a_cloud_model_is_not_the_policys_business(refreshed, policy):
    from src.features.models.records import Model

    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))
    template = preset("txt2img")
    bound = bound_for(refreshed, template, lora.id)

    policy.check(template, "txt2img", bound, refreshed.backend())


def _message(policy, template, bound, env):
    with pytest.raises(CloudPolicyViolation) as raised:
        policy.check(template, "txt2img", bound, env.backend())
    return str(raised.value)


async def test_media_is_judged_by_the_model_its_field_is_bound_to(refreshed, policy):
    solo = replace(fake_specs()[0], provider_model_id="fake/solo-1", label="Solo", inputs=(
        replace(fake_specs()[0].inputs[0], max_items=1),
    ))
    refreshed.backend().provider.discover = returning([solo, fake_specs()[0]])
    await refreshed.catalog.refresh("cloud-1")
    solo_id = await enabled(refreshed, "fake/solo-1")
    wide_id = await enabled(refreshed, IMAGE)
    template = preset("img_edit")
    template.modes["txt2img"].forms[0].fields[:] = [
        field("model", "model"),
        field("wide_model", "model"),
        field("references", "image", {"model_field": "wide_model", "input": "reference"}),
    ]
    bound = bind_form(
        template, "txt2img", None,
        {"model": make_model_ref(solo_id), "wide_model": make_model_ref(wide_id), "references": ["a.png", "b.png", "c.png"]},
        "u1", cloud_capabilities=refreshed.capabilities.spec_for,
    )

    policy.check(template, "txt2img", bound, refreshed.backend())
