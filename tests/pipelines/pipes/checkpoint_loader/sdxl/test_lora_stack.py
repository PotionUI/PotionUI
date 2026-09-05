# This container pairs numpy 1.26 with an accelerate that reads
# numpy._core.multiarray, so importing diffusers dies unless that submodule is
# wired up first, and numpy 1.26's _core shim only wires it once numpy itself
# has been imported. Both lines, in this order, make this file collectible on
# its own; a sibling that imports diffusers first still poisons the process, so
# the durable home for this is tests/conftest.py.
import numpy  # noqa: F401
import numpy._core.multiarray  # noqa: F401

"""Tests for SDXLModel's LoRA adapter stack: identity, de-duplication and
all-or-nothing application.

Covers the failure mode where a LoRA that failed to load (or a stack that
failed to activate) was logged and skipped, so generation ran with a silently
partial stack, and the mode where two LoRAs collapsed onto one adapter name
because the name came from the file basename alone.
"""

from types import SimpleNamespace

import pytest
import torch

from src.pipelines.pipes.checkpoint_loader.sdxl.main import CheckpointLoaderSDXLPipe
from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import (
    SDXLLoraStackError,
    SDXLModel,
    _lora_adapter_identity,
)


class FakePipe:
    """A pipeline that records the diffusers LoRA calls and can fail on demand.

    Adapters are tracked per component so an unwind can be checked to clear the
    text encoders as well as the UNet.
    """

    def __init__(self, fail_load_for=(), fail_set_adapters=False, delete_adapters_raises=False):
        self.fail_load_for = set(fail_load_for)
        self.fail_set_adapters = fail_set_adapters
        self.delete_adapters_raises = delete_adapters_raises

        self.load_calls = []
        self.set_adapters_calls = []
        self.delete_adapters_calls = []
        self.unload_calls = 0

        self.unet_adapters = {}
        self.text_encoder_adapters = {}
        self.active = None

        self.vae = SimpleNamespace(config=SimpleNamespace(scaling_factor=0.13025, sample_size=1024))
        self.vae.to = lambda **kwargs: self.vae
        self.scheduler = SimpleNamespace(
            config=SimpleNamespace(prediction_type="epsilon", beta_end=0.012)
        )

    # --- diffusers surface -------------------------------------------------
    def to(self, device):
        self.device = device
        return self

    def enable_freeu(self, **kwargs):
        pass

    def load_lora_weights(self, lora_dir, adapter_name=None, weight_name=None, local_files_only=None):
        self.load_calls.append(
            {
                "dir": lora_dir,
                "adapter_name": adapter_name,
                "weight_name": weight_name,
                "local_files_only": local_files_only,
            }
        )
        if weight_name in self.fail_load_for or adapter_name in self.fail_load_for:
            raise RuntimeError(f"boom loading {weight_name}")
        if adapter_name in self.unet_adapters:
            raise ValueError(f"adapter {adapter_name} already loaded")
        self.unet_adapters[adapter_name] = lora_dir
        self.text_encoder_adapters[adapter_name] = lora_dir

    def set_adapters(self, adapter_names=None, adapter_weights=None):
        self.set_adapters_calls.append((list(adapter_names), list(adapter_weights)))
        if self.fail_set_adapters:
            raise RuntimeError("boom activating")
        self.active = (list(adapter_names), list(adapter_weights))

    def delete_adapters(self, adapter_names):
        self.delete_adapters_calls.append(list(adapter_names))
        if self.delete_adapters_raises:
            raise RuntimeError("no per-adapter delete here")
        for name in adapter_names:
            self.unet_adapters.pop(name, None)
            self.text_encoder_adapters.pop(name, None)

    def unload_lora_weights(self):
        self.unload_calls += 1
        self.unet_adapters.clear()
        self.text_encoder_adapters.clear()
        self.active = None


def make_model(loras, path="/models/checkpoints/test.safetensors"):
    return SDXLModel(
        template={"base": "SDXL", "name": "test", "file_path": path},
        config={
            "path": path,
            "device": "cpu",
            "dtype": "float16",
            "nsfw": False,
            "loras": loras,
            "extras": {},
        },
    )


def write_lora(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(b"not really safetensors")
    return target


# --- adapter identity ------------------------------------------------------


class TestAdapterIdentity:
    def test_same_basename_in_different_directories_gets_distinct_adapters(self, tmp_path):
        a = write_lora(tmp_path / "packA", "style.safetensors")
        b = write_lora(tmp_path / "packB", "style.safetensors")

        model = make_model([{"file_path": a, "weight": 0.8}, {"file_path": b, "weight": 0.4}])
        pipe = FakePipe()
        model._load_loras(pipe)

        names = [call["adapter_name"] for call in pipe.load_calls]
        assert len(names) == 2
        assert names[0] != names[1]
        assert set(pipe.unet_adapters) == set(names)

    def test_names_that_sanitise_to_the_same_token_stay_distinct(self, tmp_path):
        dashed = write_lora(tmp_path / "loras", "my-lora.safetensors")
        scored = write_lora(tmp_path / "loras", "my_lora.safetensors")

        model = make_model(
            [{"file_path": dashed, "weight": 1.0}, {"file_path": scored, "weight": 0.5}]
        )
        pipe = FakePipe()
        model._load_loras(pipe)

        names = [call["adapter_name"] for call in pipe.load_calls]
        assert names[0] != names[1]
        assert pipe.active == (names, [1.0, 0.5])

    def test_identity_is_stable_across_loads(self, tmp_path):
        lora = write_lora(tmp_path / "loras", "style.safetensors")

        first = FakePipe()
        make_model([{"file_path": lora, "weight": 0.8}])._load_loras(first)
        second = FakePipe()
        make_model([{"file_path": lora, "weight": 0.2}])._load_loras(second)

        assert first.load_calls[0]["adapter_name"] == second.load_calls[0]["adapter_name"]

    def test_a_changed_stack_changes_the_adapter_set(self, tmp_path):
        a = write_lora(tmp_path / "loras", "a.safetensors")
        b = write_lora(tmp_path / "loras", "b.safetensors")

        one = FakePipe()
        make_model([{"file_path": a, "weight": 0.8}])._load_loras(one)
        two = FakePipe()
        make_model([{"file_path": a, "weight": 0.8}, {"file_path": b, "weight": 0.3}])._load_loras(two)

        assert set(one.unet_adapters) < set(two.unet_adapters)

    def test_adapter_names_are_module_key_safe(self, tmp_path):
        lora = write_lora(tmp_path / "loras", "v1.5 anime-style.safetensors")

        pipe = FakePipe()
        make_model([{"file_path": lora, "weight": 1.0}])._load_loras(pipe)

        name = pipe.load_calls[0]["adapter_name"]
        assert "." not in name
        assert " " not in name
        assert "-" not in name

    def test_identity_survives_an_unnameable_weight_file(self):
        assert _lora_adapter_identity("/models/loras", "...").startswith("lora_")


# --- stack resolution ------------------------------------------------------


class TestStackResolution:
    def test_repeated_reference_collapses_to_one_adapter_last_weight_wins(self, tmp_path):
        a = write_lora(tmp_path / "loras", "a.safetensors")
        b = write_lora(tmp_path / "loras", "b.safetensors")

        model = make_model(
            [
                {"file_path": a, "weight": 0.8},
                {"file_path": b, "weight": 0.5},
                {"file_path": a, "weight": 0.2},
            ]
        )
        pipe = FakePipe()
        model._load_loras(pipe)

        assert len(pipe.load_calls) == 2
        names, weights = pipe.active
        # `a` keeps its first position; its weight is the later reference's.
        assert names[0] == pipe.load_calls[0]["adapter_name"]
        assert weights == [0.2, 0.5]

    def test_zero_empty_and_none_weights_are_filtered(self, tmp_path):
        kept = write_lora(tmp_path / "loras", "kept.safetensors")
        model = make_model(
            [
                {"file_path": write_lora(tmp_path / "loras", "zero.safetensors"), "weight": 0},
                {"file_path": write_lora(tmp_path / "loras", "blank.safetensors"), "weight": ""},
                {"file_path": write_lora(tmp_path / "loras", "none.safetensors"), "weight": None},
                {"file_path": kept, "weight": 0.6},
            ]
        )
        pipe = FakePipe()
        model._load_loras(pipe)

        assert [call["weight_name"] for call in pipe.load_calls] == ["kept.safetensors"]

    def test_an_all_zero_stack_touches_nothing(self, tmp_path):
        model = make_model(
            [{"file_path": write_lora(tmp_path / "loras", "zero.safetensors"), "weight": 0}]
        )
        pipe = FakePipe()
        model._load_loras(pipe)

        assert pipe.load_calls == []
        assert pipe.set_adapters_calls == []
        assert model.applied_lora_stack is None

    def test_file_loras_load_from_their_directory_with_local_files_only(self, tmp_path):
        lora = write_lora(tmp_path / "loras", "style.safetensors")
        pipe = FakePipe()
        make_model([{"file_path": lora, "weight": 0.7}])._load_loras(pipe)

        call = pipe.load_calls[0]
        assert call["dir"] == str(tmp_path / "loras")
        assert call["weight_name"] == "style.safetensors"
        assert call["local_files_only"] is True

    def test_directory_loras_load_lora_safetensors_from_the_directory(self, tmp_path):
        directory = tmp_path / "dir_lora"
        directory.mkdir()
        pipe = FakePipe()
        make_model([{"file_path": directory, "weight": 0.7}])._load_loras(pipe)

        call = pipe.load_calls[0]
        assert call["dir"] == str(directory)
        assert call["weight_name"] == "lora.safetensors"


# --- all-or-nothing application -------------------------------------------


class TestAtomicity:
    def test_a_failed_load_unwinds_the_earlier_adapters_and_raises(self, tmp_path):
        good = write_lora(tmp_path / "loras", "good.safetensors")
        bad = write_lora(tmp_path / "loras", "bad.safetensors")

        model = make_model([{"file_path": good, "weight": 0.8}, {"file_path": bad, "weight": 0.4}])
        pipe = FakePipe(fail_load_for={"bad.safetensors"})

        with pytest.raises(SDXLLoraStackError) as excinfo:
            model._load_loras(pipe)

        assert "bad.safetensors" in str(excinfo.value)
        assert pipe.unet_adapters == {}
        assert pipe.text_encoder_adapters == {}
        assert pipe.set_adapters_calls == []
        assert model.applied_lora_stack is None

    def test_a_failed_activation_unwinds_every_loaded_adapter_and_raises(self, tmp_path):
        a = write_lora(tmp_path / "loras", "a.safetensors")
        b = write_lora(tmp_path / "loras", "b.safetensors")

        model = make_model([{"file_path": a, "weight": 0.8}, {"file_path": b, "weight": 0.4}])
        pipe = FakePipe(fail_set_adapters=True)

        with pytest.raises(SDXLLoraStackError):
            model._load_loras(pipe)

        assert len(pipe.load_calls) == 2
        assert pipe.unet_adapters == {}
        assert pipe.text_encoder_adapters == {}
        assert model.applied_lora_stack is None

    def test_unwind_falls_back_to_unload_when_delete_adapters_fails(self, tmp_path):
        good = write_lora(tmp_path / "loras", "good.safetensors")
        bad = write_lora(tmp_path / "loras", "bad.safetensors")

        model = make_model([{"file_path": good, "weight": 0.8}, {"file_path": bad, "weight": 0.4}])
        pipe = FakePipe(fail_load_for={"bad.safetensors"}, delete_adapters_raises=True)

        with pytest.raises(SDXLLoraStackError):
            model._load_loras(pipe)

        assert pipe.unload_calls == 1
        assert pipe.unet_adapters == {}
        assert pipe.text_encoder_adapters == {}

    def test_a_retry_after_a_failure_succeeds_on_a_fresh_pipe(self, tmp_path):
        good = write_lora(tmp_path / "loras", "good.safetensors")
        flaky = write_lora(tmp_path / "loras", "flaky.safetensors")
        loras = [{"file_path": good, "weight": 0.8}, {"file_path": flaky, "weight": 0.4}]

        model = make_model(loras)
        with pytest.raises(SDXLLoraStackError):
            model._load_loras(FakePipe(fail_load_for={"flaky.safetensors"}))

        retry_pipe = FakePipe()
        make_model(loras)._load_loras(retry_pipe)

        assert len(retry_pipe.unet_adapters) == 2
        assert retry_pipe.active[1] == [0.8, 0.4]

    def test_applied_stack_is_stamped_only_after_the_whole_stack_is_live(self, tmp_path):
        lora = write_lora(tmp_path / "loras", "style.safetensors")
        model = make_model([{"file_path": lora, "weight": 0.65}])

        stamps = []
        pipe = FakePipe()
        original = pipe.set_adapters

        def spy(**kwargs):
            stamps.append(model.applied_lora_stack)
            return original(**kwargs)

        pipe.set_adapters = spy
        model._load_loras(pipe)

        assert stamps == [None]
        assert model.applied_lora_stack == ((pipe.load_calls[0]["adapter_name"], 0.65),)

    def test_a_previous_stamp_is_cleared_before_the_pipeline_is_touched(self, tmp_path):
        bad = write_lora(tmp_path / "loras", "bad.safetensors")
        model = make_model([{"file_path": bad, "weight": 0.4}])
        model.applied_lora_stack = (("stale_adapter", 1.0),)

        with pytest.raises(SDXLLoraStackError):
            model._load_loras(FakePipe(fail_load_for={"bad.safetensors"}))

        assert model.applied_lora_stack is None


# --- through load(), including the txt2img/img2img reuse path --------------


@pytest.fixture
def cpu_only(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


@pytest.fixture
def fake_pipeline_class(monkeypatch):
    """Replaces from_single_file() with a FakePipe factory; `kwargs` configures
    how the next pipeline fails."""
    state = SimpleNamespace(kwargs={}, built=[])

    class FakePipelineClass:
        @staticmethod
        def from_single_file(**_):
            pipe = FakePipe(**state.kwargs)
            state.built.append(pipe)
            return pipe

    monkeypatch.setattr(
        "src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model.StableDiffusionXLKDiffusionPipeline",
        FakePipelineClass,
    )
    return state


class TestLoadEntryPoint:
    def test_load_leaves_no_pipeline_behind_when_the_stack_fails(
        self, tmp_path, cpu_only, fake_pipeline_class
    ):
        bad = write_lora(tmp_path / "loras", "bad.safetensors")
        fake_pipeline_class.kwargs = {"fail_load_for": {"bad.safetensors"}}
        model = make_model([{"file_path": bad, "weight": 0.4}])

        with pytest.raises(SDXLLoraStackError):
            model.load(mode="txt2img")

        assert model.pipe is None
        assert model.applied_lora_stack is None

    def test_txt2img_then_img2img_reuses_the_pipeline_without_reloading_adapters(
        self, tmp_path, cpu_only, fake_pipeline_class
    ):
        lora = write_lora(tmp_path / "loras", "style.safetensors")
        model = make_model([{"file_path": lora, "weight": 0.9}])

        model.load(mode="txt2img")
        stack_after_first = model.applied_lora_stack
        pipe = model.pipe
        model.load(mode="img2img")

        assert model.pipe is pipe
        assert len(pipe.load_calls) == 1
        assert model.loaded_pipe_type == "img2img"
        assert model.applied_lora_stack == stack_after_first


# --- the model cache key agrees with the stack the model will apply ---------


def make_loader_pipe(loras, model_path="/models/checkpoints/test.safetensors"):
    config = CheckpointLoaderSDXLPipe.get_default_config()
    config["model"] = {"file_path": model_path, "base": "SDXL", "name": "test"}
    config["loras"] = loras
    return CheckpointLoaderSDXLPipe(config)


class TestFingerprintMatchesEffectiveStack:
    """One file reached by two spellings is one adapter, so reversing the two
    entries changes which weight survives. The cache key has to move with it,
    or a cached model keeps generating at the old strength.
    """

    def test_reordering_two_spellings_of_one_file_changes_the_fingerprint(self, tmp_path):
        real = write_lora(tmp_path / "loras", "style.safetensors")
        alias = tmp_path / "loras" / ".." / "loras" / "style.safetensors"

        strong_last = make_loader_pipe(
            [{"file_path": alias, "weight": 0.2}, {"file_path": real, "weight": 0.8}]
        ).fingerprint()
        weak_last = make_loader_pipe(
            [{"file_path": real, "weight": 0.8}, {"file_path": alias, "weight": 0.2}]
        ).fingerprint()

        assert strong_last != weak_last

    def test_the_two_orders_really_do_apply_different_weights(self, tmp_path):
        real = write_lora(tmp_path / "loras", "style.safetensors")
        alias = tmp_path / "loras" / ".." / "loras" / "style.safetensors"

        strong_last = FakePipe()
        make_model(
            [{"file_path": alias, "weight": 0.2}, {"file_path": real, "weight": 0.8}]
        )._load_loras(strong_last)
        weak_last = FakePipe()
        make_model(
            [{"file_path": real, "weight": 0.8}, {"file_path": alias, "weight": 0.2}]
        )._load_loras(weak_last)

        assert len(strong_last.unet_adapters) == 1
        assert strong_last.active[1] == [0.8]
        assert weak_last.active[1] == [0.2]

    def test_equivalent_spellings_of_the_same_stack_share_a_fingerprint(self, tmp_path):
        real = write_lora(tmp_path / "loras", "style.safetensors")
        alias = tmp_path / "loras" / ".." / "loras" / "style.safetensors"

        assert (
            make_loader_pipe([{"file_path": real, "weight": 0.6}]).fingerprint()
            == make_loader_pipe([{"file_path": alias, "weight": 0.6}]).fingerprint()
        )

    def test_reordering_two_different_files_keeps_the_fingerprint(self, tmp_path):
        a = write_lora(tmp_path / "loras", "a.safetensors")
        b = write_lora(tmp_path / "loras", "b.safetensors")

        ab = make_loader_pipe(
            [{"file_path": a, "weight": 0.8}, {"file_path": b, "weight": 0.5}]
        ).fingerprint()
        ba = make_loader_pipe(
            [{"file_path": b, "weight": 0.5}, {"file_path": a, "weight": 0.8}]
        ).fingerprint()

        assert ab == ba

    def test_changing_one_weight_changes_the_fingerprint(self, tmp_path):
        a = write_lora(tmp_path / "loras", "a.safetensors")
        b = write_lora(tmp_path / "loras", "b.safetensors")
        loras = [{"file_path": a, "weight": 0.8}, {"file_path": b, "weight": 0.5}]

        before = make_loader_pipe(loras).fingerprint()
        after = make_loader_pipe(
            [dict(loras[0], weight=0.3), loras[1]]
        ).fingerprint()

        assert before != after

    def test_zero_weight_loras_stay_out_of_the_fingerprint(self, tmp_path):
        kept = write_lora(tmp_path / "loras", "kept.safetensors")
        dropped = write_lora(tmp_path / "loras", "dropped.safetensors")

        assert (
            make_loader_pipe(
                [{"file_path": kept, "weight": 0.6}, {"file_path": dropped, "weight": 0}]
            ).fingerprint()
            == make_loader_pipe([{"file_path": kept, "weight": 0.6}]).fingerprint()
        )
