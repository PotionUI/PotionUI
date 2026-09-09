"""Output equivalence of the LoRA application paths, against the pre-staging one.

Stage 1 changed WHERE a runtime-mode delta's rank factors live, not what they
are. That claim is only worth as much as a forward comparison against the path
it replaced, over the delta shapes that actually occur: a ``target_slice`` into
a fused qkv, a LoKr Kronecker pair, and adapters stored in each of the three
float precisions trainers ship.

``_reference_apply`` below reproduces ``apply_loras_with_report``'s loop as it
stood at f4fa70bb^ (the commit before staging landed). It calls the SAME
``map_lora_keys``, ``_needs_runtime_deltas`` and ``_apply_inplace`` the current
code does -- all three are byte-identical between the two revisions -- so the
only thing it reproduces is the one branch that changed: a quantised target got
its deltas attached exactly as mapped, on the CPU, in the file's own dtype.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from src.platform.runtime.native.lora.apply import (
    _ScratchPool,
    _apply_inplace,
    _needs_runtime_deltas,
    _stage_runtime_deltas,
)
from src.platform.runtime.native.lora.key_mapping import LoraDelta
from vendor.gpl.comfyui import ops as _ops_module
from vendor.gpl.comfyui.ops import (
    _add_lora_output_branch,
    apply_lora_deltas,
    partition_output_branch_deltas,
    pick_operations,
)

HIDDEN, RANK = 16, 16
OUT = HIDDEN * 3          # a fused qkv


@pytest.fixture(autouse=True)
def _on_the_cpu(monkeypatch):
    """Keep every path on the CPU so the comparison is arithmetic, not
    scheduling, and so nothing lands on the shared card."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


def _reference_apply(linear, deltas) -> None:
    """The f4fa70bb^ application: attach as mapped, or patch in place."""
    if _needs_runtime_deltas(linear):
        if linear.lora_deltas is None:
            linear.lora_deltas = []
        linear.lora_deltas.extend(deltas)
    else:
        _apply_inplace(linear, deltas, _ScratchPool())


def _staged_apply(linear, deltas) -> None:
    """The current application (stage 1)."""
    if _needs_runtime_deltas(linear):
        staged, _bytes = _stage_runtime_deltas(deltas, torch.device("cpu"))
        if linear.lora_deltas is None:
            linear.lora_deltas = []
        linear.lora_deltas.extend(staged)
    else:
        _apply_inplace(linear, deltas, _ScratchPool())


def _fp8_linear(scale=0.5):
    lin = pick_operations(torch.float8_e4m3fn, torch.bfloat16).Linear(HIDDEN, OUT, bias=False)
    torch.manual_seed(7)
    dense = torch.randn(OUT, HIDDEN) * 0.2
    if scale is not None:
        lin.weight_scale = torch.tensor(float(scale))
        dense = dense / float(scale)
    lin.weight.data = dense.to(torch.float8_e4m3fn)
    return lin


def _bf16_linear():
    lin = pick_operations(torch.bfloat16, torch.bfloat16).Linear(HIDDEN, OUT, bias=False)
    torch.manual_seed(7)
    lin.weight.data = (torch.randn(OUT, HIDDEN) * 0.2).to(torch.bfloat16)
    return lin


def _qkv_slice_deltas(dtype, strength=1.0):
    """What a diffusers adapter becomes: to_q/to_k/to_v as three row-slices of
    one fused weight. The case where losing one delta would read as a weaker
    adapter rather than a broken one."""
    torch.manual_seed(8)
    out = []
    for index in range(3):
        out.append(LoraDelta(
            down=(torch.randn(RANK, HIDDEN) * 0.3).to(dtype),
            up=(torch.randn(HIDDEN, RANK) * 0.3).to(dtype),
            alpha=float(RANK), scale=1.0 * strength,
            target_slice=(0, index * HIDDEN, HIDDEN),
        ))
    return out


def _lokr_delta(dtype, strength=1.0):
    """LyCORIS Kronecker: up/down are the factors w1/w2 and alpha is already
    pre-divided, so a path that treated them as a plain rank expansion would
    be wrong by orders of magnitude rather than subtly."""
    torch.manual_seed(9)
    # kron(w1, w2) is (w1.rows*w2.rows, w1.cols*w2.cols) -> (48, 16) = the weight.
    return [LoraDelta(down=(torch.randn(6, 4) * 0.3).to(dtype),
                      up=(torch.randn(8, 4) * 0.3).to(dtype),
                      alpha=0.5, scale=1.0 * strength, kron=True)]


def _dequantised(linear, dtype):
    """The layer's stored weight as its own forward materialises it, with no
    LoRA: cast, then the per-tensor scale when there is one."""
    weight = linear.weight.to(dtype)
    if linear.weight_scale is not None:
        weight = weight * linear.weight_scale.to(dtype)
    return weight


def _forward(linear, x):
    with torch.no_grad():
        return linear(x).float()


def _x():
    torch.manual_seed(10)
    return torch.randn(32, HIDDEN, dtype=torch.bfloat16)


DTYPES = [torch.float16, torch.bfloat16, torch.float32]


class TestStagingIsOutputEquivalent:
    """Stage 1 vs the path it replaced. Exact, not approximate: staging moves a
    device and makes a tensor contiguous, neither of which touches a value."""

    @pytest.mark.parametrize("dtype", DTYPES)
    @pytest.mark.parametrize("scale", [None, 0.5])
    def test_fused_qkv_slice_deltas_on_fp8(self, dtype, scale):
        x = _x()
        ref, cur = _fp8_linear(scale), _fp8_linear(scale)
        _reference_apply(ref, _qkv_slice_deltas(dtype))
        _staged_apply(cur, _qkv_slice_deltas(dtype))

        assert len(cur.lora_deltas) == 3
        assert torch.equal(_forward(cur, x), _forward(ref, x))

    @pytest.mark.parametrize("dtype", DTYPES)
    def test_lokr_delta_on_fp8(self, dtype):
        x = _x()
        ref, cur = _fp8_linear(), _fp8_linear()
        _reference_apply(ref, _lokr_delta(dtype))
        _staged_apply(cur, _lokr_delta(dtype))

        assert cur.lora_deltas[0].kron is True
        assert torch.equal(_forward(cur, x), _forward(ref, x))

    @pytest.mark.parametrize("dtype", DTYPES)
    def test_the_in_place_bf16_path_is_untouched(self, dtype):
        x = _x()
        ref, cur = _bf16_linear(), _bf16_linear()
        _reference_apply(ref, _qkv_slice_deltas(dtype))
        _staged_apply(cur, _qkv_slice_deltas(dtype))

        assert ref.lora_deltas is None and cur.lora_deltas is None
        assert torch.equal(cur.weight.data.float(), ref.weight.data.float())
        assert torch.equal(_forward(cur, x), _forward(ref, x))

    @pytest.mark.parametrize("strength", [0.5, 1.0, 1.5])
    def test_effective_strength_is_unchanged(self, strength):
        """The symptom to rule out: an adapter that needs 1.5 to look like it
        used to at 1.0. Measured as the size of the adapter's own contribution,
        which is what a user reads as strength."""
        x = _x()
        base = _forward(_fp8_linear(), x)
        ref, cur = _fp8_linear(), _fp8_linear()
        _reference_apply(ref, _qkv_slice_deltas(torch.bfloat16, strength))
        _staged_apply(cur, _qkv_slice_deltas(torch.bfloat16, strength))

        ref_effect = (_forward(ref, x) - base).norm()
        cur_effect = (_forward(cur, x) - base).norm()
        assert ref_effect > 0
        assert (cur_effect / ref_effect).item() == pytest.approx(1.0, abs=1e-6)



class TestActivationSideIsEquivalentToTheWeightSide:
    """The quantised forwards now add a plain delta to the ACTIVATION rather
    than to a cloned weight. ``x @ (up @ down).T == (x @ down.T) @ up.T`` is an
    identity, so the two agree to within the reduction order's own rounding --
    not to within any modelling tolerance."""

    def _weight_side_forward(self, linear, x):
        """What the forward did before: materialise the delta onto the
        dequantised weight, then one matmul."""
        weight = _dequantised(linear, x.dtype)
        weight = apply_lora_deltas(weight, linear.lora_deltas)
        with torch.no_grad():
            return torch.nn.functional.linear(x, weight, None).float()

    @pytest.mark.parametrize("dtype", DTYPES)
    @pytest.mark.parametrize("scale", [None, 0.5])
    def test_fused_qkv_slice_deltas(self, dtype, scale):
        x = _x()
        lin = _fp8_linear(scale)
        lin.lora_deltas = _stage_runtime_deltas(_qkv_slice_deltas(dtype), torch.device("cpu"))[0]

        got = _forward(lin, x)
        want = self._weight_side_forward(lin, x)
        assert (got - want).norm() / want.norm() < 5e-3

    def test_a_mixed_stack_splits_by_delta_rather_than_all_or_nothing(self):
        """One LoKr adapter alongside two plain ones must not force the plain
        ones back onto the weight."""
        deltas = _qkv_slice_deltas(torch.bfloat16)[:2] + _lokr_delta(torch.bfloat16)
        output_side, weight_side = partition_output_branch_deltas(deltas, OUT)

        assert len(output_side) == 2
        assert len(weight_side) == 1 and weight_side[0].kron is True

    def test_a_lokr_only_stack_still_goes_through_the_weight(self):
        x = _x()
        lin = _fp8_linear()
        lin.lora_deltas = _stage_runtime_deltas(_lokr_delta(torch.bfloat16), torch.device("cpu"))[0]

        got = _forward(lin, x)
        want = self._weight_side_forward(lin, x)
        assert torch.equal(got, want)

    def test_a_stack_with_no_deltas_is_untouched(self):
        x = _x()
        with_none, empty = _fp8_linear(), _fp8_linear()
        empty.lora_deltas = []
        assert torch.equal(_forward(empty, x), _forward(with_none, x))


class _CastCountingTensor(torch.Tensor):
    """Counts the ``.to()`` calls made against a staged LoRA factor.

    ``_stage_runtime_deltas`` deliberately leaves a factor in the adapter
    file's dtype, so the output branch's ``.to(out.dtype)`` is a real cast
    rather than a short-circuit -- which makes "once per call" vs "once per
    chunk" a difference the forward actually pays for.
    """

    casts: list

    def to(self, *args, **kwargs):  # type: ignore[override]
        self.casts[0] += 1
        return torch.Tensor.to(self, *args, **kwargs)


def _counted_deltas(casts: list, count: int = 3):
    torch.manual_seed(311)
    deltas = []
    for i in range(count):
        factors = []
        for raw in ((torch.randn(RANK, HIDDEN) * 0.1).to(torch.float16),
                    (torch.randn(OUT, RANK) * 0.1).to(torch.float16)):
            factor = raw.as_subclass(_CastCountingTensor)
            factor.casts = casts
            factors.append(factor)
        deltas.append(LoraDelta(down=factors[0], up=factors[1],
                                alpha=float(RANK), scale=0.3 + 0.1 * i))
    return deltas


def _run_output_branch(deltas, x, chunk_bytes):
    out = torch.zeros(x.shape[0], OUT, dtype=torch.bfloat16)
    with torch.no_grad(), patch.object(
        _ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", chunk_bytes,
    ):
        return _add_lora_output_branch(out, x, deltas, OUT)


def test_output_branch_casts_each_factor_once_per_call_not_per_chunk():
    casts: list = [0]
    deltas = _counted_deltas(casts)
    rows = 200
    x = torch.randn(rows, HIDDEN, dtype=torch.bfloat16)

    casts[0] = 0
    single = _run_output_branch(deltas, x, rows * OUT * 2 * 4)
    single_casts = casts[0]

    casts[0] = 0
    chunked = _run_output_branch(deltas, x, 8 * OUT * 2)
    chunked_casts = casts[0]

    assert torch.equal(single, chunked)
    # two factors per delta, cast once each, however many chunks it took.
    assert single_casts == chunked_casts == 2 * len(deltas)
