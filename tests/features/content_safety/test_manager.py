import pytest
from PIL import Image

from src.features.content_safety.errors import ContentCheckUnavailable
from src.pipelines.outputs import ImageGenerationOutput
from tests.features.content_safety.fakes import FakeLedger, build


def final_image():
    return ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False)


def preview_image():
    return ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=True)


async def test_allowed_user_skips_the_tagger_entirely():
    manager, tagger, _ = build("allowed", [0.99])
    output = final_image()

    outcome = await manager.gate_output("g1", "u1", output)

    assert outcome.output is output
    assert tagger.calls == []
    assert not hasattr(output, "_content_nsfw")


async def test_blocked_flagged_final_is_dropped_and_logged():
    ledger = FakeLedger()
    manager, tagger, _ = build("blocked", [0.9], ledger=ledger)

    outcome = await manager.gate_output("g1", "u1", final_image())

    assert outcome.output is None
    assert outcome.blocked == 1
    assert [kind for kind, _ in ledger.events] == ["output_blocked"]


async def test_blocked_safe_final_is_rated_and_delivered():
    manager, tagger, _ = build("blocked", [0.1])
    output = final_image()

    outcome = await manager.gate_output("g1", "u1", output)

    assert outcome.output is output
    assert tagger.calls == [1]


async def test_tagger_failure_on_a_blocked_final_is_unavailable():
    ledger = FakeLedger()
    manager, _, _ = build("blocked", error=RuntimeError("cuda oom"), ledger=ledger)

    outcome = await manager.gate_output("g1", "u1", final_image())

    assert outcome.unavailable is True
    assert [kind for kind, _ in ledger.events] == ["check_unavailable"]


async def test_previews_are_rated_before_they_are_shown_and_throttled_while_busy():
    manager, tagger, clock = build("blocked", [0.1, 0.1, 0.1])
    clock.now = 100.0

    first = await manager.gate_output("g1", "u1", preview_image())
    second = await manager.gate_output("g1", "u1", preview_image())

    assert first.output is not None
    assert second.output is None
    assert tagger.calls == [1]

    clock.now += 10.0
    third = await manager.gate_output("g1", "u1", preview_image())

    assert third.output is not None
    assert tagger.calls == [1, 1]


async def test_final_outputs_are_never_throttled():
    manager, tagger, _ = build("blocked", [0.1, 0.1])

    await manager.gate_output("g1", "u1", preview_image())
    outcome = await manager.gate_output("g1", "u1", final_image())

    assert outcome.output is not None
    assert tagger.calls == [1, 1]


async def test_preview_throttle_is_cleared_when_the_generation_finishes():
    manager, tagger, _ = build("blocked", [0.1, 0.1])

    await manager.gate_output("g1", "u1", preview_image())
    manager.finish_generation("g1")
    await manager.gate_output("g1", "u1", preview_image())

    assert tagger.calls == [1, 1]


async def test_saved_finals_are_written_to_the_ledger_by_stored_path():
    ledger = FakeLedger()
    manager, _, _ = build("blur", [0.9], ledger=ledger)
    output = final_image()
    outcome = await manager.gate_output("g1", "u1", output)
    output._saved_path = "generations/2026-01-01/g1/0.png"

    manager.record_saved(outcome)

    assert ledger.scores == {"generations/2026-01-01/g1/0.png": pytest.approx(0.9)}


def test_preflight_refuses_a_blocked_policy_without_the_tagger():
    manager, _, _ = build("blocked", present=False)

    with pytest.raises(ContentCheckUnavailable):
        manager.preflight("u1")


def test_preflight_lets_blur_and_allowed_run_without_the_tagger():
    for mode in ("blur", "allowed"):
        manager, _, _ = build(mode, present=False)

        assert manager.preflight("u1").mode == mode


def test_banned_prompt_is_found_in_any_expanded_pair_and_logged_by_index():
    ledger = FakeLedger()
    manager, _, _ = build(words=["forbidden"], ledger=ledger)
    prompts = [{"positive": "fine"}, {"positive": "a Forbidden thing"}]

    assert manager.find_banned_prompt("u1", "g1", prompts, {}) == 0
    assert ledger.events == [("banned_prompt", {"user_id": "u1", "generation_id": "g1", "detail": {"entry": 0}})]


def test_clean_prompt_passes_and_logs_nothing():
    ledger = FakeLedger()
    manager, _, _ = build(words=["forbidden"], ledger=ledger)

    assert manager.find_banned_prompt("u1", "g1", [{"positive": "fine"}], {}) is None
    assert ledger.events == []


def test_matcher_follows_the_setting_and_keeps_the_last_good_list_when_it_turns_invalid():
    manager, _, _ = build(words=["one"])
    assert manager.find_banned_prompt("u", "g", [{"positive": "one"}], {}) == 0

    manager.settings.values["content_banned_words"] = ["two"]
    assert manager.find_banned_prompt("u", "g", [{"positive": "one"}], {}) is None
    assert manager.find_banned_prompt("u", "g", [{"positive": "two"}], {}) == 0

    manager.settings.values["content_banned_words"] = "not a list"
    assert manager.find_banned_prompt("u", "g", [{"positive": "two"}], {}) == 0


def test_only_restricted_viewers_are_limited_to_fully_safe_generations():
    ledger = FakeLedger(generation_states={
        "safe": ["safe", "safe"], "mixed": ["safe", "flagged"], "unrated": ["unrated"], "empty": [],
    })
    restricted, _, _ = build(restricted=True, ledger=ledger)
    open_user, _, _ = build(restricted=False, ledger=ledger)
    ids = ["safe", "mixed", "unrated", "empty", "unknown"]

    assert restricted.viewable_generation_ids("u1", ids) == {"safe"}
    assert open_user.viewable_generation_ids("u1", ids) == set(ids)


def image_file(tmp_path, name):
    path = tmp_path / name
    Image.new("RGB", (4, 4)).save(path)
    return path


class FakeFileStore:
    def __init__(self, root):
        self.root = root

    def get_full_path(self, relative):
        return str(self.root / relative)


async def test_backfill_rates_every_unrated_file_and_reports_progress(tmp_path):
    ledger = FakeLedger()
    ledger.pending_files = [
        {"id": "1", "file_path": "a.png", "file_type": "IMAGE", "thumbnail_medium": None},
        {"id": "2", "file_path": "b.png", "file_type": "IMAGE", "thumbnail_medium": None},
    ]
    image_file(tmp_path, "a.png")
    image_file(tmp_path, "b.png")
    manager, _, _ = build("blocked", [0.9, 0.1], ledger=ledger)
    manager.file_service = FakeFileStore(tmp_path)

    assert manager.start_backfill() is True
    await manager._backfill_task

    assert ledger.scores == {"a.png": pytest.approx(0.9), "b.png": pytest.approx(0.1)}
    assert manager.status()["backfill"] == {"total": 2, "rated": 2, "running": False}


async def test_backfill_does_not_start_without_the_tagger():
    manager, _, _ = build("blocked", present=False)

    assert manager.start_backfill() is False


async def test_backfill_skips_unreadable_files_and_keeps_going(tmp_path):
    ledger = FakeLedger()
    ledger.pending_files = [
        {"id": "1", "file_path": "missing.png", "file_type": "IMAGE", "thumbnail_medium": None},
        {"id": "2", "file_path": "b.png", "file_type": "IMAGE", "thumbnail_medium": None},
    ]
    image_file(tmp_path, "b.png")
    manager, _, _ = build("blocked", [0.1], ledger=ledger)
    manager.file_service = FakeFileStore(tmp_path)

    manager.start_backfill()
    await manager._backfill_task

    assert list(ledger.scores) == ["b.png"]


def test_status_reports_the_policy_the_tagger_and_the_backfill():
    manager, tagger, _ = build("blur", present=False)

    status = manager.status()

    assert status["policy"] == "blur"
    assert status["tagger"] == {"present": False, "device": "cpu", "downloading": False}
    assert status["backfill"]["running"] is False


def test_an_overlong_prompt_is_refused_rather_than_partly_scanned():
    from src.features.content_safety.banned_words import MAX_SCAN_CHARS

    manager, _, _ = build(words=["forbidden"])

    assert manager.find_banned_prompt("u", "g", [{"positive": "x" * (MAX_SCAN_CHARS + 1)}], {}) == -1


def test_filter_paths_keeps_only_safe_paths_for_a_restricted_viewer():
    ledger = FakeLedger()
    ledger.states = lambda paths: {p: {"ok.png": "safe", "bad.png": "flagged"}.get(p, "unrated") for p in paths}
    restricted, _, _ = build(restricted=True, ledger=ledger)
    open_user, _, _ = build(restricted=False, ledger=ledger)
    paths = ["ok.png", "bad.png", "new.png", ""]

    assert restricted.filter_paths("u", paths) == {"ok.png"}
    assert open_user.filter_paths("u", paths) == {"ok.png", "bad.png", "new.png"}


async def test_saved_artifacts_are_rated_dropped_when_blocked_and_recorded_when_kept():
    ledger = FakeLedger()
    manager, tagger, _ = build("blocked", [0.9, 0.1], ledger=ledger)
    flagged = ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False, isArtifact=True)
    safe = ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False, isArtifact=True)

    dropped = await manager.gate_output("g1", "u1", flagged)
    kept = await manager.gate_output("g1", "u1", safe)
    safe._saved_path = "generations/g/art.png"
    manager.record_saved(kept)

    assert dropped.output is None
    assert dropped.blocked == 0
    assert kept.output is safe
    assert ledger.scores == {"generations/g/art.png": pytest.approx(0.1)}


async def test_saved_artifact_is_flagged_not_dropped_under_blur():
    manager, _, _ = build("blur", [0.9])
    artifact = ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False, isArtifact=True)

    outcome = await manager.gate_output("g1", "u1", artifact)

    assert outcome.output is artifact
    assert artifact._content_flagged is True
