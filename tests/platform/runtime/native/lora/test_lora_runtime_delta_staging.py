"""Runtime-mode LoRA deltas are staged for the forward that consumes them.

A LoRA state dict is read to CPU (``load_torch_file(..., device="cpu")``, mmapped
from the safetensors file) and ``map_lora_keys`` leaves it there. A delta
attached to a quantised Linear in that state makes
``vendor.gpl.comfyui.ops.apply_lora_deltas`` copy both rank factors
host-to-device AND cast them to fp32 on EVERY forward of that Linear, which on
an fp8 checkpoint is every patched linear on every sampling step.
"""

from __future__ import annotations

import types
import weakref

import pytest
import torch

from src.platform.runtime.native.lora.apply import (
    _runtime_delta_device,
    _stage_runtime_deltas,
    apply_loras,
    apply_loras_with_report,
    remove_loras,
)
from src.platform.runtime.native.lora.key_mapping import LoraDelta
from vendor.gpl.comfyui.ops import pick_operations

from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora


def _bf16_kohya_lora(**kwargs) -> dict:
    """A kohya LoRA in the half precision trainers actually ship, so a staged
    factor differs from the mapped one in dtype as well as device."""
    return {k: v.to(torch.bfloat16) if v.is_floating_point() and v.dim() else v
            for k, v in _kohya_lora(**kwargs).items()}


def _fp8_flux(monkeypatch):
    """A tiny Flux whose patched qkv has genuine fp8 storage (-> runtime mode),
    with CUDA hidden so the compute device is the CPU and no test allocates on
    the shared card."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    m = _build(pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)
    return m, qkv


class TestStaging:
    def test_attached_deltas_keep_the_adapters_own_dtype(self, monkeypatch):
        """Staging is device-only. Widening a half-precision adapter to the
        forward's fp32 math dtype would double what the stack costs on the
        card for a cast that is rank-sized, not weight-sized."""
        m, qkv = _fp8_flux(monkeypatch)
        apply_loras(m, [(_bf16_kohya_lora(), 1.0)])

        (delta,) = qkv.lora_deltas
        assert delta.up.dtype is torch.bfloat16
        assert delta.down.dtype is torch.bfloat16

    def test_the_forwards_own_transfer_returns_the_staged_tensor_unchanged(self, monkeypatch):
        """The property that removes the per-forward host-to-device copy: the
        factor is already on the device ``apply_lora_deltas`` asks for, so its
        transfer is a no-op returning the same object rather than a copy."""
        m, qkv = _fp8_flux(monkeypatch)
        apply_loras(m, [(_bf16_kohya_lora(), 1.0)])

        (delta,) = qkv.lora_deltas
        device = _runtime_delta_device(qkv)
        assert delta.up.to(device=device) is delta.up
        assert delta.down.to(device=device) is delta.down

    def test_a_view_factor_is_staged_contiguous(self):
        up = torch.randn(4, 192).t()          # (192, 4), not contiguous
        down = torch.randn(64, 4).t()         # (4, 64), not contiguous
        assert not up.is_contiguous() and not down.is_contiguous()

        [staged], nbytes = _stage_runtime_deltas(
            [LoraDelta(down=down, up=up, alpha=4.0, scale=1.0)], torch.device("cpu"))

        assert staged.up.is_contiguous() and staged.down.is_contiguous()
        assert torch.equal(staged.up, up) and torch.equal(staged.down, down)
        assert nbytes == (up.numel() + down.numel()) * up.element_size()

    def test_staging_preserves_the_delta_spec(self):
        original = LoraDelta(down=torch.randn(4, 64), up=torch.randn(64, 4),
                             alpha=8.0, scale=0.5, target_slice=(0, 64, 64), kron=True)
        [staged], _ = _stage_runtime_deltas([original], torch.device("cpu"))
        assert (staged.alpha, staged.scale, staged.target_slice, staged.kron) == (8.0, 0.5, (0, 64, 64), True)

    def test_in_place_targets_stage_nothing(self, monkeypatch):
        """A patchable-storage Linear is baked at apply time and has no
        per-forward delta to stage; reporting staged bytes for it would be a
        VRAM claim nothing actually holds."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        m = _build()  # fp32 storage throughout
        _patched, _unmatched, [report] = apply_loras_with_report(m, [(_kohya_lora(), 1.0)])

        assert report.runtime_params == 0
        assert report.inplace_params == report.matched_params > 0
        assert report.staged_bytes == 0
        assert report.staged_device is None

    def test_remove_drops_the_staged_tensors(self, monkeypatch):
        m, qkv = _fp8_flux(monkeypatch)
        apply_loras(m, [(_bf16_kohya_lora(), 1.0)])
        staged = weakref.ref(qkv.lora_deltas[0].up)
        assert staged() is not None

        remove_loras(m)

        assert qkv.lora_deltas is None
        assert staged() is None


class TestReport:
    def test_runtime_targets_are_counted_and_sized(self, monkeypatch):
        m, qkv = _fp8_flux(monkeypatch)
        _patched, _unmatched, [report] = apply_loras_with_report(
            m, [(_bf16_kohya_lora(), 1.0)], names=["/loras/style.safetensors"])

        assert (report.matched_params, report.runtime_params, report.inplace_params) == (1, 1, 0)
        assert report.staged_device == "cpu"
        (delta,) = qkv.lora_deltas
        assert report.staged_bytes == (delta.up.numel() + delta.down.numel()) * 2
        assert report.apply_seconds > 0.0

    def test_timing_stays_out_of_equality_so_repeated_evidence_dedupes(self, monkeypatch):
        """Callers dedupe adapter evidence by value (the windowed-LoRA
        diagnostic emits once per generation by comparing report tuples).
        Wall-clock inside ``__eq__`` would make every re-application unequal."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        lora = _kohya_lora()
        _p, _u, [first] = apply_loras_with_report(_build(), [(lora, 1.0)], names=["/a.safetensors"])
        _p, _u, [second] = apply_loras_with_report(_build(), [(lora, 1.0)], names=["/a.safetensors"])

        assert first.apply_seconds != second.apply_seconds
        assert first == second


class TestComputeDevice:
    def test_an_already_cuda_layer_names_its_own_index_not_the_current_one(self, monkeypatch):
        """A delta staged on cuda:0 for a layer living on cuda:1 is copied
        every forward exactly as a CPU one is, so the layer's own device wins
        over the process-current one."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
        on_cuda1 = types.SimpleNamespace(
            parameters=lambda recurse=True: [types.SimpleNamespace(device=torch.device("cuda", 1))],
            buffers=lambda recurse=True: [],
        )
        assert _runtime_delta_device(on_cuda1) == torch.device("cuda", 1)

    def test_a_cpu_pinned_streamed_layer_stages_on_the_current_cuda_device(self, monkeypatch):
        """Partial-residency streaming leaves the weight pinned on the CPU and
        streams it per forward -- the forward still runs on the card, which is
        where the deltas belong."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
        assert _runtime_delta_device(torch.nn.Linear(4, 4)) == torch.device("cuda", 0)

    def test_falls_back_to_the_weights_device_without_cuda(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert _runtime_delta_device(torch.nn.Linear(4, 4)) == torch.device("cpu")


@pytest.mark.requires_gpu
def test_runtime_deltas_land_on_the_cuda_device_the_layer_runs_on():
    m = _build(pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)
    m.to("cuda")

    apply_loras(m, [(_bf16_kohya_lora(), 1.0)])

    (delta,) = qkv.lora_deltas
    assert delta.up.device == qkv.weight.device
    assert delta.up.to(device=qkv.weight.device) is delta.up


def test_each_files_apply_is_marked_for_the_profiler(monkeypatch):
    import src.platform.runtime.native.lora.apply as apply_mod

    marks: list = []
    monkeypatch.setattr(apply_mod, "get_profiler", lambda: types.SimpleNamespace(
        mark=lambda event, **fields: marks.append((event, fields))))
    m, _qkv = _fp8_flux(monkeypatch)

    apply_loras_with_report(m, [(_bf16_kohya_lora(), 1.0)], names=["/loras/style.safetensors"])

    [(event, fields)] = marks
    assert event == "lora.apply"
    assert fields["source"] == "/loras/style.safetensors"
    assert (fields["inplace_params"], fields["runtime_params"]) == (0, 1)
    assert fields["staged_device"] == "cpu"
    assert fields["staged_mb"] > 0.0
    assert fields["seconds"] > 0.0


class TestStagingIsValuePreserving:
    """Staging moves a device; it must change nothing a forward can observe.

    The parsed-LoRA cache now hands the SAME state dict to every generation, so
    anything here that mutated or transformed a factor would show up as a LoRA
    that works once and then drifts.
    """

    def _fp8_linear(self):
        lin = pick_operations(torch.float8_e4m3fn, torch.bfloat16).Linear(16, 24, bias=False)
        torch.manual_seed(3)
        lin.weight.data = (torch.randn(24, 16) * 0.2).to(torch.float8_e4m3fn)
        lin.weight_scale = torch.tensor(0.5)
        return lin

    def _delta(self):
        torch.manual_seed(4)
        return LoraDelta(down=(torch.randn(4, 16) * 0.3).to(torch.bfloat16),
                         up=(torch.randn(24, 4) * 0.3).to(torch.bfloat16),
                         alpha=4.0, scale=0.8, target_slice=(0, 0, 24))

    def test_a_staged_forward_matches_the_unstaged_one_exactly(self, monkeypatch):
        """The pre-staging behaviour, reproduced by attaching the factors as
        they came off disk. Not 'within tolerance' -- bit-identical."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        x = torch.randn(6, 16, dtype=torch.bfloat16)
        delta = self._delta()

        unstaged = self._fp8_linear()
        unstaged.lora_deltas = [delta]
        staged = self._fp8_linear()
        staged.lora_deltas = _stage_runtime_deltas([delta], torch.device("cpu"))[0]

        with torch.no_grad():
            assert torch.equal(staged(x).float(), unstaged(x).float())

    def test_the_factors_survive_staging_unchanged(self):
        delta = self._delta()
        up_before, down_before = delta.up.clone(), delta.down.clone()

        [out], _bytes = _stage_runtime_deltas([delta], torch.device("cpu"))

        assert torch.equal(out.up, up_before) and torch.equal(out.down, down_before)
        assert torch.equal(delta.up, up_before) and torch.equal(delta.down, down_before)
        assert out.up.shape == up_before.shape and out.down.shape == down_before.shape

    def test_the_strength_folded_into_scale_before_staging_survives_it(self, monkeypatch):
        """``apply_loras_with_report`` multiplies the user's strength into
        ``scale`` and THEN stages; a stage that reset it would silently apply
        every adapter at 1.0."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        m, qkv = _fp8_flux(monkeypatch)

        apply_loras(m, [(_bf16_kohya_lora(), 0.25)])

        (delta,) = qkv.lora_deltas
        assert delta.scale == pytest.approx(0.25)
