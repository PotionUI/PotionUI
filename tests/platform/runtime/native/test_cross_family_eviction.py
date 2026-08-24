"""Cross-family VRAM eviction on the NativeGenerator lifecycle (no GPU).

The scenario: a prior generation's DiT (e.g. Krea-2, 24.5GB) is left resident on
the GPU by design; the NEXT generation belongs to a different family (Anima) whose
DiT + fp32 decode spike then OOMs on top of the stale resident. These tests assert
that ``NativeGenerator`` evicts the FOREIGN resident before claiming VRAM for
sampling and before the decode spike — and never evicts its OWN DiT/VAE.

CUDA is faked (the generator's device is ``cuda:0`` but ``free_vram_gb`` /
``torch.cuda.*`` are monkeypatched), so no GPU is touched.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan


class _FakeSpec:
    latent_format: dict = {}
    sampling_settings: dict = {}


class _FakeModule:
    """Stand-in nn-ish module; ``decode_image`` presence flips the causal-3D path."""

    def __init__(self, causal3d: bool = False):
        if causal3d:
            self.decode_image = lambda x: x


class _FakeNativeModel:
    """Duck-types NativeModel for the generator's move/offload/estimate calls."""

    def __init__(self, kind, *, gb, causal3d=False, oom_moves=0):
        self.kind = kind
        self.spec = _FakeSpec() if kind == "dit" else None
        self.estimated_vram_gb = gb
        self.compute_dtype = torch.float32
        self.module = _FakeModule(causal3d=causal3d)
        self.device = "cpu"
        self.offloaded = False
        self.moved_to: list[str] = []
        self._oom_moves = oom_moves

    def move_to(self, device):
        self.moved_to.append(str(device))
        if self._oom_moves > 0 and str(device).startswith("cuda"):
            self._oom_moves -= 1
            raise torch.cuda.OutOfMemoryError("fake OOM")
        self.device = str(device)

    def offload(self):
        self.offloaded = True
        self.device = "cpu"


class _ForeignModel:
    """A prior generation's resident model, registered in the manager."""

    def __init__(self):
        self.offloaded = False

    def offload(self):
        self.offloaded = True


def _make_generator(*, dit_gb=4.0, vae_gb=0.3, causal3d=True, oom_moves=0):
    dit = _FakeNativeModel("dit", gb=dit_gb, oom_moves=oom_moves)
    vae = _FakeNativeModel("vae", gb=vae_gb, causal3d=causal3d)
    plan = DevicePlan("cuda:0", "cuda:0", "cuda:0")
    gen = NativeGenerator(dit, te=object(), vae=vae, device_plan=plan)
    return gen, dit, vae


def _fake_cuda(monkeypatch, *, free_gb):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency, "free_vram_gb", lambda dev: free_gb)
    # engine.py imported free_vram_gb by name -> patch that binding too.
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda dev: free_gb)


def test_sample_move_evicts_foreign_resident(monkeypatch):
    # Krea-2 (24.5GB) resident, only 4GB free; Anima's DiT (4GB)+reserve needs ~5GB
    # -> the foreign DiT is evicted before our DiT moves.
    _fake_cuda(monkeypatch, free_gb=4.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    foreign = _ForeignModel()
    mgr.note_resident(foreign, "cuda:0", 24.5)

    gen, dit, vae = _make_generator(dit_gb=4.0)
    gen._move_dit_to_gpu("cuda:0")

    assert foreign.offloaded is True           # prior family's DiT evicted
    assert dit.moved_to == ["cuda:0"]          # our DiT then moved on
    assert dit.offloaded is False
    mgr.clear()


def test_sample_move_oom_retries_after_evicting_all_foreign(monkeypatch):
    # Plenty reported free (so the size test wouldn't evict), but the move OOMs
    # once -> we must evict ALL foreign and retry, then succeed.
    _fake_cuda(monkeypatch, free_gb=30.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    foreign = _ForeignModel()
    mgr.note_resident(foreign, "cuda:0", 24.5)

    gen, dit, vae = _make_generator(dit_gb=4.0, oom_moves=1)
    gen._move_dit_to_gpu("cuda:0")

    assert foreign.offloaded is True           # evicted on the OOM-retry path
    assert dit.moved_to == ["cuda:0", "cuda:0"]  # failed once, retried
    assert dit.device == "cuda:0"
    mgr.clear()


def test_owned_models_are_never_evicted(monkeypatch):
    # The generation's own DiT/VAE, if somehow registered as resident, must be in
    # the exclude set and survive an eviction request.
    _fake_cuda(monkeypatch, free_gb=0.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    gen, dit, vae = _make_generator(dit_gb=4.0)
    # Register our OWN dit+vae as resident, plus a foreign model.
    mgr.note_resident(dit, "cuda:0", 4.0)
    mgr.note_resident(vae, "cuda:0", 0.3)
    foreign = _ForeignModel()
    mgr.note_resident(foreign, "cuda:0", 24.5)

    gen._ensure_room_for(10.0, "cuda:0")

    assert foreign.offloaded is True           # foreign evicted
    assert dit.offloaded is False              # our own DiT untouched
    assert vae.offloaded is False              # our own VAE untouched
    mgr.clear()


def test_ensure_room_missing_estimate_evicts_all_foreign(monkeypatch):
    # need_gb <= 0 (no estimate) -> fall back to evicting ALL foreign residents.
    _fake_cuda(monkeypatch, free_gb=1.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    f1, f2 = _ForeignModel(), _ForeignModel()
    mgr.note_resident(f1, "cuda:0", 24.5)
    mgr.note_resident(f2, "cuda:0", 5.0)

    gen, dit, vae = _make_generator()
    gen._ensure_room_for(0.0, "cuda:0")        # unknown need

    assert f1.offloaded and f2.offloaded
    mgr.clear()


def test_decode_evicts_foreign_before_spike(monkeypatch):
    # A causal-3D decode at 1024²-ish latents has a large fp32 spike; the foreign
    # resident must be evicted before it (this is the exact real-world OOM).
    _fake_cuda(monkeypatch, free_gb=6.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    foreign = _ForeignModel()
    mgr.note_resident(foreign, "cuda:0", 24.5)

    gen, dit, vae = _make_generator(causal3d=True)
    # Placement None -> _resident("dit") True -> dit NOT offloaded by the phase step;
    # the foreign eviction is what frees room. Latents ~ (1,16,1,128,128).
    latents = torch.zeros(1, 16, 1, 128, 128)
    need = gen._decode_need_gb(latents)
    assert need > 15.0                          # spike dominates (causal-3D 1.2MB/px)

    gen._ensure_room_for(need, "cuda:0")
    assert foreign.offloaded is True
    mgr.clear()


def test_no_eviction_on_cpu(monkeypatch):
    # On a CPU device the whole mechanism is inert.
    mgr = residency.get_residency_manager()
    mgr.clear()
    foreign = _ForeignModel()
    mgr.note_resident(foreign, "cuda:0", 24.5)
    gen, dit, vae = _make_generator()
    gen._ensure_room_for(10.0, "cpu")
    assert foreign.offloaded is False           # cuda-only; cpu is a no-op
    mgr.clear()


# --- finding 1: own-DiT offload for a high-res decode spike --------------------


def test_own_dit_offloaded_before_decode_when_spike_too_big(monkeypatch):
    # High-res decode: the fp32 spike (~20GB) won't fit in 3GB free even after the
    # foreign eviction -> our OWN DiT must be offloaded too, regardless of placement.
    _fake_cuda(monkeypatch, free_gb=3.0)
    gen, dit, vae = _make_generator()
    dit.device = "cuda:0"                        # our DiT resident
    gen._free_own_dit_for_decode(need_gb=20.0, device="cuda:0")
    assert dit.offloaded is True


def test_own_dit_kept_before_decode_when_spike_fits(monkeypatch):
    # Low-res decode: the spike fits alongside the resident DiT -> keep it (a
    # needless offload would just cost a reload).
    _fake_cuda(monkeypatch, free_gb=25.0)
    gen, dit, vae = _make_generator()
    dit.device = "cuda:0"
    gen._free_own_dit_for_decode(need_gb=6.0, device="cuda:0")
    assert dit.offloaded is False


def test_free_own_dit_noop_when_already_offloaded(monkeypatch):
    _fake_cuda(monkeypatch, free_gb=1.0)
    gen, dit, vae = _make_generator()
    dit.device = "cpu"                           # already off the GPU
    gen._free_own_dit_for_decode(need_gb=20.0, device="cuda:0")
    assert dit.offloaded is False                # no redundant offload


# --- finding 2: decode OOM-retry TE offload must not crash on a wrapper --------


def test_maybe_offload_te_handles_clip_wrapper_without_to():
    # A pipe-constructed generator's `te` can be a ClipTextEncoder WRAPPER with no
    # .to, only a .encoder — the decode OOM-retry calls _maybe_offload_te, which
    # must not AttributeError (that would turn a recoverable OOM into a crash).
    class _Enc:
        def __init__(self):
            self.moved: list[str] = []

        def to(self, device):
            self.moved.append(str(device))
            return self

    class _Wrapper:
        def __init__(self, enc):
            self.encoder = enc

    enc = _Enc()
    gen, _dit, _vae = _make_generator()
    gen.te = _Wrapper(enc)
    gen._maybe_offload_te()                       # must not raise
    assert enc.moved == ["cpu"]                   # reached the underlying encoder


def test_maybe_offload_te_skips_te_with_neither_to_nor_encoder():
    gen, _dit, _vae = _make_generator()
    gen.te = object()                             # no .to, no .encoder
    gen._maybe_offload_te()                       # no exception, just skipped
