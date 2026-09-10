"""Streamed offload / partial residency must release device-derived caches.

An arch submodule can cache tensors derived from the current device/dtype/
shape (RoPE tables keyed on a signature that includes ``str(device)``) in
plain instance attributes rather than parameters/buffers, so they never enter
the state-dict and are normally cleared only by the module's own ``_apply``
override, which fires whenever ``nn.Module.to()``/``.cuda()`` runs.

Streamed offload (``NativeModel.offload()``'s early-return for an active
``ModuleStreamer``) and entering partial residency (``ModuleStreamer.apply()``)
both move weight tensors directly (``_move_own_tensors``) without ever calling
``.to()`` on the root module, so ``_apply`` never fires there. Without an
explicit release, a cache built while the model was GPU-resident stays
reachable through a wrapper that now reports itself offloaded. This covers the
generic walker (``base.release_derived_caches``), the LTX arch's real caches,
and the two placement paths that must invoke it.
"""

from __future__ import annotations

import gc
import weakref

import numpy._core.multiarray  # noqa: F401 -- venv numpy 2.5.1/1.26.4 dist-info overlay trap
import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.arch.ltx.model import LTXAVModel
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.base import load_into_module, release_derived_caches
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from vendor.gpl.comfyui.ops import pick_operations

# Tiny 19b-shaped AV config (mirrors test_ltx_forward.py's TINY_19B): both the
# top-level positional-embedding cache and the embeddings-connector caches are
# real, populated by the model's own forward paths.
TINY_LTX = {
    "image_model": "ltxav", "in_channels": 8, "out_channels": 8,
    "num_attention_heads": 2, "attention_head_dim": 4, "cross_attention_dim": 8,
    "caption_channels": 12, "num_layers": 1,
    "audio_num_attention_heads": 2, "audio_attention_head_dim": 4,
    "audio_cross_attention_dim": 8, "audio_in_channels": 128,
    "has_caption_projection": True,
    "use_embeddings_connector": True, "connector_attention_head_dim": 4,
    "video_connector_inner": 8, "audio_connector_inner": 8, "connector_num_layers": 1,
    "connector_num_learnable_registers": 4,
    "blocks_gated": False, "has_prompt_adaln": False,
}


def _build_ltx() -> LTXAVModel:
    m = LTXAVModel.from_config(TINY_LTX, pick_operations(torch.float32, torch.float32))
    with torch.no_grad():
        for p in m.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
    return m.eval()


def _populate_caches(m: LTXAVModel) -> None:
    """Drive the model's real forward paths so both cache owners fill in."""
    vx = torch.randn(1, 8, 2, 2, 2)
    ax = torch.randn(1, 8, 3, 16)
    context = torch.randn(1, 5, 2 * 12)
    timestep = torch.tensor([0.5])
    with torch.inference_mode():
        m.forward([vx, ax], timestep, context)
    m.video_embeddings_connector(torch.randn(1, 6, 8))
    m.audio_embeddings_connector(torch.randn(1, 6, 8))
    assert m._pe_cache is not None
    assert m.video_embeddings_connector._pe_cache is not None
    assert m.audio_embeddings_connector._pe_cache is not None


def _streamed_model(m: nn.Module) -> tuple[NativeModel, ModuleStreamer]:
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    model = NativeModel("diffusion_model", m, estimated_vram_gb=23.3)
    model._streamer = streamer
    return model, streamer


# --- generic fake arch (no LTX/H3-specific machinery required) ----------------


class _FakeArch(nn.Module):
    """Minimal stand-in mirroring the real contract: a plain-attribute cache
    keyed on device, released via ``release_derived_caches`` and by ``_apply``."""

    def __init__(self, dim: int = 4, cache_elems: int = 256) -> None:
        super().__init__()
        self.linear = nn.Linear(dim, dim)
        self._cache_elems = cache_elems
        self._cache_key: str | None = None
        self._cache: torch.Tensor | None = None

    def build_cache(self, device: str = "cpu") -> None:
        self._cache_key = device
        self._cache = torch.zeros(self._cache_elems, device=device)

    def release_derived_caches(self) -> int:
        released = 0 if self._cache is None else self._cache.numel() * self._cache.element_size()
        self._cache_key = None
        self._cache = None
        return released

    def _apply(self, fn, recurse: bool = True):
        self.release_derived_caches()
        return super()._apply(fn, recurse=recurse)


class _RaisingArch(nn.Module):
    """A cache owner whose release fails from its ``fail_from_call``'th call
    onward -- for the failure-cleanup tests. Defaults to failing immediately."""

    def __init__(self, fail_from_call: int = 1) -> None:
        super().__init__()
        self.linear = nn.Linear(2, 2)
        self._calls = 0
        self._fail_from_call = fail_from_call

    def release_derived_caches(self) -> int:
        self._calls += 1
        if self._calls >= self._fail_from_call:
            raise RuntimeError("boom")
        return 0


def test_walker_sums_bytes_across_every_owner_and_clears_them():
    root = nn.Module()
    root.a = _FakeArch(cache_elems=100)
    root.b = _FakeArch(cache_elems=50)
    root.a.build_cache()
    root.b.build_cache()

    released = release_derived_caches(root)

    assert released == (100 + 50) * 4  # float32
    assert root.a._cache is None and root.b._cache is None


def test_walker_is_noop_when_no_owner_has_a_cache():
    root = nn.Module()
    root.a = _FakeArch()
    assert release_derived_caches(root) == 0


def test_walker_duck_types_module_itself_no_attribute_error_on_bare_object():
    """A lightweight test double without the full nn.Module surface (as used
    throughout test_engine.py's offload()/move_to() fakes) must be a no-op,
    not an AttributeError -- release_derived_caches() has to compose with
    those existing fakes."""

    class _BareFake:
        def to(self, device):
            return self

    assert release_derived_caches(_BareFake()) == 0


def test_walker_propagates_a_raising_owner():
    """Matches offload()'s existing streamed-branch contract: nothing there is
    wrapped in a defensive try/except (unlike unload()'s best-effort teardown),
    so a broken cache owner must fail loudly, not be swallowed."""
    root = nn.Module()
    root.bad = _RaisingArch()
    with pytest.raises(RuntimeError, match="boom"):
        release_derived_caches(root)


# --- ModuleStreamer.apply() releases caches on the entry transition -----------


def test_streamer_apply_releases_a_stale_cache_built_for_a_different_placement():
    m = nn.Module()
    m.fake = _FakeArch()
    m.fake.build_cache("cpu")
    old_key = m.fake._cache_key
    cache_ref = weakref.ref(m.fake._cache)

    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)

    assert m.fake._cache is None
    assert m.fake._cache_key is None
    assert m.fake._cache_key != old_key
    gc.collect()
    assert cache_ref() is None, "cache tensor must not survive the placement transition"

    streamer.teardown()


# --- NativeModel.offload()'s streamed early-return releases LTX's real caches -


def test_release_derived_caches_direct_call_reports_ltx_cache_bytes():
    m = _build_ltx()
    _populate_caches(m)

    released = release_derived_caches(m)

    assert released > 0
    assert m._pe_cache is None
    assert m.video_embeddings_connector._pe_cache is None
    assert release_derived_caches(m) == 0  # idempotent: nothing left to release


def test_streamed_offload_releases_ltx_pe_caches():
    m = _build_ltx()
    # Enter streamed residency first (as a real generation does before its
    # denoise loop), THEN populate the caches -- apply()'s own release call
    # (of whatever pre-existed) must not be mistaken for the offload fix.
    model, _streamer = _streamed_model(m)
    _populate_caches(m)

    top_ref = weakref.ref(m._pe_cache[0][0][0])  # (v_pe, av_cross_video) cos tensor
    video_conn_ref = weakref.ref(m.video_embeddings_connector._pe_cache[0])
    audio_conn_ref = weakref.ref(m.audio_embeddings_connector._pe_cache[0])
    old_top_key = m._pe_cache_key
    old_video_key = m.video_embeddings_connector._pe_cache_key

    model.offload()

    assert model.device == "cpu"
    assert m._pe_cache is None and m._pe_cache_key is None
    assert m._pe_cache_key != old_top_key
    assert m.video_embeddings_connector._pe_cache is None
    assert m.video_embeddings_connector._pe_cache_key != old_video_key
    assert m.audio_embeddings_connector._pe_cache is None

    gc.collect()
    assert top_ref() is None, "top-level positional-embedding cache leaked past offload"
    assert video_conn_ref() is None, "video connector cache leaked past offload"
    assert audio_conn_ref() is None, "audio connector cache leaked past offload"


def test_stale_cache_key_misses_after_streamed_offload():
    """A cache key built for the pre-offload placement must not satisfy a later
    lookup with the identical signature -- the offload path must force a real
    recompute rather than silently answering from a torn-down cache."""
    m = _build_ltx()
    _populate_caches(m)
    connector = m.video_embeddings_connector
    seq_len = connector._pe_cache_key[0]
    device, dtype = connector._pe_cache_key[1], connector._pe_cache_key[2]
    old_cache = connector._pe_cache

    model, _streamer = _streamed_model(m)
    model.offload()

    # Same signature as before the offload: must be a structural miss (key is
    # None), never `old_cache` handed back as if nothing happened.
    assert connector._pe_cache_key is None
    hit = connector._pe_cache_key == (seq_len, device, dtype)
    assert not hit
    assert connector._pe_cache is not old_cache


def test_normal_offload_control_clears_caches_via_apply_as_before():
    """Control: a component with no active streamer takes offload()'s
    move_to("cpu") branch, which already clears caches via `_apply` -- this
    path is untouched by the fix and must keep working."""
    m = _build_ltx()
    _populate_caches(m)
    model = NativeModel("diffusion_model", m, estimated_vram_gb=23.3)

    model.offload()

    assert model.device == "cpu"
    assert m._pe_cache is None
    assert m.video_embeddings_connector._pe_cache is None


def test_repeated_streamed_offload_resident_cycles_never_leak_a_cache():
    """Cycle streamed -> offloaded -> resident (re-streamed) three times; no
    cycle may leave a previous cycle's cache tensor reachable."""
    m = _build_ltx()
    refs: list[weakref.ReferenceType] = []

    for _ in range(3):
        _populate_caches(m)
        refs.append(weakref.ref(m._pe_cache[0][0][0]))
        model, _streamer = _streamed_model(m)
        model.offload()
        assert m._pe_cache is None

    gc.collect()
    for i, ref in enumerate(refs):
        assert ref() is None, f"cycle {i}: cache tensor survived offload"


def test_failure_in_one_cache_owner_propagates_out_of_offload():
    """Matches offload()'s existing no-defensive-catch contract for the
    streamed branch: a broken owner must abort the call loudly rather than
    leave the model half-updated and silently reporting success."""
    root = nn.Module()
    # fail_from_call=2: apply()'s own release call (below) is call #1 and must
    # succeed, isolating the failure to offload()'s explicit call (#2) so this
    # actually tests offload()'s propagation, not apply()'s.
    root.bad = _RaisingArch(fail_from_call=2)
    plan = plan_residency_split(root, resident_budget_gb=0.0)
    streamer = ModuleStreamer(root)
    streamer.apply("cpu", plan, pin=False)
    model = NativeModel("diffusion_model", root, estimated_vram_gb=23.3, device="cuda:0")
    model._streamer = streamer

    with pytest.raises(RuntimeError, match="boom"):
        model.offload()

    # offload() raised before reaching note_offloaded()/device="cpu" -- the
    # model must not claim to have completed the transition it didn't finish.
    assert model.device == "cuda:0"


# --- MiniMax-H3: same contract, its own RoPE cos/sin cache ---------------------
#
# Tiny config mirrors tests/platform/runtime/native/arch/test_minimax_h3_model.py's
# TINY_FULL (keeps the real model's shape traps: heads*head_dim != hidden_size,
# fused fc1). ``_build_h3`` goes through the real ``load_into_module`` path (like
# that file) so ``rope.inv_freq`` is real post_load()-derived, not empty.

TINY_H3 = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": 6, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
    "pruned": False, "time_embed_dim": 12, "freq_dim": 8, "time_embed_hidden_dim": 16,
}


def _build_h3() -> MiniMaxH3Model:
    m = MiniMaxH3Model.from_config(TINY_H3, pick_operations(torch.float32, torch.float32))
    sd = {}
    for k, v in m.state_dict().items():
        sd[k] = v.clone() if not v.is_floating_point() else torch.randn_like(v) * 0.02
    load_into_module(m, sd, match_model_spec(TINY_H3))
    return m.eval()


def _h3_streamed_model(m: nn.Module) -> tuple[NativeModel, ModuleStreamer]:
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    model = NativeModel("diffusion_model", m, estimated_vram_gb=23.3)
    model._streamer = streamer
    return model, streamer


def test_h3_streamed_offload_releases_pe_cache():
    m = _build_h3()
    model, _streamer = _h3_streamed_model(m)
    position_ids = torch.rand(7, 3, dtype=torch.float64)
    m._prepare_positional_embeddings(position_ids, torch.float32)
    assert m._pe_cache is not None
    cache_ref = weakref.ref(m._pe_cache[0])

    model.offload()

    assert model.device == "cpu"
    assert m._pe_cache is None and m._pe_cache_key is None
    gc.collect()
    assert cache_ref() is None, "H3 RoPE cos/sin cache leaked past streamed offload"


def test_h3_stale_cache_key_misses_after_streamed_offload():
    """A key built for the pre-offload placement -- even from the SAME
    ``position_ids`` tensor object, so id()/version/shape/device all still
    match -- must not be silently served from a torn-down cache."""
    m = _build_h3()
    position_ids = torch.rand(7, 3, dtype=torch.float64)
    old_result = m._prepare_positional_embeddings(position_ids, torch.float32)
    old_key = m._pe_cache_key

    model, _streamer = _h3_streamed_model(m)
    model.offload()

    assert m._pe_cache_key is None
    assert m._pe_cache_key != old_key
    # Recomputing with the identical tensor object must rebuild, not reuse the
    # released tuple, since the cache holding it is gone.
    new_result = m._prepare_positional_embeddings(position_ids, torch.float32)
    assert new_result is not old_result
    assert m._pe_cache_key == old_key  # id/version/shape/device do match again


def test_h3_normal_offload_control_clears_cache_via_apply_as_before():
    """Control: no active streamer -> offload()'s move_to("cpu") branch,
    which already clears the cache via `_apply` -- untouched by the fix."""
    m = _build_h3()
    position_ids = torch.rand(7, 3, dtype=torch.float64)
    m._prepare_positional_embeddings(position_ids, torch.float32)
    model = NativeModel("diffusion_model", m, estimated_vram_gb=23.3)

    model.offload()

    assert model.device == "cpu"
    assert m._pe_cache is None


def test_h3_release_derived_caches_direct_call_is_idempotent():
    m = _build_h3()
    position_ids = torch.rand(7, 3, dtype=torch.float64)
    m._prepare_positional_embeddings(position_ids, torch.float32)

    released = release_derived_caches(m)

    assert released > 0
    assert m._pe_cache is None
    assert release_derived_caches(m) == 0
