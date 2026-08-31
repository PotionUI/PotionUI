"""build_model_bundle: turning a processed pipeline's model references into a
real ModelBundleManifestV1."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from typing import Dict, Optional

from src.features.models.records import Model
from src.features.remote_execution.model_bundle_builder import (
    ModelBundleResolutionError,
    build_model_bundle,
)
from src.platform.worker_protocol import ProcessedPipeV1


class FakeModelRepository:
    """A `get_by_file_path`/`update_digest` stand-in - no database, no filesystem.

    `rows` is the source of truth (keyed by file_path); `get_by_file_path`
    hands back a fresh `Model` each call, mirroring a real repository read.
    `digest_writes` records every `update_digest` call so a test can assert
    a second bundle build does not re-hash an already-digested model.
    """

    def __init__(self):
        self.rows: Dict[str, dict] = {}
        self.lookups: list = []
        self.digest_writes: list = []

    def register(self, file_path: str, **fields) -> None:
        self.rows[file_path] = {"file_path": file_path, **fields}

    def get_by_file_path(self, file_path: str, include_providers: bool = True) -> Optional[Model]:
        self.lookups.append(file_path)
        fields = self.rows.get(file_path)
        return Model(**fields) if fields is not None else None

    def update_digest(self, model_id: str, *, sha256: str, file_size: int) -> bool:
        self.digest_writes.append((model_id, sha256, file_size))
        for fields in self.rows.values():
            if fields.get("id") == model_id:
                fields["sha256"] = sha256
                fields["file_size"] = file_size
                return True
        return False


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

    def test_a_missing_digest_is_hashed_and_persisted_and_dispatch_proceeds(self):
        fd, path = tempfile.mkstemp()
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"checkpoint bytes")
        expected_digest = hashlib.sha256(b"checkpoint bytes").hexdigest()

        repo = FakeModelRepository()
        repo.register(
            path, id="m-nd", filename="no_digest.safetensors",
            model_type="checkpoint", sha256=None, file_size=None,
        )
        pipe = _pipe("p1", {"diffusion_model": {"file_path": path}})

        bundle = build_model_bundle([pipe], model_repository=repo)

        self.assertEqual(len(bundle.entries), 1)
        self.assertEqual(bundle.entries[0].digest.hex, expected_digest)
        self.assertEqual(bundle.entries[0].size_bytes, len(b"checkpoint bytes"))
        self.assertEqual(repo.digest_writes, [("m-nd", expected_digest, len(b"checkpoint bytes"))])

    def test_a_second_build_does_not_rehash_an_already_digested_model(self):
        fd, path = tempfile.mkstemp()
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"checkpoint bytes")

        repo = FakeModelRepository()
        repo.register(
            path, id="m-nd", filename="no_digest.safetensors",
            model_type="checkpoint", sha256=None, file_size=None,
        )
        pipe = _pipe("p1", {"diffusion_model": {"file_path": path}})

        build_model_bundle([pipe], model_repository=repo)
        build_model_bundle([pipe], model_repository=repo)

        self.assertEqual(len(repo.digest_writes), 1)

    def test_a_missing_digest_for_a_file_absent_on_disk_still_refuses(self):
        repo = FakeModelRepository()
        repo.register(
            "/models/checkpoints/gone.safetensors", id="m-gone", filename="gone.safetensors",
            model_type="checkpoint", sha256=None, file_size=None,
        )
        pipe = _pipe("p1", {"diffusion_model": {"file_path": "/models/checkpoints/gone.safetensors"}})

        with self.assertRaises(ModelBundleResolutionError) as ctx:
            build_model_bundle([pipe], model_repository=repo)
        self.assertIn("missing on disk", str(ctx.exception))
        self.assertEqual(repo.digest_writes, [])

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
