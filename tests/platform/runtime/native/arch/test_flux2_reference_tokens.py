from __future__ import annotations

import torch

from .test_flux_model import TINY_FLUX1, TINY_FLUX2, _build_ready

X = (1, 16, 6, 8)
REF_A = (1, 16, 4, 5)
REF_B = (1, 16, 3, 7)


def _prepare(m, refs, method="index"):
    x = torch.randn(*X)
    context = torch.randn(1, 5, 32)
    return m._prepare_branch(x, context, None, None, None, tuple(refs), method)


def test_flux2_defaults_to_index_offsets_of_ten():
    m = _build_ready(TINY_FLUX2)
    assert m.params.default_ref_method == "index"
    assert m.params.ref_index_scale == 10.0


def test_references_are_packed_into_one_clean_token_run_in_order():
    m = _build_ready(TINY_FLUX2)
    a, b = torch.randn(*REF_A), torch.randn(*REF_B)
    prepared = _prepare(m, [a, b])
    expected = torch.cat([m._pack_tokens(a), m._pack_tokens(b)], dim=1)
    assert prepared.ref_tokens.shape == (1, 4 * 5 + 3 * 7, 16)
    assert torch.equal(prepared.ref_tokens, expected)


def test_position_ids_give_each_reference_its_own_time_slot_and_grid():
    m = _build_ready(TINY_FLUX2)
    refs = [torch.randn(*REF_A), torch.randn(*REF_B)]
    captured = {}
    real = m.pe_embedder.forward

    def spy(ids):
        captured["ids"] = ids
        return real(ids)

    m.pe_embedder.forward = spy
    _prepare(m, refs)
    img = captured["ids"][:, 5:]
    n_x = X[2] * X[3]
    n_a = REF_A[2] * REF_A[3]
    assert img[:, :n_x, 0].unique().tolist() == [0.0]
    assert img[:, n_x:n_x + n_a, 0].unique().tolist() == [10.0]
    assert img[:, n_x + n_a:, 0].unique().tolist() == [20.0]
    ref_a = img[0, n_x:n_x + n_a]
    assert ref_a[:, 1].max().item() == REF_A[2] - 1
    assert ref_a[:, 2].max().item() == REF_A[3] - 1
    assert ref_a[:, 1].min().item() == 0 and ref_a[:, 2].min().item() == 0


def test_output_keeps_the_noise_latent_shape_and_depends_on_references():
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(*X)
    t = torch.tensor([0.5])
    context = torch.randn(1, 5, 32)
    a, b = torch.randn(*REF_A), torch.randn(*REF_B)
    with torch.no_grad():
        plain = m(x, t, context)
        one = m(x, t, context, ref_latents=[a])
        two = m(x, t, context, ref_latents=[a, b])
        swapped = m(x, t, context, ref_latents=[b, a])
    assert plain.shape == one.shape == two.shape == x.shape
    assert not torch.allclose(plain, one)
    assert not torch.allclose(one, two)
    assert not torch.allclose(two, swapped)


def test_flux1_default_method_differs_from_flux2():
    assert _build_ready(TINY_FLUX1).params.default_ref_method == "offset"
