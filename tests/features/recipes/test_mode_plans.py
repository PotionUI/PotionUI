from types import SimpleNamespace

import pytest

from src.features.recipes.mode_plans import ModePlanner, PlanApprovalError
from src.features.recipes.targets import InstallTargets, TargetOption
from src.platform.runtime.gpu_profile import build_gpu_profile
from tests.features.recipes.mode_fixtures import GIB, FakeDbRepo, FakeLoader, FakeModelRepo, video_preset

ADA = build_gpu_profile((8, 9), 24.0, name="RTX 4090")
BLACKWELL = build_gpu_profile((12, 0), 32.0, name="RTX 5090")


class Roots:
    def __init__(self, path):
        self.path = path

    def write_dir(self, model_type):
        return SimpleNamespace(path=self.path, root_id="home")


def _planner(tmp_path, gpu=ADA, repo=None, db=None, free=900 * GIB):
    targets = InstallTargets(
        model_roots=Roots(tmp_path),
        gpu_profile_provider=lambda *args: gpu,
        disk_usage=lambda path: SimpleNamespace(free=free, total=2000 * GIB, used=0),
    )
    return ModePlanner(
        FakeLoader(video_preset()),
        repo or FakeModelRepo(),
        preset_db_repo=db or FakeDbRepo(),
        targets=targets,
        provider_registry_factory=None,
    )


def _assess(planner, **kwargs):
    kwargs.setdefault("selected", ["video"])
    kwargs.setdefault("locked", ["video"])
    return planner.assess("VID", hash_candidates="never", with_estimate=False, **kwargs)


def _slot(payload, slot_id):
    return next(s for s in payload["slots"] if s["id"] == slot_id)


def test_consent_payload_for_a_fresh_ada_card(tmp_path):
    planner = _planner(tmp_path)
    payload = planner.consent_payload(_assess(planner))

    video = next(m for m in payload["modes"] if m["id"] == "video")
    assert (video["selected"], video["locked"], video["ready"]) == (True, True, False)
    assert video["missing_bytes"] == 35 * GIB
    assert payload["total_bytes"] == 35 * GIB
    assert [a["id"] for a in payload["artifacts"]] == ["model", "text_encoder", "video_vae"]
    assert _slot(payload, "model")["recommended_variant_id"] == "dit-int8"
    assert _slot(payload, "model")["best_fit_variant_id"] == "dit-int8"
    assert payload["target"]["kind"] == "local"
    assert payload["target"]["options"][0]["gpu_name"] == "RTX 4090"
    assert payload["disk"][0]["free_bytes"] == 900 * GIB
    assert _slot(payload, "model")["disk_id"] == payload["disk"][0]["id"]
    assert payload["estimate"] is None
    assert payload["kind"] == "modes"


def test_blackwell_prefers_the_nvfp4_file(tmp_path):
    planner = _planner(tmp_path, gpu=BLACKWELL)
    payload = planner.consent_payload(_assess(planner))

    assert _slot(payload, "model")["recommended_variant_id"] == "dit-nvfp4"


def test_an_installed_variant_stays_preselected_and_the_gpu_pick_is_marked(tmp_path):
    repo = FakeModelRepo(files=[("diffusion_model", "dit-int8.safetensors")])
    planner = _planner(tmp_path, gpu=BLACKWELL, repo=repo)
    assessment = _assess(planner)
    slot = _slot(planner.consent_payload(assessment), "model")

    assert slot["recommended_variant_id"] == "dit-int8"
    assert slot["best_fit_variant_id"] == "dit-nvfp4"
    assert [v["id"] for v in slot["variants"] if v["best_fit"]] == ["dit-nvfp4"]
    assert [f["slot_id"] for f in planner.selection_files(assessment, ["video"], [])] == ["text_encoder", "video_vae"]
    switched = planner.selection_files(assessment, ["video"], [], {"model": "dit-nvfp4"})
    assert switched[0]["filename"] == "dit-nvfp4.safetensors"


def test_shared_slots_count_once_across_modes(tmp_path):
    planner = _planner(tmp_path)
    assessment = _assess(planner)
    files = planner.selection_files(assessment, ["video", "cinemagraph", "add_water"], [])

    assert [f["slot_id"] for f in files] == ["model", "text_encoder", "video_vae", "effect_lora@cinemagraph", "effect_lora@add_water"]


def test_approve_validates_modes_extras_and_selections(tmp_path):
    planner = _planner(tmp_path)
    assessment = _assess(planner)

    with pytest.raises(PlanApprovalError, match="not one of the modes"):
        planner.approve(assessment, ["video", "nope"])
    with pytest.raises(PlanApprovalError, match="Video is always included"):
        planner.approve(assessment, ["cinemagraph"])
    with pytest.raises(PlanApprovalError, match="not an extra file"):
        planner.approve(assessment, ["video"], ["control_model"])
    with pytest.raises(PlanApprovalError, match="not a file offered"):
        planner.approve(assessment, ["video"], [], {"model": "ghost"})

    plan = planner.approve(assessment, ["video"], ["audio_vae"], {"model": "dit-bf16"})
    assert plan["modes"] == ["video"]
    assert [f["slot_id"] for f in plan["files"]] == ["model", "text_encoder", "video_vae", "audio_vae"]
    assert plan["files"][0]["filename"] == "dit-bf16.safetensors"
    assert plan["chosen"]["model"]["fields"] == {"video": "model"}
    assert plan["destination_backend_id"] is None
    assert plan["bytes"] == (40 + 14 + 1) * GIB + 300 * 1024 ** 2


def test_disk_shortfall_blocks_the_approval_with_the_arithmetic(tmp_path):
    planner = _planner(tmp_path, free=10 * GIB)
    with pytest.raises(PlanApprovalError, match="Not enough space on models folder") as excinfo:
        planner.approve(_assess(planner))
    assert "Deselect about" in str(excinfo.value)


def test_any_of_group_downloads_its_first_member_until_one_is_present(tmp_path):
    repo = FakeModelRepo(files=[("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors")])
    planner = _planner(tmp_path, repo=repo)
    assessment = _assess(planner)

    assert planner.mode_ready(assessment, "restore") is False
    assert [f["slot_id"] for f in planner.selection_files(assessment, ["restore"], [])] == ["restore_lora"]
    assert [f["slot_id"] for f in planner.selection_files(assessment, ["restore"], ["deblur_lora"])] == ["deblur_lora"]
    repo.add("lora", "deblur.safetensors")
    assert planner.mode_ready(_assess(planner), "restore") is True


def test_manual_slots_never_block_readiness_or_download(tmp_path):
    repo = FakeModelRepo(
        files=[("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors"), ("model_patch", "ctrl-int8.safetensors")]
    )
    planner = _planner(tmp_path, repo=repo)
    assessment = _assess(planner)

    assert planner.mode_ready(assessment, "motion") is True
    assert _slot(planner.consent_payload(assessment), "own_upscaler")["manual"] is True
    assert planner.selection_files(assessment, ["motion"], []) == []


def test_admin_default_on_an_available_file_satisfies_the_slot(tmp_path):
    repo = FakeModelRepo(files=[("lora", "my-own.safetensors")])
    db = FakeDbRepo(overrides={"VID": {"cinemagraph": {"effect_lora": {"default": "my-own.safetensors"}}}})
    planner = _planner(tmp_path, repo=repo, db=db)
    assessment = _assess(planner)

    assert assessment.slots["effect_lora@cinemagraph"].satisfied_by == "admin_default"
    assert "effect_lora@cinemagraph" not in [f["slot_id"] for f in planner.selection_files(assessment, ["cinemagraph"], [])]


def test_admin_default_naming_a_recommendation_preselects_it_and_hidden_restricts_choices(tmp_path):
    db = FakeDbRepo(overrides={"VID": {"video": {"model": {"default": "models/diffusion_models/dit-bf16.safetensors", "visible": False}}}})
    planner = _planner(tmp_path, db=db)
    slot = _slot(planner.consent_payload(_assess(planner)), "model")

    assert slot["recommended_variant_id"] == "dit-bf16"
    assert [v["id"] for v in slot["variants"]] == ["dit-bf16"]


def test_extras_skip_hides_extra_slots(tmp_path):
    planner = _planner(tmp_path)
    payload = planner.consent_payload(_assess(planner, extras="skip"))

    assert "audio_vae" not in [s["id"] for s in payload["slots"]]
    assert next(m for m in payload["modes"] if m["id"] == "video")["extra_slot_ids"] == []


def test_summary_counts_ready_modes_and_bytes(tmp_path):
    repo = FakeModelRepo()
    for model_type, filename in [("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors")]:
        repo.add(model_type, filename, size=1)
    repo.add("lora", "cine.safetensors")
    planner = _planner(tmp_path, repo=repo)

    summary = planner.summary("VID")
    modes = {m["id"]: m for m in summary["modes"]}
    assert (summary["modes_total"], summary["modes_ready"]) == (5, 2)
    assert modes["cinemagraph"]["removable"] is True
    assert modes["cinemagraph"]["own_bytes"] == 200 * 1024 ** 2
    assert modes["video"]["removable"] is False
    assert modes["video"]["shares_with"] == ["cinemagraph", "add_water", "restore", "motion"]
    assert modes["add_water"]["missing_bytes"] == 900 * 1024 ** 2
    assert summary["installed_bytes"] == (20 + 14 + 1) * GIB + 200 * 1024 ** 2
    assert planner.summary("missing") is None


def test_unknown_target_is_reported(tmp_path):
    planner = _planner(tmp_path)
    with pytest.raises(PlanApprovalError, match="not a place"):
        _assess(planner, target="worker-9")


def test_a_target_that_cannot_download_turns_every_slot_manual(tmp_path):
    planner = _planner(tmp_path)
    planner.targets.options = lambda engine: [TargetOption(backend_id="comfy", label="My ComfyUI", kind="external")]
    assessment = _assess(planner)
    payload = planner.consent_payload(assessment)

    assert all(slot["manual"] for slot in payload["slots"])
    assert _slot(payload, "model")["reason"] == "Add this file to My ComfyUI's models folder"
    assert payload["total_bytes"] == 0
    assert planner.approve(assessment)["files"] == []
