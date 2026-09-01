"""Tests for the generation export/import bundle (GenerationHistoryArchive.export_bundle /
import_bundle) - the portable "reproduce this generation on another PotionUI instance" pair.

Two groups:

- `TestImportBundleValidation` - structural/size rejections that never touch a
  repository, exercised against Mocks (mirrors test_export.py's style).
- `TestExportImportRoundTrip` - a real (temp-file) database, since
  generation_parameter_repo/generation_model_repo/generation_segment_repo/
  models.repository are module-level singletons bound to `src.platform.database`'s
  `db` at import time, not constructor-injected (see test_history_query_provenance.py).
"""

import io
import json
import sys
import os
import zipfile

import pytest
from unittest.mock import Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.features.generation import history_archive as history_archive_module
from src.features.generation.history_archive import GenerationHistoryArchive
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.exceptions import GenerationBundleImportError
from src.features.generation.exceptions import GenerationNotFoundException


def _valid_document(**generation_overrides):
    generation = {
        "preset_id": None,
        "mode": "txt2img",
        "form_data": {"prompt": "a cat", "seed": 1},
    }
    generation.update(generation_overrides)
    return {
        "schema": "potionui.generation",
        "schema_version": 1,
        "kind": "generation",
        "exported_at": "2026-01-01T00:00:00+00:00",
        "generation": generation,
        "models": [],
        "outputs": [],
    }


def _zip_of(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestImportBundleValidation:
    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()
        self.query = GenerationHistoryQuery(generation_repo=self.mock_repo)
        self.archive = GenerationHistoryArchive(
            self.mock_repo, self.mock_file_service, self.mock_plugins, self.query
        )

    def test_accepts_bare_json_with_no_models_or_preset(self):
        content = json.dumps(_valid_document()).encode("utf-8")

        result = self.archive.import_bundle(content)

        assert result["reuse"]["form_data"] == {"prompt": "a cat", "seed": 1}
        assert result["reuse"]["mode"] == "txt2img"
        assert result["preset_available"] is False
        assert result["warnings"] == []

    def test_accepts_zip_bundle_ignoring_other_entries(self):
        content = _zip_of({
            "generation.json": json.dumps(_valid_document()),
            "outputs/0.png": b"not-actually-a-png",
        })

        result = self.archive.import_bundle(content)

        assert result["reuse"]["form_data"]["prompt"] == "a cat"

    def test_rejects_wrong_schema(self):
        doc = _valid_document()
        doc["schema"] = "someone-elses-format"

        with pytest.raises(GenerationBundleImportError, match="Not a PotionUI generation export"):
            self.archive.import_bundle(json.dumps(doc).encode("utf-8"))

    def test_rejects_wrong_schema_version(self):
        doc = _valid_document()
        doc["schema_version"] = 2

        with pytest.raises(GenerationBundleImportError, match="schema_version"):
            self.archive.import_bundle(json.dumps(doc).encode("utf-8"))

    def test_rejects_missing_form_data(self):
        doc = _valid_document()
        del doc["generation"]["form_data"]

        with pytest.raises(GenerationBundleImportError, match="form_data"):
            self.archive.import_bundle(json.dumps(doc).encode("utf-8"))

    def test_rejects_missing_mode(self):
        doc = _valid_document()
        del doc["generation"]["mode"]

        with pytest.raises(GenerationBundleImportError, match="mode"):
            self.archive.import_bundle(json.dumps(doc).encode("utf-8"))

    def test_rejects_non_dict_document(self):
        with pytest.raises(GenerationBundleImportError, match="JSON object"):
            self.archive.import_bundle(json.dumps([1, 2, 3]).encode("utf-8"))

    def test_rejects_invalid_json(self):
        with pytest.raises(GenerationBundleImportError, match="not valid JSON"):
            self.archive.import_bundle(b"{not json")

    def test_rejects_oversized_upload(self, monkeypatch):
        monkeypatch.setattr(history_archive_module, "_MAX_BUNDLE_UPLOAD_BYTES", 10)

        with pytest.raises(GenerationBundleImportError, match="maximum upload size"):
            self.archive.import_bundle(b"x" * 11)

    def test_rejects_zip_with_too_many_entries(self, monkeypatch):
        monkeypatch.setattr(history_archive_module, "_MAX_BUNDLE_ZIP_ENTRIES", 1)
        content = _zip_of({
            "generation.json": json.dumps(_valid_document()),
            "outputs/0.png": b"x",
        })

        with pytest.raises(GenerationBundleImportError, match="too many entries"):
            self.archive.import_bundle(content)

    def test_rejects_zip_missing_generation_json(self):
        content = _zip_of({"outputs/0.png": b"x"})

        with pytest.raises(GenerationBundleImportError, match="missing generation.json"):
            self.archive.import_bundle(content)

    def test_rejects_oversized_manifest_in_zip(self, monkeypatch):
        monkeypatch.setattr(history_archive_module, "_MAX_BUNDLE_MANIFEST_BYTES", 5)
        content = _zip_of({"generation.json": json.dumps(_valid_document())})

        with pytest.raises(GenerationBundleImportError, match="too large"):
            self.archive.import_bundle(content)

    def test_rejects_hostile_non_zip_binary_with_pk_prefix(self):
        with pytest.raises(GenerationBundleImportError, match="not a valid zip or JSON bundle"):
            self.archive.import_bundle(b"PK\x03\x04garbage-not-a-real-zip")

    def test_export_bundle_raises_when_not_owned(self):
        self.mock_repo.get_by_id.return_value = None  # not found / not owned

        with pytest.raises(GenerationNotFoundException):
            self.archive.export_bundle("gen-missing", "user-1")


class _StubPresetNameResolver:
    def __init__(self, names: dict):
        self._names = names

    def name_map(self):
        return dict(self._names)


class TestExportImportRoundTrip:
    """Real-DB round trip: export_bundle's envelope feeds straight into
    import_bundle. Uses PersistenceTestBase, which redirects the canonical
    `src.platform.database.database.db` name every repository resolves at
    call time."""

    @pytest.fixture(autouse=True)
    def _base(self, tmp_path):
        from tests.fixtures.persistence_base import PersistenceTestBase

        class _Harness(PersistenceTestBase):
            def runTest(self):
                pass

        self.harness = _Harness()
        self.harness.setUp()
        self.db = self.harness.db

        from src.features.generation.repository import GenerationRepository
        from src.features.generation.parameter_repository import generation_parameter_repo
        from src.features.generation.model_repository import generation_model_repo
        from src.features.models.repository import model_repo

        self.generation_repo = GenerationRepository()
        self.param_repo = generation_parameter_repo
        self.gen_model_repo = generation_model_repo
        self.model_repo = model_repo

        self.user_id = self.harness.create_test_user()

        yield

        self.harness.tearDown()

    def _make_archive(self, preset_names=None):
        from src.platform.plugins import PluginRegistry
        query = GenerationHistoryQuery(
            generation_repo=self.generation_repo,
            preset_name_resolver=_StubPresetNameResolver(preset_names or {}),
        )
        mock_file_service = Mock()
        mock_file_service.generation_exists.return_value = False
        return GenerationHistoryArchive(
            self.generation_repo, mock_file_service, Mock(spec=PluginRegistry), query
        )

    def _create_generation_with_seed_batch(self, seeds):
        from src.features.generation.records import Generation, File
        from src.platform.util.ids import generate_ulid

        gen_id = generate_ulid()
        self.generation_repo.create(Generation(
            id=gen_id,
            preset_id="preset-1",
            form_data={"prompt": "a cat", "seed": -1},
            user_id=self.user_id,
            mode="txt2img",
            form_name="default",
        ))
        for idx in range(len(seeds)):
            self.generation_repo.add_file(gen_id, File(
                file_path=f"generations/2026-01-01/{gen_id}/{idx}.png",
                file_type="IMAGE",
                user_id=self.user_id,
                is_final=True,
                width=512,
                height=512,
            ))
        self.param_repo.create_batch(gen_id, "seed", seeds)
        return gen_id

    def _add_model(self, gen_id, filename="checkpoint.safetensors", sha256="abc123"):
        from src.features.models.records import Model
        from src.platform.util.ids import generate_ulid

        with self.db.get_cursor() as cursor:
            model_id = generate_ulid()
            cursor.execute("""
                INSERT INTO models (id, filename, file_path, file_size, model_type, sha256)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (model_id, filename, f"/models/{filename}", 1024, "checkpoint", sha256))
        self.gen_model_repo.create_batch(gen_id, [model_id])
        return model_id

    def _extract_envelope(self, zip_bytes) -> dict:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        return json.loads(zf.read("generation.json"))

    def test_export_pins_form_data_seed_to_first_output(self):
        gen_id = self._create_generation_with_seed_batch([42, 43])
        archive = self._make_archive(preset_names={"preset-1": "My Preset"})

        zip_bytes, filename = archive.export_bundle(gen_id, self.user_id)

        assert filename == f"potionui-generation-{gen_id}.zip"
        envelope = self._extract_envelope(zip_bytes)
        assert envelope["schema"] == "potionui.generation"
        assert envelope["generation"]["form_data"]["seed"] == 42
        assert envelope["generation"]["parameters"] == [{"seed": 42}, {"seed": 43}]
        assert envelope["generation"]["preset_name"] == "My Preset"
        assert len(envelope["outputs"]) == 2

    def test_export_includes_model_with_digest(self):
        gen_id = self._create_generation_with_seed_batch([7])
        self._add_model(gen_id, filename="checkpoint.safetensors", sha256="digest-1")
        archive = self._make_archive()

        zip_bytes, _ = archive.export_bundle(gen_id, self.user_id)

        envelope = self._extract_envelope(zip_bytes)
        assert len(envelope["models"]) == 1
        assert envelope["models"][0]["filename"] == "checkpoint.safetensors"
        assert envelope["models"][0]["sha256"] == "digest-1"
        assert envelope["models"][0]["model_type"] == "checkpoint"

    def test_import_of_exported_bundle_round_trips_reuse_payload(self):
        # A real cross-instance import can't be exercised against a single test
        # database (the model row IS the "local install" here), so this checks
        # the case where the model the export names is already present.
        gen_id = self._create_generation_with_seed_batch([99])
        self._add_model(gen_id, filename="checkpoint.safetensors", sha256="digest-1")
        export_archive = self._make_archive(preset_names={"preset-1": "My Preset"})
        zip_bytes, _ = export_archive.export_bundle(gen_id, self.user_id)

        import_archive = self._make_archive(preset_names={"preset-1": "My Preset"})
        result = import_archive.import_bundle(zip_bytes)

        assert result["reuse"]["preset_id"] == "preset-1"
        assert result["reuse"]["form_data"]["seed"] == 99
        assert result["preset_available"] is True
        assert result["warnings"] == []

    def test_import_warns_when_preset_not_installed(self):
        gen_id = self._create_generation_with_seed_batch([1])
        export_archive = self._make_archive(preset_names={"preset-1": "My Preset"})
        zip_bytes, _ = export_archive.export_bundle(gen_id, self.user_id)

        import_archive = self._make_archive(preset_names={})  # importer never installed it
        result = import_archive.import_bundle(zip_bytes)

        assert result["preset_available"] is False
        assert any("My Preset" in w for w in result["warnings"])

    def test_import_warns_when_model_missing_locally(self):
        gen_id = self._create_generation_with_seed_batch([1])
        self._add_model(gen_id, filename="missing.safetensors", sha256="digest-1")
        export_archive = self._make_archive()
        zip_bytes, _ = export_archive.export_bundle(gen_id, self.user_id)

        # Simulate a bare importing instance: the model the export names isn't
        # present there (can't use a second database in this harness, so drop
        # the row the export just read).
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM models WHERE filename = ?", ("missing.safetensors",))

        import_archive = self._make_archive()
        result = import_archive.import_bundle(zip_bytes)

        assert any("missing.safetensors" in w and "not found locally" in w for w in result["warnings"])

    def test_import_warns_on_digest_mismatch(self):
        gen_id = self._create_generation_with_seed_batch([1])
        self._add_model(gen_id, filename="checkpoint.safetensors", sha256="exported-digest")
        export_archive = self._make_archive()
        zip_bytes, _ = export_archive.export_bundle(gen_id, self.user_id)

        # Simulate a locally-present copy of the model that was re-downloaded
        # (or re-packed) and now hashes differently than the exported one.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE models SET sha256 = ? WHERE filename = ?",
                ("different-digest", "checkpoint.safetensors"),
            )

        import_archive = self._make_archive()
        result = import_archive.import_bundle(zip_bytes)

        assert any("digest does not match" in w for w in result["warnings"])

    def test_export_raises_not_found_for_generation_owned_by_another_user(self):
        gen_id = self._create_generation_with_seed_batch([1])
        archive = self._make_archive()

        with pytest.raises(GenerationNotFoundException):
            archive.export_bundle(gen_id, "someone-else")
