import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.availability_repository import ModelAvailabilityRepository
from src.features.models.availability_records import ModelAvailability
from src.features.models.repository import ModelRepository
from src.features.models.records import Model
from src.features.backends.repository import BackendRepository
from src.features.backends.records import Backend


class TestModelAvailabilityRepositoryStatsForBackend(PersistenceTestBase):
    """stats_for_backend() aggregates model_availability per backend - the data
    source for the Backends detail pane's Stats tab."""

    def setUp(self):
        super().setUp()
        self.repo = ModelAvailabilityRepository()

        # Same repatching TestModelRepository does: each of these modules
        # imports `db` at module load time, so without repatching them here
        # they stay bound to whatever `db` was live on first import, not this
        # test's fresh temp database.
        import src.features.models.availability_repository as availability_repository_module
        availability_repository_module.db = self.db

        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db

        import src.features.backends.repository as backend_repository_module
        backend_repository_module.db = self.db

        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.model_repo = ModelRepository()
        self.backend_repo = BackendRepository()

    def _create_backend(self, name: str = "Native") -> Backend:
        return self.backend_repo.create(
            Backend(id="", name=name, engine="native", driver="native.local", enabled=True, is_default=False, config={})
        )

    def _create_model(self, file_path: str, sha256: str) -> Model:
        model = Model(
            filename=os.path.basename(file_path),
            file_path=file_path,
            file_size=1024,
            sha256=sha256,
            model_type="checkpoint",
        )
        return self.model_repo.create(model)

    def test_no_rows_returns_zeroed_stats(self):
        backend = self._create_backend()

        stats = self.repo.stats_for_backend(backend.id)

        self.assertEqual(stats["indexed_models"], 0)
        self.assertEqual(stats["total_size_bytes"], 0)
        self.assertIsNone(stats["last_indexed_at"])

    def test_aggregates_count_and_size_for_the_backend(self):
        backend = self._create_backend()
        model_a = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)
        model_b = self._create_model("/models/checkpoints/b.safetensors", "b" * 64)

        self.repo.upsert(ModelAvailability(id="", model_id=model_a.id, backend_id=backend.id, ref="a.safetensors", size=1000))
        self.repo.upsert(ModelAvailability(id="", model_id=model_b.id, backend_id=backend.id, ref="b.safetensors", size=2500))

        stats = self.repo.stats_for_backend(backend.id)

        self.assertEqual(stats["indexed_models"], 2)
        self.assertEqual(stats["total_size_bytes"], 3500)
        self.assertIsNotNone(stats["last_indexed_at"])

    def test_only_counts_the_requested_backend(self):
        backend_a = self._create_backend("A")
        backend_b = self._create_backend("B")
        model = self._create_model("/models/checkpoints/shared.safetensors", "c" * 64)

        self.repo.upsert(ModelAvailability(id="", model_id=model.id, backend_id=backend_a.id, ref="shared.safetensors", size=500))
        self.repo.upsert(ModelAvailability(id="", model_id=model.id, backend_id=backend_b.id, ref="shared.safetensors", size=700))

        stats_a = self.repo.stats_for_backend(backend_a.id)
        stats_b = self.repo.stats_for_backend(backend_b.id)

        self.assertEqual(stats_a["indexed_models"], 1)
        self.assertEqual(stats_a["total_size_bytes"], 500)
        self.assertEqual(stats_b["indexed_models"], 1)
        self.assertEqual(stats_b["total_size_bytes"], 700)

    def test_missing_size_does_not_break_the_sum(self):
        backend = self._create_backend()
        model = self._create_model("/models/checkpoints/unsized.safetensors", "d" * 64)

        self.repo.upsert(ModelAvailability(id="", model_id=model.id, backend_id=backend.id, ref="unsized.safetensors", size=None))

        stats = self.repo.stats_for_backend(backend.id)

        self.assertEqual(stats["indexed_models"], 1)
        self.assertEqual(stats["total_size_bytes"], 0)


class TestModelAvailabilityRepositoryDigest(PersistenceTestBase):
    """`digest` round-trips through upsert/get, and a `confidence = 'conflict'` row
    is excluded from the routing-facing queries - migration 110."""

    def setUp(self):
        super().setUp()
        self.repo = ModelAvailabilityRepository()

        import src.features.models.availability_repository as availability_repository_module
        availability_repository_module.db = self.db

        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db

        import src.features.backends.repository as backend_repository_module
        backend_repository_module.db = self.db

        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.model_repo = ModelRepository()
        self.backend_repo = BackendRepository()

    def _create_backend(self, name: str = "Native") -> Backend:
        return self.backend_repo.create(
            Backend(id="", name=name, engine="native", driver="native.local", enabled=True, is_default=False, config={})
        )

    def _create_model(self, file_path: str, sha256: str) -> Model:
        model = Model(
            filename=os.path.basename(file_path),
            file_path=file_path,
            file_size=1024,
            sha256=sha256,
            model_type="checkpoint",
        )
        return self.model_repo.create(model)

    def test_digest_round_trips_through_upsert_and_get(self):
        backend = self._create_backend()
        model = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)

        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=backend.id, ref="a.safetensors",
            size=1024, confidence="verified", digest="a" * 64,
        ))

        row = self.repo.get(model.id, backend.id)
        self.assertEqual(row.digest, "a" * 64)
        self.assertEqual(row.confidence, "verified")

    def test_reupsert_overwrites_the_previous_digest(self):
        backend = self._create_backend()
        model = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)

        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=backend.id, ref="a.safetensors",
            digest="a" * 64,
        ))
        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=backend.id, ref="a.safetensors",
            digest="b" * 64, confidence="conflict",
        ))

        row = self.repo.get(model.id, backend.id)
        self.assertEqual(row.digest, "b" * 64)
        self.assertEqual(row.confidence, "conflict")

    def test_conflicted_backend_is_excluded_from_backends_holding(self):
        """The routing-facing query: a conflicted claim must not count as "holds"."""
        good = self._create_backend("Good")
        conflicted = self._create_backend("Conflicted")
        model = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)

        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=good.id, ref="a.safetensors",
            confidence="verified", digest="a" * 64,
        ))
        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=conflicted.id, ref="a.safetensors",
            confidence="conflict", digest="b" * 64,
        ))

        holders = self.repo.backends_holding([model.id])

        self.assertEqual(holders, {good.id})

    def test_conflicted_backend_is_excluded_from_badges(self):
        conflicted = self._create_backend("Conflicted")
        model = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)

        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=conflicted.id, ref="a.safetensors",
            confidence="conflict", digest="b" * 64,
        ))

        by_model = self.repo.backend_ids_by_model([model.id])

        self.assertEqual(by_model.get(model.id, []), [])

    def test_conflicts_for_returns_only_conflicted_rows_within_scope(self):
        backend_a = self._create_backend("A")
        backend_b = self._create_backend("B")
        model = self._create_model("/models/checkpoints/a.safetensors", "a" * 64)

        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=backend_a.id, ref="a.safetensors",
            confidence="verified", digest="a" * 64,
        ))
        self.repo.upsert(ModelAvailability(
            id="", model_id=model.id, backend_id=backend_b.id, ref="a.safetensors",
            confidence="conflict", digest="b" * 64,
        ))

        conflicts = self.repo.conflicts_for([model.id], [backend_a.id, backend_b.id])

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].backend_id, backend_b.id)
