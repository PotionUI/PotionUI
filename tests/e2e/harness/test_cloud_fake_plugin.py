from __future__ import annotations

import importlib.util
import io
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

_HARNESS_DIR = Path(__file__).resolve().parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import cloud_fake
import e2e_harness

from src.features.cloud.contracts import spec_problems
from src.features.presets.linter import PresetLinter
from src.platform.plugins.loader import EXTRA_PLUGIN_DIRS_ENV, PluginLoader

PLUGIN_DIR = cloud_fake.PLUGINS_ROOT / "cloud-fake"


def load_provider_module():
    spec = importlib.util.spec_from_file_location("e2e_cloud_fake_provider", PLUGIN_DIR / "provider" / "e2e_fake.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_plugin_is_found_through_the_extra_plugin_directory_seam(monkeypatch, tmp_path):
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, str(cloud_fake.PLUGINS_ROOT))

    found = PluginLoader(str(tmp_path / "none"), str(tmp_path / "none")).discover_plugins()

    (manifest,) = [m for m in found if m.id == cloud_fake.PLUGIN_ID]
    assert manifest.validation_error in (None, "")
    assert Path(manifest.plugin_dir) == PLUGIN_DIR


def test_without_the_seam_the_plugin_is_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv(EXTRA_PLUGIN_DIRS_ENV, raising=False)

    found = PluginLoader(str(tmp_path / "none"), str(tmp_path / "none")).discover_plugins()

    assert found == []


def test_several_directories_can_be_listed_with_the_platform_separator(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, os.pathsep.join([str(other), str(cloud_fake.PLUGINS_ROOT)]))

    loader = PluginLoader(str(tmp_path / "none"), str(tmp_path / "none"))

    assert loader.extra_dirs == [other, cloud_fake.PLUGINS_ROOT]
    assert [m.id for m in loader.discover_plugins()] == [cloud_fake.PLUGIN_ID]


def test_restarting_the_backend_stops_it_through_the_shared_helper_then_relaunches_and_logs_in(monkeypatch):
    from types import SimpleNamespace

    events = []
    app = SimpleNamespace(
        instance=object(), repo_root=Path("."), extra_env={"X": "1"}, client=object(), username="u", password="p",
    )
    monkeypatch.setattr(cloud_fake, "stop_backend_process", lambda instance, stage: events.append(("stop", stage)))
    monkeypatch.setattr(cloud_fake, "spawn_backend", lambda instance, root, extra_env: events.append(("spawn", extra_env)))
    monkeypatch.setattr(cloud_fake, "stage_wait_for_health", lambda client: events.append(("health", None)))
    monkeypatch.setattr(cloud_fake, "login", lambda client, username, password: events.append(("login", username)))

    cloud_fake.restart_backend(app)

    assert events == [("stop", "cloud-fake"), ("spawn", {"X": "1"}), ("health", None), ("login", "u")]


def test_the_plugin_env_points_the_backend_at_the_plugin_folder():
    assert cloud_fake.plugin_env() == {EXTRA_PLUGIN_DIRS_ENV: str(cloud_fake.PLUGINS_ROOT)}


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        (["cloud-fake-generate"], True),
        (["chat", "cloud-fake-cancel"], True),
        (["admin-cloud-catalog"], False),
        (["models"], False),
        ([], False),
    ],
)
def test_only_specs_named_for_the_fake_provider_get_the_seeded_backend(chunk, expected):
    assert cloud_fake.wants_cloud_fake(chunk) is expected


def test_the_fake_models_pass_the_platform_spec_checks_and_differ_in_what_they_offer():
    module = load_provider_module()

    full, lite = module.e2e_specs()

    assert spec_problems(full) == [] and spec_problems(lite) == []
    assert (full.provider_model_id, lite.provider_model_id) == (cloud_fake.FULL_MODEL, cloud_fake.LITE_MODEL)
    assert full.tasks == {"txt2img", "img_edit"} and lite.tasks == {"txt2img"}
    assert {p.name for p in full.params} == {"aspect_ratio", "quality", "background", "x.style"}
    assert {p.name for p in lite.params} == {"aspect_ratio", "x.style"}
    assert [p.values for p in lite.params if p.name == "aspect_ratio"] == [("1:1", "4:3")]
    assert [i.role for i in full.inputs] == ["reference"] and lite.inputs == ()
    assert full.pricing[0].usd > lite.pricing[0].usd


def test_the_images_are_deterministic_valid_pngs_that_differ_per_job():
    module = load_provider_module()

    first = module.image_bytes("job-1:0")
    again = module.image_bytes("job-1:0")
    other = module.image_bytes("job-2:0")

    assert first == again and first != other
    image = Image.open(io.BytesIO(first))
    assert image.format == "PNG" and image.size == (module.IMAGE_SIZE, module.IMAGE_SIZE)


async def test_the_provider_yields_one_image_per_requested_output_and_reports_its_cost():
    from decimal import Decimal

    from src.features.cloud.contracts import CloudRequest
    from src.features.cloud.http import CloudHttp

    module = load_provider_module()
    config = module.E2eFakeConfig(id="b", name="B", mode="sync", cost_usd=0.07)
    provider = module.E2eFakeProvider(config, CloudHttp("https://fake.invalid"))
    spec = (await provider.discover())[0]
    request = CloudRequest(model=spec, task="txt2img", prompt="a cat", count=3)

    job = await provider.submit(request)

    assert len(job.result.artifacts) == 3
    assert all(a.data and a.media_type == "image/png" for a in job.result.artifacts)
    assert job.result.cost.amount_usd == Decimal("0.07")


async def test_async_mode_follows_the_configured_queue_and_run_time():
    from src.features.cloud.contracts import CloudRequest
    from src.features.cloud.http import CloudHttp
    from src.features.cloud.testing.fake import FakeClock

    module = load_provider_module()
    config = module.E2eFakeConfig(id="b", name="B", mode="async", queue_seconds=1, duration_seconds=3)
    provider = module.E2eFakeProvider(config, CloudHttp("https://fake.invalid"))
    clock = FakeClock()
    provider.clock = clock
    spec = (await provider.discover())[0]

    job = await provider.submit(CloudRequest(model=spec, task="txt2img", prompt="a", count=1))
    assert job.result is None and job.poll_after_s == config.poll_seconds
    states = []
    for step in (0, 2, 2):
        clock.advance(step)
        states.append((await provider.poll(job)).state)

    assert states == ["queued", "running", "succeeded"]


async def test_a_configured_failure_kind_fails_every_submit():
    from src.features.cloud.contracts import CloudError, CloudRequest
    from src.features.cloud.http import CloudHttp

    module = load_provider_module()
    provider = module.E2eFakeProvider(module.E2eFakeConfig(id="b", name="B", fail_kind="credits"), CloudHttp("https://fake.invalid"))
    spec = (await provider.discover())[0]

    with pytest.raises(CloudError) as raised:
        await provider.submit(CloudRequest(model=spec, task="txt2img", prompt="a", count=1))

    assert raised.value.kind == "credits"


async def test_the_cancel_knob_decides_whether_the_provider_accepts_a_cancel():
    from src.features.cloud.contracts import CloudJob
    from src.features.cloud.http import CloudHttp

    module = load_provider_module()
    job = CloudJob(job_id="j-1")

    def provider(**knobs):
        return module.E2eFakeProvider(module.E2eFakeConfig(id="b", name="B", **knobs), CloudHttp("https://fake.invalid"))

    assert await provider().cancel(job) is True
    assert await provider(supports_cancel=False).cancel(job) is False
    assert provider(supports_cancel=False).supports_cancel is False
    assert provider().supports_cancel is True


def test_the_config_exposes_its_delay_knobs_to_the_admin_form():
    module = load_provider_module()

    names = {field["name"]: field for field in module.E2eFakeConfig.engine_fields()}

    assert {"mode", "queue_seconds", "duration_seconds", "poll_seconds", "fail_kind", "cost_usd", "supports_cancel", "api_key"} <= set(names)
    assert names["mode"]["options"] == ["sync", "async"]
    assert names["api_key"]["secret"] is True
    assert set(cloud_fake.DEFAULT_KNOBS) <= set(names)


def test_the_presets_use_only_shared_blocks_and_lint_clean():
    from src.plugin_api.cloud import CLOUD_BLOCKS

    root = PLUGIN_DIR / "presets"
    issues = PresetLinter([str(root)], registered_drivers={cloud_fake.DRIVER}).lint()

    assert [i for i in issues if i.level == "error"] == []
    used = set()
    for form in root.rglob("form.yml"):
        for line in form.read_text(encoding="utf-8").splitlines():
            if "paths._shared }}/cloud/" in line:
                used.add(line.split("/cloud/", 1)[1].strip().strip('"'))
    assert used and used <= set(CLOUD_BLOCKS)


def test_the_preset_declares_the_fake_driver_and_the_documented_id():
    import yaml

    manifest = yaml.safe_load((PLUGIN_DIR / "presets" / "Fake" / "studio" / "preset.yml").read_text(encoding="utf-8"))

    assert manifest["id"] == cloud_fake.PRESET_ID and manifest["name"] == cloud_fake.PRESET_NAME
    assert (manifest["engine"], manifest["driver"], manifest["modes"]) == ("cloud", cloud_fake.DRIVER, ["txt2img", "edit"])


live = pytest.mark.skipif(
    os.environ.get("POTIONUI_E2E_BACKEND") != "1",
    reason="boots a real throwaway backend; set POTIONUI_E2E_BACKEND=1 to run",
)


@pytest.fixture(scope="module")
def app():
    with e2e_harness.ThrowawayApp(extra_env=cloud_fake.plugin_env(), username="e2e-owner", password="e2e-owner-password-1") as running:
        running.backend_id = cloud_fake.prepare(running)
        yield running


def api_get(app, path, **kwargs):
    response = app.client.get(path, **kwargs)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.json().get("data", response.json())


def api_send(app, method, path, **kwargs):
    response = getattr(app.client, method)(path, **kwargs)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.json().get("data", response.json())


def wait_for_status(app, generation_id, wanted, timeout=60.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = api_get(app, f"/api/generations/{generation_id}/status")
        if last.get("status") in wanted:
            return last
        time.sleep(0.25)
    raise AssertionError(f"generation {generation_id} never reached {wanted}; last {last}")


def start(app, model_id, **form):
    body = {
        "preset_id": cloud_fake.PRESET_ID,
        "mode": "txt2img",
        "prompts": [{"positive": "a lighthouse at dusk", "negative": ""}],
        "form_data": {"model": f"model:{model_id}", "count": 1, "seed": 7, **form},
    }
    return api_send(app, "post", "/api/generations/start", json=body)["generation_id"]


def model_listing(app):
    return api_get(app, f"/api/presets/{cloud_fake.PRESET_ID}/models", params={"model_type": "cloud", "tasks": "txt2img"})


@live
class TestLiveFlow:
    def test_a_fresh_backend_has_the_plugin_enabled_a_catalog_and_nothing_enabled(self, app):
        plugins = api_get(app, "/api/plugins")
        assert any(p["id"] == cloud_fake.PLUGIN_ID and p["enabled"] for p in plugins)
        engines = api_get(app, "/api/backends/engines")
        assert cloud_fake.DRIVER in [d["driver"] for d in engines]
        catalog = api_get(app, f"/api/cloud/backends/{app.backend_id}/catalog")
        assert catalog["counts"] == {"total": 2, "enabled": 0, "missing": 0}
        assert sorted(item["slug"] for item in catalog["items"]) == sorted([cloud_fake.FULL_SLUG, cloud_fake.LITE_SLUG])
        assert model_listing(app)["models"] == []

    def test_the_preset_is_listed_for_the_admin(self, app):
        presets = api_get(app, "/api/presets")
        match = [p for p in presets if p["id"] == cloud_fake.PRESET_ID]
        assert match and match[0]["engine"] == "cloud" and match[0]["driver"] == cloud_fake.DRIVER

    def test_enabling_a_model_through_the_api_makes_it_appear_in_the_presets_listing(self, app):
        result = api_send(app, "post", f"/api/cloud/backends/{app.backend_id}/catalog/enable", json={"slugs": [cloud_fake.FULL_SLUG, cloud_fake.LITE_SLUG]})
        assert sorted(result["changed"]) == sorted([cloud_fake.FULL_SLUG, cloud_fake.LITE_SLUG])
        listing = model_listing(app)
        assert sorted(m["filename"] for m in listing["models"]) == sorted([cloud_fake.FULL_SLUG, cloud_fake.LITE_SLUG])
        assert {m["name"] for m in listing["models"]} == {cloud_fake.FULL_LABEL, cloud_fake.LITE_LABEL}

    def test_the_capabilities_differ_between_the_two_models(self, app):
        models = {m["filename"]: m["id"] for m in model_listing(app)["models"]}
        full = api_get(app, f"/api/cloud/models/{models[cloud_fake.FULL_SLUG]}/capabilities")
        lite = api_get(app, f"/api/cloud/models/{models[cloud_fake.LITE_SLUG]}/capabilities")
        assert {p["name"] for p in full["params"]} == {"aspect_ratio", "quality", "background", "x.style"}
        assert {p["name"] for p in lite["params"]} == {"aspect_ratio", "x.style"}
        assert [i["role"] for i in full["inputs"]] == ["reference"] and lite["inputs"] == []

    def test_a_generation_through_the_api_completes_and_lands_in_history_with_its_cost(self, app):
        model_id = next(m["id"] for m in model_listing(app)["models"] if m["filename"] == cloud_fake.FULL_SLUG)

        generation_id = start(app, model_id)
        status = wait_for_status(app, generation_id, {"completed", "failed"})

        assert status["status"] == "completed", status
        history = api_get(app, f"/api/generations/history/{generation_id}")
        generation = history.get("generation", history)
        files = generation.get("files") or []
        assert len(files) >= 1
        admin = api_get(app, f"/api/admin/generations/{generation_id}")
        assert admin["cost"]["amount_usd"] == "0.04" and admin["cost"]["source"] == "provider"
        assert "cost" not in generation and "amount_usd" not in str(generation)

    def test_a_running_generation_can_be_cancelled_mid_run(self, app):
        api_send(app, "put", f"/api/backends/{app.backend_id}", json={"duration_seconds": 120.0})
        model_id = next(m["id"] for m in model_listing(app)["models"] if m["filename"] == cloud_fake.FULL_SLUG)

        generation_id = start(app, model_id)
        wait_for_status(app, generation_id, {"running", "processing", "pending"}, timeout=20)
        time.sleep(1.0)
        started = time.monotonic()
        api_send(app, "post", f"/api/generations/{generation_id}/cancel")
        status = wait_for_status(app, generation_id, {"cancelled"}, timeout=15)

        assert status["status"] == "cancelled"
        assert time.monotonic() - started < 10
        admin = api_get(app, f"/api/admin/generations/{generation_id}")
        assert admin["cost"] is None

    def test_an_unsupported_control_is_stripped_and_a_bad_value_is_a_422(self, app):
        api_send(app, "put", f"/api/backends/{app.backend_id}", json={"duration_seconds": 1.0})
        lite_id = next(m["id"] for m in model_listing(app)["models"] if m["filename"] == cloud_fake.LITE_SLUG)

        bad = app.client.post("/api/generations/start", json={
            "preset_id": cloud_fake.PRESET_ID, "mode": "txt2img",
            "prompts": [{"positive": "a", "negative": ""}],
            "form_data": {"model": f"model:{lite_id}", "count": 1, "aspect_ratio": "21:9"},
        })
        assert bad.status_code == 422 and "aspect_ratio" in str(bad.json())

        generation_id = start(app, lite_id, quality=9, aspect_ratio="4:3")
        assert wait_for_status(app, generation_id, {"completed", "failed"})["status"] == "completed"
