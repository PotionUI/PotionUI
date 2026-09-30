import pytest

from src.features.models.type_resolution import Copy, Resolution, resolve_type


def verdict(status, model_type=None):
    return {"status": status, "model_type": model_type}


def assertion(model_type, source="admin"):
    return {"model_type": model_type, "source": source}


SCANNED_CHECKPOINT = Copy("checkpoint", True)
SCANNED_UNET = Copy("unet", True)
PLAIN_CHECKPOINT = Copy("checkpoint", False)
PLAIN_DIFFUSION = Copy("diffusion_model", False)
PLAIN_LORA = Copy("lora", False)


@pytest.mark.parametrize("source", ["admin", "recipe", "download"])
def test_an_assertion_wins_over_everything(source):
    result = resolve_type(
        assertion("vae", source), [SCANNED_CHECKPOINT, PLAIN_LORA], verdict("decided", "diffusion_model")
    )
    assert result == Resolution("vae", source)


def test_no_copies_and_no_assertion_changes_nothing():
    assert resolve_type(None, [], verdict("decided", "checkpoint")) is None


def test_a_decided_verdict_of_a_scanned_copy_is_used():
    result = resolve_type(None, [SCANNED_CHECKPOINT], verdict("decided", "diffusion_model"))
    assert result == Resolution("diffusion_model", "header")


def test_a_scanned_copy_beats_an_unscanned_copy():
    result = resolve_type(None, [SCANNED_CHECKPOINT, PLAIN_LORA], verdict("decided", "diffusion_model"))
    assert result == Resolution("diffusion_model", "header")


def test_unscanned_copies_that_agree_keep_the_folder_type():
    result = resolve_type(None, [PLAIN_CHECKPOINT, Copy("checkpoint", False)], verdict("decided", "diffusion_model"))
    assert result == Resolution("checkpoint", "folder")


def test_unscanned_copies_that_disagree_use_a_decided_verdict():
    result = resolve_type(None, [PLAIN_CHECKPOINT, PLAIN_DIFFUSION], verdict("decided", "diffusion_model"))
    assert result == Resolution("diffusion_model", "header")


@pytest.mark.parametrize("v", [None, verdict("undecided"), verdict("invalid")])
def test_unscanned_copies_that_disagree_fall_back_to_the_first_type_in_model_types_order(v):
    result = resolve_type(None, [PLAIN_DIFFUSION, PLAIN_CHECKPOINT], v)
    assert result == Resolution("checkpoint", "folder")


def test_every_copy_scanned_and_undecided_is_undefined():
    result = resolve_type(None, [SCANNED_CHECKPOINT, SCANNED_UNET], verdict("undecided"))
    assert result == Resolution("undefined", "header")


@pytest.mark.parametrize("v", [None, verdict("invalid")])
def test_every_copy_scanned_without_a_usable_verdict_keeps_the_folder_type(v):
    result = resolve_type(None, [SCANNED_CHECKPOINT], v)
    assert result == Resolution("checkpoint", "folder")


def test_an_undecided_verdict_never_overrides_an_unscanned_copy():
    result = resolve_type(None, [SCANNED_CHECKPOINT, PLAIN_LORA], verdict("undecided"))
    assert result == Resolution("lora", "folder")


def test_a_decided_verdict_without_a_type_is_ignored():
    result = resolve_type(None, [SCANNED_CHECKPOINT], verdict("decided", None))
    assert result == Resolution("checkpoint", "folder")
