"""build_model_bundle: turning a processed pipeline's model references into a
real ModelBundleManifestV1, sourced from recorded model digests only - never
by hashing a file on the dispatch path."""

from __future__ import annotations

import unittest
from typing import Dict, Optional

from src.features.models.records import Model
from src.features.remote_execution.model_bundle_builder import (
    ModelBundleResolutionError,
    build_model_bundle,
)
from src.platform.worker_protocol import ProcessedPipeV1


class FakeModelRepository:
    """A `get_by_file_path`-only stand-in - no database, no filesystem.

    Tracks which paths were actually looked up so a test can assert a
    skipped (e.g. zero-weight LoRA) reference never even reached the repo.
    """

    def __init__(self):
        self.by_path: Dict[str, Model] = {}
        self.lookups: list = []

    def register(self, file_path: str, **fields) -> None:
        self.by_path[file_path] = Model(file_path=file_path, **fields)

    def get_by_file_path(self, file_path: str, include_providers: bool = True) -> Optional[Model]:
        self.lookups.append(file_path)
        return self.by_path.get(file_path)


def _pipe(pipe_id: str, config: dict) -> ProcessedPipeV1:
    return ProcessedPipeV1(pipe_id=pipe_id, pipe_type="model_loader", enabled=True, config=config, inputs={})


class TestEmptyPipeline(unittest.TestCase):
    def test_no_model_references_produces_an_empty_deterministic_bundle(self):
        repo = FakeModelRepository()
        bundle = build_model_bundle([_pipe("p1", {"device": "cuda"})], model_repository=repo)

        self.assertEqual(bundle.entries, ())
        self.assertEqual(bundle.total_size_bytes, 0)
        self.assertEqual(repo.lookups, [])

        again = build_model_bundle([_pipe("p1", {"device": "cuda"})], model_repository=repo)
        self.assertEqual(bundle.bundle_digest, again.bundle_digest)


class TestResolvedEntries(unittest.TestCase):
    def setUp(self):
        self.repo = FakeModelRepository()
        self.repo.register(
            "/models/checkpoints/dit.safetensors", id="m-dit", filename="dit.safetensors",
            model_type="diffusion_model", sha256="a" * 64, file_size=1000,
        )
        self.repo.register(
            "/models/vae/vae.safetensors", id="m-vae", filename="vae.safetensors",
            model_type="vae", sha256="b" * 64, file_size=200,
        )

    def test_each_referenced_file_becomes_an_entry_with_role_and_digest(self):
        pipe = _pipe("p1", {
            "diffusion_model": {"file_path": "/models/checkpoints/dit.safetensors", "name": "dit"},
            "vae": {"file_path": "/models/vae/vae.safetensors", "name": "vae"},
        })
        bundle = build_model_bundle([pipe], model_repository=self.repo)

        self.assertEqual(len(bundle.entries), 2)
        by_id = {e.logical_id: e for e in bundle.entries}
        dit = by_id["diffusion_model/dit.safetensors"]
        self.assertEqual(dit.role, "diffusion_model")
        self.assertEqual(dit.relative_path, "diffusion_models/dit.safetensors")
        self.assertEqual(dit.digest.hex, "a" * 64)
        self.assertEqual(dit.size_bytes, 1000)
        vae = by_id["vae/vae.safetensors"]
        self.assertEqual(vae.relative_path, "vae/vae.safetensors")
        self.assertEqual(bundle.total_size_bytes, 1200)

    def test_the_same_file_referenced_twice_dedups_to_one_entry(self):
        pipe_a = _pipe("p1", {"diffusion_model": {"file_path": "/models/checkpoints/dit.safetensors"}})
        pipe_b = _pipe("p2", {"unet": {"file_path": "/models/checkpoints/dit.safetensors"}})
        bundle = build_model_bundle([pipe_a, pipe_b], model_repository=self.repo)

        self.assertEqual(len(bundle.entries), 1)
        self.assertEqual(self.repo.lookups, ["/models/checkpoints/dit.safetensors"])

    def test_output_is_deterministic_regardless_of_pipe_order(self):
        pipe = _pipe("p1", {
            "diffusion_model": {"file_path": "/models/checkpoints/dit.safetensors"},
            "vae": {"file_path": "/models/vae/vae.safetensors"},
        })
        forward = build_model_bundle([pipe], model_repository=self.repo)

        reordered_pipe = _pipe("p1", {
            "vae": {"file_path": "/models/vae/vae.safetensors"},
            "diffusion_model": {"file_path": "/models/checkpoints/dit.safetensors"},
        })
        backward = build_model_bundle([reordered_pipe], model_repository=self.repo)

        self.assertEqual(forward.bundle_digest, backward.bundle_digest)
        self.assertEqual([e.logical_id for e in forward.entries], [e.logical_id for e in backward.entries])


class TestLoraWeightFiltering(unittest.TestCase):
    def test_a_zero_weight_lora_is_skipped_without_even_looking_it_up(self):
        repo = FakeModelRepository()
        pipe = _pipe("p1", {
            "loras": [{"file_path": "/models/loras/disabled.safetensors", "weight": 0.0}],
        })
        bundle = build_model_bundle([pipe], model_repository=repo)

        self.assertEqual(bundle.entries, ())
        self.assertEqual(repo.lookups, [])

    def test_a_nonzero_weight_lora_is_resolved_normally(self):
        repo = FakeModelRepository()
        repo.register(
            "/models/loras/active.safetensors", id="m-lora", filename="active.safetensors",
            model_type="lora", sha256="c" * 64, file_size=50,
        )
        pipe = _pipe("p1", {
            "loras": [{"file_path": "/models/loras/active.safetensors", "weight": 0.8}],
        })
        bundle = build_model_bundle([pipe], model_repository=repo)

        self.assertEqual(len(bundle.entries), 1)
        self.assertEqual(bundle.entries[0].role, "lora")


class TestMissingDigest(unittest.TestCase):
    def test_an_unindexed_model_path_fails_loudly_rather_than_hashing(self):
        repo = FakeModelRepository()
        pipe = _pipe("p1", {"diffusion_model": {"file_path": "/models/checkpoints/unknown.safetensors"}})

        with self.assertRaises(ModelBundleResolutionError) as ctx:
            build_model_bundle([pipe], model_repository=repo)
        self.assertIn("not indexed", str(ctx.exception))

    def test_an_indexed_model_with_no_recorded_digest_fails_loudly(self):
        repo = FakeModelRepository()
        repo.register(
            "/models/checkpoints/no_digest.safetensors", id="m-nd", filename="no_digest.safetensors",
            model_type="checkpoint", sha256=None, file_size=500,
        )
        pipe = _pipe("p1", {"diffusion_model": {"file_path": "/models/checkpoints/no_digest.safetensors"}})

        with self.assertRaises(ModelBundleResolutionError) as ctx:
            build_model_bundle([pipe], model_repository=repo)
        self.assertIn("no recorded content digest", str(ctx.exception))

    def test_a_directory_layout_model_fails_loudly_rather_than_a_wrong_entry(self):
        repo = FakeModelRepository()
        repo.register(
            "/models/llm/gemma3", id="m-dir", filename="gemma3",
            model_type="llm", sha256="d" * 64, file_size=9999, is_directory=True,
        )
        pipe = _pipe("p1", {"text_encoder": {"file_path": "/models/llm/gemma3"}})

        with self.assertRaises(ModelBundleResolutionError) as ctx:
            build_model_bundle([pipe], model_repository=repo)
        self.assertIn("directory", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
