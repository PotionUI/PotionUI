"""Tests for CheckpointLoaderSDXLPipe.fingerprint() — the model-cache key.

Regression coverage for the multi-LoRA bug where the fingerprint only
reflected the *last* LoRA in the list, causing false cache hits (stale LoRA
sets silently reused) and spurious misses.
"""

import pytest

from src.pipelines.pipes.checkpoint_loader.sdxl.main import CheckpointLoaderSDXLPipe


def make_pipe(model_path="models/checkpoints/test.safetensors", loras=None):
    config = CheckpointLoaderSDXLPipe.get_default_config()
    config["model"] = {"file_path": model_path}
    config["loras"] = loras or []
    return CheckpointLoaderSDXLPipe(config)


class TestFingerprint:
    def test_no_loras(self):
        pipe = make_pipe()
        assert pipe.fingerprint() == "models/checkpoints/test.safetensors|[]"

    def test_single_lora_format_unchanged(self):
        # Cache continuity: the single-LoRA fingerprint format must not change.
        pipe = make_pipe(loras=[{"file_path": "models/loras/a.safetensors", "weight": 0.8}])
        assert "a.safetensors" in pipe.fingerprint()
        assert "0.8" in pipe.fingerprint()

    def test_all_loras_in_fingerprint(self):
        pipe = make_pipe(loras=[
            {"file_path": "models/loras/a.safetensors", "weight": 0.8},
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
        ])
        fp = pipe.fingerprint()
        assert "a.safetensors" in fp
        assert "b.safetensors" in fp

    def test_first_lora_weight_changes_fingerprint(self):
        loras = [
            {"file_path": "models/loras/a.safetensors", "weight": 0.8},
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
        ]
        fp_before = make_pipe(loras=loras).fingerprint()
        loras_changed = [dict(loras[0], weight=0.3), loras[1]]
        fp_after = make_pipe(loras=loras_changed).fingerprint()
        assert fp_before != fp_after

    def test_removing_first_lora_changes_fingerprint(self):
        both = make_pipe(loras=[
            {"file_path": "models/loras/a.safetensors", "weight": 0.8},
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
        ]).fingerprint()
        only_last = make_pipe(loras=[
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
        ]).fingerprint()
        assert both != only_last

    def test_lora_order_does_not_matter(self):
        fp_ab = make_pipe(loras=[
            {"file_path": "models/loras/a.safetensors", "weight": 0.8},
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
        ]).fingerprint()
        fp_ba = make_pipe(loras=[
            {"file_path": "models/loras/b.safetensors", "weight": 0.5},
            {"file_path": "models/loras/a.safetensors", "weight": 0.8},
        ]).fingerprint()
        assert fp_ab == fp_ba

    @pytest.mark.parametrize("weight", [0, 0.0, "", None])
    def test_inactive_loras_excluded(self, weight):
        pipe = make_pipe(loras=[{"file_path": "models/loras/a.safetensors", "weight": weight}])
        assert pipe.fingerprint() == make_pipe().fingerprint()

    def test_model_path_in_fingerprint(self):
        fp1 = make_pipe(model_path="models/checkpoints/one.safetensors").fingerprint()
        fp2 = make_pipe(model_path="models/checkpoints/two.safetensors").fingerprint()
        assert fp1 != fp2
