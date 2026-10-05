from __future__ import annotations

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.pipes.generator.video_ltx.ic_lora import read_reference_scales, resolve_reference_scales


def _lora(tmp_path, name, metadata=None):
    path = tmp_path / name
    save_file({"diffusion_model.blocks.0.attn.to_q.lora_A.weight": torch.zeros(1, 2)}, str(path), metadata=metadata)
    return str(path)


def test_reads_both_factors_from_metadata(tmp_path):
    path = _lora(tmp_path, "a.safetensors",
                 {"reference_downscale_factor": "2", "reference_temporal_scale_factor": "4"})
    assert read_reference_scales(path) == (2, 4)


def test_missing_metadata_means_one(tmp_path):
    assert read_reference_scales(_lora(tmp_path, "a.safetensors")) == (1, 1)
    assert read_reference_scales(_lora(tmp_path, "b.safetensors", {"ss_network_dim": "32"})) == (1, 1)


def test_float_spelling_is_accepted_and_garbage_falls_back_to_one(tmp_path):
    assert read_reference_scales(_lora(tmp_path, "a.safetensors", {"reference_downscale_factor": "2.0"})) == (2, 1)
    assert read_reference_scales(_lora(tmp_path, "b.safetensors", {"reference_downscale_factor": "half"})) == (1, 1)


def test_unreadable_file_falls_back_to_one(tmp_path):
    path = tmp_path / "not.safetensors"
    path.write_bytes(b"nope")
    assert read_reference_scales(str(path)) == (1, 1)


def test_resolve_takes_the_one_non_default_factor(tmp_path):
    plain = _lora(tmp_path, "plain.safetensors")
    scaled = _lora(tmp_path, "scaled.safetensors", {"reference_downscale_factor": "2"})
    assert resolve_reference_scales([plain, scaled, plain]) == (2, 1)
    assert resolve_reference_scales([]) == (1, 1)


def test_resolve_rejects_conflicting_factors(tmp_path):
    two = _lora(tmp_path, "two.safetensors", {"reference_downscale_factor": "2"})
    three = _lora(tmp_path, "three.safetensors", {"reference_downscale_factor": "3"})
    with pytest.raises(ValueError, match="disagree"):
        resolve_reference_scales([two, three])
