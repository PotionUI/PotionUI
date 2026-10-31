from types import SimpleNamespace

import pytest

from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.modes_plan import ModesPlanExecutor
from src.features.recipes.executors.preset_defaults import PresetDefaultsExecutor
from src.features.recipes.mode_plans import ModePlanner
from src.features.recipes.mode_slots import PlanApprovalError
from src.features.recipes.records import MODE_ADMIN, MODE_ONBOARDING, GrantRequest, LintContext, RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.features.recipes.targets import InstallTargets
from src.platform.runtime.gpu_profile import build_gpu_profile
from tests.features.recipes.mode_fixtures import GIB, FakeDbRepo, FakeLoader, FakeModelRepo, video_preset

ADA = build_gpu_profile((8, 9), 24.0, name="RTX 4090")
BASE = [("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors")]


def _planner(tmp_path, repo, db=None):
    targets = InstallTargets(
        model_roots=SimpleNamespace(write_dir=lambda t: SimpleNamespace(path=tmp_path, root_id="home")),
        gpu_profile_provider=lambda *a: ADA,
        disk_usage=lambda p: SimpleNamespace(free=500 * GIB, total=1000 * GIB, used=0),
    )
    return ModePlanner(FakeLoader(video_preset()), repo, preset_db_repo=db or FakeDbRepo(), targets=targets)


def _context(params, mode=MODE_ADMIN, safe_input=None, step_outputs=None):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING, mode=mode, safe_input=safe_input, created_by="admin")
    step = RecipeStep(key="modes.plan", kind="modes.plan", title="Plan", params=params)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    return StepContext(run=run, recipe=recipe, step=step, step_outputs=step_outputs or {})


STARTER = {"preset_id": "VID", "modes": "all", "selected": ["video"], "locked": ["video"]}
DERIVED = {"preset_id": "VID", "modes": "all", "selected": []}


def test_parks_for_consent_when_the_base_mode_needs_files(tmp_path):
    result = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo())).execute(_context(STARTER, mode=MODE_ONBOARDING))

    assert result.awaiting_consent is True
    assert result.consent_request["total_bytes"] == 35 * GIB


def test_onboarding_skips_consent_when_the_base_mode_is_ready(tmp_path):
    result = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo(files=BASE))).execute(_context(STARTER, mode=MODE_ONBOARDING))

    assert result.success is True
    assert result.safe_output["plan"]["modes"] == ["video"]
    assert result.safe_output["plan"]["files"] == []
    assert result.safe_output["plan"]["chosen"]["model"]["installed"] is True


def test_admin_runs_still_offer_the_modes_that_are_not_ready(tmp_path):
    executor = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo(files=BASE)))

    assert executor.execute(_context(STARTER)).awaiting_consent is True


def test_preselected_modes_come_from_the_run_input(tmp_path):
    executor = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo(files=BASE)))
    result = executor.execute(_context(DERIVED, safe_input={"preselect_modes": ["cinemagraph"]}))

    selected = [m["id"] for m in result.consent_request["modes"] if m["selected"]]
    assert selected == ["cinemagraph"]
    assert result.consent_request["total_bytes"] == 200 * 1024 ** 2


def test_grant_turns_the_approval_into_a_plan(tmp_path):
    executor = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo(files=BASE)))
    context = _context(DERIVED, safe_input={"preselect_modes": ["cinemagraph"]})
    consent = executor.execute(context).consent_request

    defaulted = executor.grant(context, consent, GrantRequest())
    assert defaulted["plan"]["modes"] == ["cinemagraph"]
    picked = executor.grant(context, consent, GrantRequest(modes=["add_water", "restore"], extras=["deblur_lora"]))
    assert [f["slot_id"] for f in picked["plan"]["files"]] == ["effect_lora@add_water", "deblur_lora"]
    with pytest.raises(PlanApprovalError):
        executor.grant(context, consent, GrantRequest(target="elsewhere"))


def test_unknown_preset_fails_plainly(tmp_path):
    result = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo())).execute(_context({"preset_id": "NOPE"}))

    assert result.success is False
    assert "isn't available" in result.safe_error_detail


def test_validate_params_checks_modes_and_extras(tmp_path):
    executor = ModesPlanExecutor(_planner(tmp_path, FakeModelRepo()))
    lint = LintContext(preset_index={"VID": video_preset()})

    assert executor.validate_params(dict(STARTER, preselect_extras=["audio_vae"]), None, lint) == []
    issues = executor.validate_params({"preset_id": "VID", "selected": ["ghost"], "preselect_extras": ["model"]}, None, lint)
    assert any("'ghost'" in i for i in issues)
    assert any("'model'" in i for i in issues)
    assert executor.validate_params({"preset_id": "NOPE"}, None, lint) == ["modes.plan: preset 'NOPE' does not exist"]


def test_preview_returns_the_consent_payload_without_side_effects(tmp_path):
    repo = FakeModelRepo()
    preview = ModesPlanExecutor(_planner(tmp_path, repo)).preview(_context(STARTER))

    assert preview["summary"] == "5 modes, 0 ready"
    assert preview["consent_request"]["kind"] == "modes"


def _defaults_context(plan):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    step = RecipeStep(key="preset.defaults", kind="preset.defaults", title="Defaults", params={"plan": "modes.plan"})
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    return StepContext(run=run, recipe=recipe, step=step, step_outputs={"modes.plan": {"plan": plan}})


def test_preset_defaults_fill_only_unset_or_broken_defaults(tmp_path):
    repo = FakeModelRepo(files=BASE + [("lora", "cine.safetensors")])
    db = FakeDbRepo(overrides={"VID": {
        "video": {"text_encoder": {"default": "te-int8.safetensors", "editable": False}, "video_vae": {"default": "gone.safetensors", "visible": False}},
    }})
    planner = _planner(tmp_path, repo, db)
    plan = planner.approve(planner.assess("VID", selected=["video", "cinemagraph"], hash_candidates="never", with_estimate=False))

    result = PresetDefaultsExecutor(FakeLoader(video_preset()), db, planner.model_available).execute(_defaults_context(plan))

    stored = db.overrides["VID"]
    assert stored["video"]["text_encoder"] == {"default": "te-int8.safetensors", "editable": False}
    assert stored["video"]["video_vae"] == {"default": "vae.safetensors", "visible": False}
    assert stored["video"]["model"] == {"default": "dit-int8.safetensors"}
    assert stored["cinemagraph"]["effect_lora"] == {"default": "cine.safetensors"}
    assert {"mode": "video", "field": "text_encoder", "value": "te-int8.safetensors"} in result.safe_output["kept"]
    assert len(result.safe_output["set"]) == 6


def test_preset_defaults_need_an_installed_preset_and_a_plan(tmp_path):
    db = FakeDbRepo(installed=())
    executor = PresetDefaultsExecutor(FakeLoader(video_preset()), db, lambda v, t: True)

    assert executor.execute(_defaults_context({"preset_id": "VID", "modes": [], "chosen": {}})).error_code == "PRESET_NOT_INSTALLED"
    empty = _defaults_context({})
    empty.step_outputs.clear()
    assert executor.execute(empty).error_code == "PRESET_DEFAULTS_NO_PLAN"
