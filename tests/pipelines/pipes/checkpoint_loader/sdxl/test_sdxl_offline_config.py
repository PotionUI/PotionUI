"""Proof that the vendored SDXL base pipeline config resolves fully offline.

`StableDiffusionXLKDiffusionPipeline.from_single_file()` needs a diffusers-format
config (model_index.json + per-component config.json + tokenizer vocab) to know
how to shape each component before loading the checkpoint's tensors into it.
Without an explicit `config=`, diffusers infers
`stabilityai/stable-diffusion-xl-base-1.0` and fetches this same bundle from the
Hugging Face Hub — which raises `LocalEntryNotFoundError` under the native
engine's default `HF_HUB_OFFLINE=1` (see `text_encoders/tokenization.py`) on any
machine without a warm HF cache.

These tests exercise the exact code path diffusers'
`FromSingleFileMixin.from_single_file` takes for a local `config=` directory
(`diffusers/loaders/single_file.py`, the `if not os.path.isdir(...)` branch is
skipped because our config *is* a directory) and for each sub-component
(`load_single_file_sub_model`, `diffusers/loaders/single_file.py`), under a
forced-offline, cold-cache environment, and assert the Hub is never touched.

We cannot load real weights here (no checkpoint file, no GPU) — this proves
config *resolution*, which is the part that broke on fresh installs. The
downstream weight conversion (`convert_sdxl_unet_checkpoint` etc.) is unchanged
by this fix and is exercised by the existing SDXL golden/generator tests.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import SDXL_BASE_PIPELINE_CONFIG


@pytest.fixture
def cold_offline_hf_env(tmp_path, monkeypatch):
    """Force HF_HUB_OFFLINE with a brand-new, empty HF cache directory.

    Mirrors a fresh install: no warm cache, hub access forced off. If any code
    path under test tries to actually reach the network it will raise
    LocalEntryNotFoundError (offline) instead of silently succeeding because a
    developer's real `~/.cache/huggingface` happened to have this repo cached.
    """
    cache_dir = tmp_path / "hf_home"
    cache_dir.mkdir()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(cache_dir))
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir / "hub"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return cache_dir


class TestVendoredConfigAssets:
    """Sanity checks on the vendored directory itself (no network, no diffusers)."""

    def test_config_dir_exists_and_is_local(self):
        assert os.path.isdir(SDXL_BASE_PIPELINE_CONFIG), (
            "from_single_file() only skips the hub fetch when config= is a real "
            "local directory (diffusers/loaders/single_file.py: "
            "`if not os.path.isdir(default_pretrained_model_config_name)`)"
        )
        assert os.path.isabs(SDXL_BASE_PIPELINE_CONFIG)

    def test_no_weight_files_vendored(self):
        weight_suffixes = {".safetensors", ".bin", ".pt", ".pth", ".ckpt"}
        offenders = [
            p for p in Path(SDXL_BASE_PIPELINE_CONFIG).rglob("*")
            if p.is_file() and p.suffix.lower() in weight_suffixes
        ]
        assert offenders == [], f"vendored config must never carry weights: {offenders}"

    def test_total_size_is_kb_scale(self):
        total = sum(p.stat().st_size for p in Path(SDXL_BASE_PIPELINE_CONFIG).rglob("*") if p.is_file())
        # Config JSON + CLIP BPE vocab for two tokenizers is a few MB; anything
        # in the hundreds-of-MB range would mean a weight file snuck in.
        assert total < 10 * 1024 * 1024, f"vendored config unexpectedly large: {total} bytes"

    def test_required_component_subfolders_present(self):
        base = Path(SDXL_BASE_PIPELINE_CONFIG)
        assert (base / "model_index.json").is_file()
        for component, filename in [
            ("scheduler", "scheduler_config.json"),
            ("text_encoder", "config.json"),
            ("text_encoder_2", "config.json"),
            ("tokenizer", "vocab.json"),
            ("tokenizer", "merges.txt"),
            ("tokenizer_2", "vocab.json"),
            ("tokenizer_2", "merges.txt"),
            ("unet", "config.json"),
            ("vae", "config.json"),
        ]:
            assert (base / component / filename).is_file(), f"missing {component}/{filename}"

    def test_model_index_class_name_matches_our_pipeline(self):
        import json

        with open(Path(SDXL_BASE_PIPELINE_CONFIG) / "model_index.json") as f:
            config = json.load(f)
        assert config["_class_name"] == "StableDiffusionXLKDiffusionPipeline"


class TestOfflineConfigResolution:
    """Exercise the actual diffusers code path with the hub fetch trapped."""

    def test_pipeline_load_config_never_touches_hub(self, cold_offline_hf_env):
        """Mirrors single_file.py's local-dir branch: `pipeline_class.load_config(cached_model_config_path)`."""
        from src.pipelines.pipes.generator.sdxl.pipeline import StableDiffusionXLKDiffusionPipeline

        with patch("huggingface_hub.snapshot_download") as mock_snapshot:
            config_dict = StableDiffusionXLKDiffusionPipeline.load_config(SDXL_BASE_PIPELINE_CONFIG)

        mock_snapshot.assert_not_called()
        for expected in ("vae", "text_encoder", "text_encoder_2", "tokenizer", "tokenizer_2", "unet", "scheduler"):
            assert expected in config_dict, f"model_index.json missing expected component: {expected}"

    def test_tokenizers_load_offline(self, cold_offline_hf_env):
        """`load_single_file_sub_model` loads tokenizer/tokenizer_2 via
        `CLIPTokenizer.from_pretrained(cached_model_config_path, subfolder=name,
        local_files_only=local_files_only)` — note sdxl_model.py never passes
        `local_files_only=True` explicitly, so this must work even with the
        default `local_files_only=False` because the path is a local directory,
        not a repo id.
        """
        from transformers import CLIPTokenizer

        with patch("huggingface_hub.snapshot_download") as mock_snapshot:
            tokenizer = CLIPTokenizer.from_pretrained(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="tokenizer", local_files_only=False
            )
            tokenizer_2 = CLIPTokenizer.from_pretrained(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="tokenizer_2", local_files_only=False
            )

        mock_snapshot.assert_not_called()
        assert tokenizer.vocab_size == 49408
        assert tokenizer_2.vocab_size == 49408

    def test_component_configs_load_offline(self, cold_offline_hf_env):
        """unet/vae/scheduler config.json resolution — the shape hint
        from_single_file() uses before converting checkpoint tensors into it.
        """
        from diffusers import UNet2DConditionModel, AutoencoderKL, EulerDiscreteScheduler

        with patch("huggingface_hub.snapshot_download") as mock_snapshot:
            unet_config, _ = UNet2DConditionModel.load_config(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="unet", return_unused_kwargs=True
            )
            vae_config, _ = AutoencoderKL.load_config(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="vae", return_unused_kwargs=True
            )
            scheduler = EulerDiscreteScheduler.from_pretrained(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="scheduler", local_files_only=False
            )

        mock_snapshot.assert_not_called()
        assert unet_config["in_channels"] == 4
        assert vae_config["latent_channels"] == 4
        assert scheduler.config.beta_start == 0.00085

    def test_text_encoder_configs_load_offline(self, cold_offline_hf_env):
        """CLIPTextConfig / CLIPTextConfig(withProjection) resolution for
        text_encoder / text_encoder_2 — the shape hint
        create_diffusers_clip_model_from_ldm() uses.
        """
        from transformers import CLIPTextConfig

        with patch("huggingface_hub.snapshot_download") as mock_snapshot:
            te1 = CLIPTextConfig.from_pretrained(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="text_encoder", local_files_only=False
            )
            te2 = CLIPTextConfig.from_pretrained(
                SDXL_BASE_PIPELINE_CONFIG, subfolder="text_encoder_2", local_files_only=False
            )

        mock_snapshot.assert_not_called()
        assert te1.hidden_size == 768   # CLIP-L
        assert te2.hidden_size == 1280  # OpenCLIP ViT-bigG

    def test_sdxl_model_passes_config_unconditionally(self):
        """Lock the wiring in sdxl_model.py: both from_single_file() call sites
        (load(), load_with_controlnet()) must pass config=SDXL_BASE_PIPELINE_CONFIG
        unconditionally — not gated on cache warmth, network flags, etc. — so
        behavior is identical on warm-cache and fresh-install machines.
        """
        import inspect
        from src.pipelines.pipes.checkpoint_loader.sdxl import sdxl_model

        source = inspect.getsource(sdxl_model)
        call_sites = source.count("StableDiffusionXLKDiffusionPipeline.from_single_file(")
        assert call_sites == 2, "expected exactly the load() and load_with_controlnet() call sites"
        assert source.count("config=SDXL_BASE_PIPELINE_CONFIG") == 2
