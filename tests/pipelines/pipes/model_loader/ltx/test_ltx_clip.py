"""Tests for LTXClipTextEncoder: Gemma3 encode -> projection chain order,
batched multi-request encoding, and the projected-conditioning
cache.
"""

from __future__ import annotations

from types import SimpleNamespace

import torch

from src.pipelines.pipes.model_loader.ltx import ltx_clip as ltx_clip_module
from src.pipelines.pipes.model_loader.ltx.ltx_clip import LTXClipTextEncoder
from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache


class _FakeTE:
    role = "gemma3_12b"

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        b = len(texts)
        return {
            "context": torch.ones(b, 4, 8, device="cpu"),
            "attention_mask": torch.ones(b, 4, device="cpu"),
        }


class _FakeDitModule:
    def __init__(self):
        self.calls = []

    def apply_text_conditioning(self, gemma_output, attention_mask=None, **projections):
        self.calls.append({
            "device": gemma_output.device,
            "batch": gemma_output.shape[0],
            "mask_device": None if attention_mask is None else attention_mask.device,
            "projections": projections,
        })
        return torch.zeros(gemma_output.shape[0], 4, 16)


def _adapter(fingerprint="te|dit"):
    # The prompt-embed cache is a process-global singleton (see embed_cache.py) --
    # clear it so one test's cached entry can't leak into the next (several tests
    # here intentionally reuse the same prompt/negative/fingerprint).
    get_prompt_embed_cache().clear()
    te = _FakeTE()
    dit = _FakeDitModule()
    projections = {"video_projection_weight": torch.zeros(2, 2)}
    adapter = LTXClipTextEncoder(te, dit, projections, device="cpu", model_fingerprint=fingerprint)
    return adapter, te, dit


def test_encodes_positive_and_negative_in_one_batched_call_when_cfg_on():
    adapter, te, dit = _adapter()
    result = adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    # positive + negative are ONE encode() call, not two --
    # batching amortises the TE's own per-call overhead.
    assert te.calls == [["a cat", "blurry"]]
    assert "context" in result.embeds
    assert "context" in result.n_embeds


def test_skips_negative_encode_when_cfg_off():
    adapter, te, dit = _adapter()
    result = adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=False)
    assert te.calls == [["a cat"]]
    assert result.n_embeds == {}


def test_projection_runs_once_over_the_whole_batch():
    adapter, te, dit = _adapter()
    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    # ONE apply_text_conditioning call covering both the positive and negative
    # rows (not one per prompt) -- the projection/connector chain is batched too.
    assert len(dit.calls) == 1
    call = dit.calls[0]
    assert call["batch"] == 2
    assert call["device"] == torch.device("cpu")
    assert call["mask_device"] == torch.device("cpu")
    assert "video_projection_weight" in call["projections"]


def test_embeds_left_on_cpu_for_generator_to_move():
    adapter, te, dit = _adapter()
    result = adapter.encode_prompt("a cat", "blurry")
    assert result.embeds["context"].device == torch.device("cpu")
    assert result.n_embeds["context"].device == torch.device("cpu")


def test_encode_prompts_batches_multiple_requests_into_one_window():
    """An N-image batch must pay ONE move/encode/project
    window, not N. Two distinct-prompt requests -> ONE encode() call with all
    4 texts (2 pos + 2 neg) and ONE apply_text_conditioning call."""
    adapter, te, dit = _adapter()
    requests = [
        {"prompt": "a cat", "negative_prompt": "blurry", "do_classifier_free_guidance": True},
        {"prompt": "a dog", "negative_prompt": "grainy", "do_classifier_free_guidance": True},
    ]
    results = adapter.encode_prompts(requests)

    assert len(results) == 2
    assert te.calls == [["a cat", "blurry", "a dog", "grainy"]]
    assert len(dit.calls) == 1
    assert dit.calls[0]["batch"] == 4
    assert results[0].p_prompt == "a cat" and results[0].n_prompt == "blurry"
    assert results[1].p_prompt == "a dog" and results[1].n_prompt == "grainy"


def test_encode_prompts_mixed_cfg_flattens_correctly():
    """One request wants CFG, the other doesn't -- texts flatten to
    [pos0, neg0, pos1] (no neg1), and each result still gets the right slice."""
    adapter, te, dit = _adapter()
    requests = [
        {"prompt": "a cat", "negative_prompt": "blurry", "do_classifier_free_guidance": True},
        {"prompt": "a dog", "negative_prompt": "grainy", "do_classifier_free_guidance": False},
    ]
    results = adapter.encode_prompts(requests)

    assert te.calls == [["a cat", "blurry", "a dog"]]
    assert dit.calls[0]["batch"] == 3
    assert "context" in results[0].n_embeds
    assert results[1].n_embeds == {}


def test_repeated_request_hits_the_projected_conditioning_cache():
    """A second identical request must skip BOTH the encode AND the
    projection/connector chain (not just the raw Gemma3 encode) -- the cache
    now stores the final projected conditioning."""
    adapter, te, dit = _adapter()
    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    assert len(te.calls) == 1
    assert len(dit.calls) == 1

    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    assert len(te.calls) == 1   # no new encode
    assert len(dit.calls) == 1  # no new projection


def test_no_fingerprint_means_no_caching():
    adapter, te, dit = _adapter(fingerprint=None)
    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)
    assert len(te.calls) == 2
    assert len(dit.calls) == 2


# --- no duplicate projection weights ---------------------------
#
# `_move_projection_chain` used to rebind `self.projections` to a brand new
# dict on every device round-trip. The model loader (main.py) hands the SAME
# dict object to both `LTXModelBundle.projections` and this adapter's
# `self.projections` -- rebinding one side's reference broke that alias, and
# the GPU-and-back round trip then materialised a SECOND, permanently-held
# copy of the (bf16) projection weights in host RAM (the "video/audio
# projection weight present twice in the CPU census" leak). Moving each
# tensor's storage in place (`tensor.data = tensor.data.to(device)`) keeps
# every alias looking at the same object.


def test_move_projection_chain_preserves_shared_dict_and_tensor_identity():
    adapter, te, dit = _adapter()
    shared_dict = adapter.projections
    original_tensor = shared_dict["video_projection_weight"]

    adapter._move_projection_chain("cpu")  # a no-op move (already cpu) -- must not rebind anything

    assert adapter.projections is shared_dict
    assert adapter.projections["video_projection_weight"] is original_tensor


def test_encode_prompt_does_not_duplicate_projection_weights():
    """End-to-end: a real encode_prompt() call must not leave the adapter
    pointing at a different dict/tensor than whatever else (e.g. a bundle)
    was constructed with the same projections dict."""
    adapter, te, dit = _adapter()
    shared_dict = adapter.projections
    original_tensor = shared_dict["video_projection_weight"]

    adapter.encode_prompt("a cat", "blurry", do_classifier_free_guidance=True)

    assert adapter.projections is shared_dict
    assert adapter.projections["video_projection_weight"] is original_tensor


# --- projection ladder (never cascade a projection OOM into a
# full CPU re-encode) -----------------------------------------------------------


def test_ladder_stays_on_gpu_when_projection_budget_fits(monkeypatch):
    adapter, te, dit = _adapter()
    monkeypatch.setattr(ltx_clip_module.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(ltx_clip_module, "free_vram_gb", lambda dev: 20.0)

    calls = []
    monkeypatch.setattr(
        adapter, "_project_on",
        lambda raw, device: calls.append(device) or torch.zeros(1, 4, 16),
    )

    raw = {"context": SimpleNamespace(device="cuda:0"), "attention_mask": None}
    adapter._project_with_ladder(raw)

    assert calls == ["cuda:0"]  # projected right where the raw encode landed


def test_ladder_skips_straight_to_cpu_when_budget_does_not_fit(monkeypatch):
    adapter, te, dit = _adapter()
    monkeypatch.setattr(ltx_clip_module.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(ltx_clip_module, "free_vram_gb", lambda dev: 0.001)

    calls = []
    monkeypatch.setattr(
        adapter, "_project_on",
        lambda raw, device: calls.append(device) or torch.zeros(1, 4, 16),
    )

    raw = {"context": SimpleNamespace(device="cuda:0"), "attention_mask": None}
    adapter._project_with_ladder(raw)

    assert calls == ["cpu"]  # never even attempts a GPU projection that plainly won't fit


def test_ladder_falls_back_to_cpu_projection_on_oom_without_reencoding(monkeypatch):
    """The projection phase OOM'ing must fall back to a CPU PROJECTION only --
    never re-trigger the (expensive) raw Gemma3 encode (the actual
    regression: a projection OOM used to cascade into redoing the whole
    encode+project window on the CPU)."""
    adapter, te, dit = _adapter()
    monkeypatch.setattr(ltx_clip_module.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(ltx_clip_module, "free_vram_gb", lambda dev: 20.0)
    monkeypatch.setattr(ltx_clip_module.torch.cuda, "empty_cache", lambda: None)

    calls = []

    def _project_on(raw, device):
        calls.append(device)
        if device == "cuda:0":
            raise torch.cuda.OutOfMemoryError("simulated projection OOM")
        return torch.zeros(1, 4, 16)

    monkeypatch.setattr(adapter, "_project_on", _project_on)

    raw = {"context": SimpleNamespace(device="cuda:0"), "attention_mask": None}
    result = adapter._project_with_ladder(raw)

    assert calls == ["cuda:0", "cpu"]  # tried GPU, fell back to CPU projection only
    assert te.calls == []              # the raw encode was never touched by the ladder
    assert torch.equal(result, torch.zeros(1, 4, 16))


# --- LTX RAM-ratchet: trim glibc after a cuda projection round-trip --


def test_project_on_trims_host_allocator_after_cuda_round_trip(monkeypatch):
    """`_project_on` moves the connector modules + projection weights to cuda
    and always moves them back to CPU in its `finally` -- those landing-back
    tensors are fresh anonymous allocations (not the original mmap-backed
    checkpoint tensors), and this call must ask glibc to return the freed
    heap, mirroring `NativeModel.move_to`'s existing size-gated trim."""
    adapter, te, dit = _adapter()
    monkeypatch.setattr(adapter, "_move_projection_chain", lambda device: None)
    monkeypatch.setattr(adapter, "_project", lambda raw: torch.zeros(1, 4, 16))

    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager
    calls = []
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))

    raw = {"context": SimpleNamespace(device="cuda:0")}
    adapter._project_on(raw, "cuda:0")

    assert calls, "trim_host_allocator must fire after a cuda projection round-trip"


def test_project_on_skips_trim_for_a_cpu_only_projection(monkeypatch):
    """A projection that never touched the GPU (CPU-only fallback) has nothing
    to reclaim -- the trim must not fire."""
    adapter, te, dit = _adapter()
    monkeypatch.setattr(adapter, "_move_projection_chain", lambda device: None)
    monkeypatch.setattr(adapter, "_project", lambda raw: torch.zeros(1, 4, 16))

    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager
    calls = []
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))

    raw = {"context": SimpleNamespace(device="cpu")}
    adapter._project_on(raw, "cpu")

    assert not calls


def test_ladder_projects_on_cpu_when_raw_encode_itself_landed_on_cpu(monkeypatch):
    """A genuine raw-encode OOM (or no CUDA at all) lands `raw` on CPU -- the
    ladder must not attempt to move anything to the GPU in that case."""
    adapter, te, dit = _adapter()
    monkeypatch.setattr(ltx_clip_module.torch.cuda, "is_available", lambda: True)

    calls = []
    monkeypatch.setattr(
        adapter, "_project_on",
        lambda raw, device: calls.append(device) or torch.zeros(1, 4, 16),
    )

    raw = {"context": SimpleNamespace(device="cpu"), "attention_mask": None}
    adapter._project_with_ladder(raw)

    assert calls == ["cpu"]


# --- "34GB conditioning zombie" regression: te_encoder/dit_module must be
# WEAK views, not strong references that bypass the MODELS cache -----------
#
# Reproduces the actual production incident end to end through the REAL
# caching path (ModelLifecycleManager.acquire/evict_dead_weight +
# PromptEncoderPipe.process, exactly like model_loader/ltx + prompt_encoder
# wire it), using tiny real nn.Module fakes (weakly referenceable, unlike
# tests/pipelines/pipes/latent_upscaler/ltx's own `_te()` stand-ins) so a
# weakref on the TE's actual parameter storage can prove eviction really
# released it. FAILS on the pre-fix code (te_encoder/dit_module stored as
# plain strong attributes pointing directly at NativeModel.module,
# bypassing the wrapper the MODELS cache actually refcounts) and PASSES once
# they're WeakModelRef fields (see ltx_clip.py's module docstring).

import gc
import weakref

import torch.nn as nn

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager
from src.platform.runtime.native.engine import NativeModel


class _RealFakeTE(nn.Module):
    """A real nn.Module (weakly referenceable, unlike types.SimpleNamespace)
    standing in for the ~22GB Gemma3-12B encoder -- small enough to build
    and tear down instantly in a CPU-only test."""

    role = "gemma3_12b"

    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(8, 8))

    def encode(self, texts):
        b = len(texts)
        return {
            "context": torch.ones(b, 4, 8, device="cpu"),
            "attention_mask": torch.ones(b, 4, device="cpu"),
        }


class _RealFakeDit(nn.Module):
    """Stands in for the DiT module -- only `apply_text_conditioning` is
    exercised here; no real embeddings-connector submodules needed."""

    def apply_text_conditioning(self, gemma_output, attention_mask=None, **projections):
        return torch.zeros(gemma_output.shape[0], 4, 16)


def test_te_eviction_actually_frees_the_module_after_prompt_encoder_caches_conditioning():
    get_prompt_embed_cache().clear()

    te_module = _RealFakeTE()
    dit_module = _RealFakeDit()
    te_native = NativeModel(kind="text_encoder", module=te_module)

    models = ModelLifecycleManager()
    models.acquire(key="native/te/fake", fingerprint="fp", loader=lambda: te_native)

    # Mirrors model_loader/ltx/main.py's construction exactly: the raw
    # `.module`, not the NativeModel wrapper, is what LTXClipTextEncoder gets.
    clip = LTXClipTextEncoder(
        te_native.module, dit_module, projections={}, device="cpu", model_fingerprint="fp",
    )

    # Captured BEFORE eviction, exactly like the production diagnostic
    # (`_sample_parameter_weakref` in model_lifecycle/manager.py) does -- the
    # thing that must become collectible is the TE's actual weight storage,
    # not just the NativeModel wrapper around it.
    weight_ref = weakref.ref(te_module.weight)

    # `te_native`/`te_module` are local variables ONLY -- exactly like
    # model_loader/ltx/main.py's own `te_model`, which goes out of scope the
    # instant that pipe's process() returns. After this point the MODELS
    # cache entry (wrapper -> .module) is the ONLY thing besides `clip`
    # itself that can reach the raw module -- an extra strong reference held
    # by this test would mask the bug by making the wrapper's own
    # sys.getrefcount() check see 3+ refs and skip the unload branch
    # entirely, so both must go.
    del te_native
    del te_module

    # Mirrors prompt_encoder/main.py's real process(): p_prompt/n_prompt fed
    # via pipe_input (as an upstream prompt_expander would), MODELS injected
    # so the pipe goes through models.acquire(key="prompt_encoder.conditioning", ...).
    pipe = PromptEncoderPipe({"quantity": 1, "guidance_scale": 7.5})
    pipe_input = PipeInput(input={
        "clip": clip, "MODELS": models, "p_prompt": "a cat", "n_prompt": "",
    })
    result = pipe.process(pipe_input, generation_outputs=lambda _o: None)
    assert result.output["conditioning"], "prompt_encoder must have produced conditioning"

    # Mirrors latent_upscaler/ltx/main.py's `_unload_idle_te`: the standalone
    # upscale pipe explicitly releases the TE mid-generation once
    # prompt_encoder has already produced the conditioning the refine pass
    # needs.
    unloaded = models.evict_dead_weight("native/te/fake")
    assert unloaded is True  # the wrapper-level unload reports success either way

    # `clip` is STILL alive here (exactly like Generation.generate()'s
    # `pipe_outputs` list keeps every pipe's outputs alive for the rest of
    # the run) -- proving eviction works despite that, not because `clip`
    # conveniently went out of scope first.
    gc.collect()

    assert weight_ref() is None, (
        "TE weight tensor is still alive after evict_dead_weight() reported "
        "unloaded=True -- something (LTXClipTextEncoder.te_encoder) still "
        "holds a strong reference to the raw module, defeating cache eviction "
        "(the '34GB conditioning zombie' regression)"
    )
    assert clip.te_encoder is None, "the weak view must report the evicted TE as gone"
    assert clip.dit_module is dit_module, "the DiT was never evicted -- must still be reachable"
