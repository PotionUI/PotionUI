from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.io.quant_formats import nvfp4_activation_amax, quantize_nvfp4_dynamic
from src.platform.runtime.native.kernels import capabilities, nvfp4_quant, nvfp4_quant_disabled_reason, quantize_nvfp4_activation

pytestmark = pytest.mark.requires_gpu

DTYPES = [torch.bfloat16, torch.float16, torch.float32]


@pytest.fixture(autouse=True)
def _triton_gpu():
    caps = capabilities()
    if not torch.cuda.is_available() or not caps.triton or not caps.device(0).at_least(8, 9):
        pytest.skip("needs a CUDA GPU of compute capability 8.9+ with Triton")
    nvfp4_quant.reset_nvfp4_quant_state()
    yield
    assert nvfp4_quant_disabled_reason() is None


def _bits(t: torch.Tensor) -> torch.Tensor:
    return t.view(torch.uint8) if t.element_size() == 1 else t.view(torch.int32)


def _assert_matches_eager(x: torch.Tensor, tensor_amax: torch.Tensor | None = None) -> None:
    from src.platform.runtime.native.kernels import nvfp4_quant_triton

    want = quantize_nvfp4_dynamic(x, tensor_amax=tensor_amax)
    got = quantize_nvfp4_activation(x, tensor_amax=tensor_amax)
    for g, w in zip(got, want):
        assert g.shape == w.shape and g.dtype == w.dtype
        assert torch.equal(_bits(g), _bits(w))
    amax = nvfp4_activation_amax(x) if tensor_amax is None else tensor_amax
    for g, w in zip(nvfp4_quant_triton.quantize(x, amax), want):
        assert torch.equal(_bits(g), _bits(w))


def _randn(m, k, dtype, seed, scale=1.0):
    g = torch.Generator(device="cuda").manual_seed(seed)
    return (torch.randn(m, k, device="cuda", generator=g) * scale).to(dtype)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("k", [16, 3072, 5376, 14336])
@pytest.mark.parametrize("m", [1, 7, 129, 300])
@pytest.mark.parametrize("given_amax", [False, True])
def test_random_activations_match_the_eager_quantizer_bit_for_bit(dtype, k, m, given_amax):
    x = _randn(m, k, dtype, seed=m * 100003 + k)
    amax = nvfp4_activation_amax(x) * 1.375 if given_amax else None
    _assert_matches_eager(x, amax)


@pytest.mark.parametrize("dtype", DTYPES)
def test_log_uniform_magnitudes_across_every_code_match(dtype):
    g = torch.Generator(device="cuda").manual_seed(7)
    lo, hi = (-14, 14) if dtype == torch.float16 else (-60, 60)
    mags = torch.exp2(torch.empty(257, 1024, device="cuda").uniform_(lo, hi, generator=g))
    signs = torch.where(torch.rand(257, 1024, device="cuda", generator=g) < 0.5, -1.0, 1.0)
    block_scale = torch.exp2(torch.randint(-8, 9, (257, 64, 1), device="cuda", generator=g).float())
    x = (mags.view(257, 64, 16) * block_scale).view(257, 1024) * signs
    _assert_matches_eager(x.to(dtype))
    _assert_matches_eager(x.to(dtype), torch.tensor(3.0, device="cuda"))


@pytest.mark.parametrize("dtype", DTYPES)
def test_zero_blocks_and_negative_zero_match(dtype):
    x = _randn(130, 512, dtype, seed=1)
    x[3] = 0
    x[5, :16] = -0.0
    x[6, 3] = -0.0
    x[7, 32:48] = -0.0
    _assert_matches_eager(x)
    _assert_matches_eager(torch.zeros(33, 256, device="cuda", dtype=dtype))
    _assert_matches_eager(torch.full((33, 256), -0.0, device="cuda", dtype=dtype))


@pytest.mark.parametrize("dtype", DTYPES)
def test_huge_and_tiny_magnitudes_match(dtype):
    big = 6.0e4 if dtype == torch.float16 else 3.0e38
    tiny = 6.0e-8 if dtype == torch.float16 else 1.0e-38
    x = _randn(64, 256, dtype, seed=2)
    x[1, :40] = (x[1, :40].float() * big / 10).to(dtype)
    x[2, 7] = big
    _assert_matches_eager(x)
    t = _randn(64, 256, dtype, seed=3, scale=tiny)
    _assert_matches_eager(t)
    t[0, 0] = 1.0
    _assert_matches_eager(t)


@pytest.mark.parametrize("dtype", DTYPES)
def test_inf_and_nan_match_the_eager_quantizer(dtype):
    x = _randn(64, 256, dtype, seed=4)
    x[4, 9] = float("inf")
    x[8, 1] = float("-inf")
    _assert_matches_eager(x)
    _assert_matches_eager(x, torch.tensor(5.0, device="cuda"))
    y = _randn(64, 256, dtype, seed=5)
    y[10, 2] = float("nan")
    _assert_matches_eager(y)
    _assert_matches_eager(y, torch.tensor(5.0, device="cuda"))


@pytest.mark.parametrize("amax", [0.0, 1e-30, 1e-3, 1e30, float("inf")])
def test_given_tensor_amax_extremes_match(amax):
    _assert_matches_eager(_randn(48, 1024, torch.bfloat16, seed=6), torch.tensor(amax, device="cuda"))


def test_a_row_chunk_with_the_full_tensor_amax_matches():
    x = _randn(640, 3072, torch.bfloat16, seed=8)
    amax = nvfp4_activation_amax(x)
    for start in range(0, 640, 128):
        _assert_matches_eager(x[start:start + 128], amax)


def _every_float_around(value: float, ulps: int) -> torch.Tensor:
    centre = torch.tensor([value], dtype=torch.float32).view(torch.int32).item()
    return torch.arange(centre - ulps, centre + ulps, dtype=torch.int32, device="cuda").view(torch.float32)


def test_every_float_around_the_exponent_and_rounding_thresholds_matches():
    thresholds = [2.0 ** (n - 1.1925) for n in (1, 2, 3)] + [0.25, 0.75, 1.25, 1.75, 2.5, 3.5, 5.0]
    body = torch.cat([_every_float_around(t, 1 << 15) for t in thresholds])
    rows = body.numel() // 15
    x = torch.cat([torch.full((rows, 1), 6.0, device="cuda"), body[: rows * 15].view(rows, 15)], dim=1)
    x = torch.cat([x, -x], dim=1)
    amax = torch.tensor(10.5, device="cuda")
    _, block, scale = quantize_nvfp4_dynamic(x[:1], tensor_amax=amax)
    assert scale.item() == 2.0 ** -8
    assert block[0, 0].float().item() == 256.0
    _assert_matches_eager(x, amax)
    _assert_matches_eager(x.to(torch.bfloat16), amax)


HOST_STALLS = ("cudaStreamSynchronize", "cudaDeviceSynchronize", "cudaEventSynchronize", "aten::_local_scalar_dense", "aten::item")
SIDE_WORK = ("aten::clamp", "aten::div", "aten::zero_", "aten::fill_", "aten::zeros", "aten::_to_copy")


def _profiled_names(fn) -> list[str]:
    from collections import Counter

    from torch.profiler import ProfilerActivity, profile

    def names(f):
        f()
        torch.cuda.synchronize()
        with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
            f()
        return Counter(e.name for e in prof.events())

    return list((names(fn) - names(lambda: None)).elements())


@pytest.mark.parametrize("rows", [1, 300])
@pytest.mark.parametrize("given_amax", [False, True])
def test_a_quantization_launches_only_the_reduction_and_the_kernel_without_host_stalls(rows, given_amax):
    x = _randn(rows, 3072, torch.bfloat16, seed=9)
    amax = nvfp4_activation_amax(x) if given_amax else None
    names = _profiled_names(lambda: quantize_nvfp4_activation(x, amax))
    assert names.count("_nvfp4_quant_kernel") == 1
    assert ("aten::linalg_vector_norm" in names) is not given_amax
    assert [n for n in names if n in HOST_STALLS or "DtoH" in n] == []
    assert [n for n in names if n in SIDE_WORK] == []
