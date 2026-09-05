"""Binary artifact payloads in a durable run report: offloaded to managed
storage, bounded by bytes as well as count, and owned by the report row.

Runs against a real scratch SQLite database and a real `FileStore` rooted in
a temp directory - the point of the change is what actually lands on disk and
in the row, which a mocked store cannot show.
"""

import base64
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src.features.generation.history_archive import GenerationHistoryArchive
from src.features.generation.run_report_artifacts import (
    RUN_REPORT_ARTIFACT_REF,
    iter_ref_paths,
    sniff_image_payload,
)
from src.features.generation.run_report_recorder import (
    SCHEMA_VERSION,
    _ARTIFACTS_CAP,
    _ARTIFACT_ITEM_BYTES_CAP,
    _ARTIFACTS_BYTES_CAP,
    _PLUGIN_OUTPUT_ITEM_BYTES_CAP,
    RunReportRecorder,
)
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.platform.database.database import Database
from src.platform.filesystem.file_store import FileStore

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


def _jpeg_base64(size_px: int = 256) -> str:
    """A real JPEG, base64-encoded, big enough to cross the offload floor."""
    image = Image.new("RGB", (size_px, size_px))
    for x in range(size_px):
        for y in range(0, size_px, 7):
            image.putpixel((x, y), (x % 256, y % 256, (x * y) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _artifact_message(artifact_data, artifact_type="compare_images", pipe_id=0):
    return {
        "type": "pipe_artifact",
        "pipe_id": pipe_id,
        "pipe_name": f"pipe-{pipe_id}",
        "artifact_type": artifact_type,
        "artifact_data": artifact_data,
    }


class RunReportOffloadCase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()

        patcher = patch("src.platform.database.database.db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.storage_dir = Path(self.temp_dir) / "storage"
        self.file_service = FileStore(str(self.storage_dir))
        self.repository = GenerationRunReportRepository()
        self.recorder = RunReportRecorder(self.repository, self.file_service)

        self._insert_generation("gen-1")

    def tearDown(self):
        Database._instance = None

    def _insert_generation(self, generation_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generations (id, preset_id, form_data) VALUES (?, ?, ?)",
                (generation_id, "preset", "{}"),
            )
            conn.commit()

    def _stored_files(self):
        return sorted(p for p in self.storage_dir.rglob("*") if p.is_file())

    def _saved_report(self, generation_id="gen-1"):
        return self.repository.get(generation_id)


class TestSavedVersusLivePayload(RunReportOffloadCase):

    def test_binary_payload_is_referenced_not_inlined(self):
        payload = _jpeg_base64()
        message = _artifact_message({"compare_image": payload, "compare_label": "before"})

        self.recorder.record_output("gen-1", message)
        self.recorder.flush("gen-1", terminal_status="completed")

        saved = self._saved_report()
        self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
        ref = saved["artifacts"][0]["artifact_data"]["compare_image"]
        self.assertEqual(ref["$media"], RUN_REPORT_ARTIFACT_REF)
        self.assertEqual(ref["mime"], "image/jpeg")
        self.assertEqual(ref["width"], 256)
        self.assertEqual(ref["url"], f"/api/generations/gen-1/run-report/artifacts/{ref['name']}")
        self.assertEqual(saved["artifacts"][0]["artifact_data"]["compare_label"], "before")

        row_bytes = len(json.dumps(saved).encode("utf-8"))
        self.assertLess(row_bytes, len(payload) // 4)

    def test_live_message_keeps_its_inline_payload(self):
        payload = _jpeg_base64()
        message = _artifact_message({"compare_image": payload})

        self.recorder.record_output("gen-1", message)

        self.assertEqual(message["artifact_data"]["compare_image"], payload)

    def test_referenced_bytes_are_readable_back_by_name(self):
        payload = _jpeg_base64()
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": payload}))
        self.recorder.flush("gen-1", terminal_status="completed")

        ref = self._saved_report()["artifacts"][0]["artifact_data"]["compare_image"]
        content, mime = self.recorder.artifact_bytes("gen-1", ref["name"])

        self.assertEqual(content, base64.b64decode(payload))
        self.assertEqual(mime, "image/jpeg")

    def test_unknown_name_resolves_to_nothing(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.assertIsNone(self.recorder.artifact_bytes("gen-1", "../../etc/passwd"))

    def test_small_payloads_stay_inline(self):
        self.recorder.record_output("gen-1", _artifact_message({"seed": 42}, "seed"))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.assertEqual(self._saved_report()["artifacts"][0]["artifact_data"], {"seed": 42})
        self.assertEqual(self._stored_files(), [])


class TestNoDuplicationOfExistingOutputs(RunReportOffloadCase):

    def test_a_payload_that_is_already_a_served_output_is_kept_as_the_reference(self):
        url = "/api/media/generations/gen-1/0.png" + "?cache=" + "x" * 8000
        self.recorder.record_output("gen-1", _artifact_message({"to_image": url}))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.assertEqual(self._saved_report()["artifacts"][0]["artifact_data"]["to_image"], url)
        self.assertEqual(self._stored_files(), [])

    def test_a_storage_key_payload_is_kept_as_the_reference(self):
        key = "generations/2026-09-05/gen-1/0.png" + "#" + "y" * 8000
        self.recorder.record_output("gen-1", _artifact_message({"to_image": key}))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.assertEqual(self._saved_report()["artifacts"][0]["artifact_data"]["to_image"], key)
        self.assertEqual(self._stored_files(), [])


class TestByteAndCountBounds(RunReportOffloadCase):

    def test_an_oversized_non_binary_payload_is_marked_omitted(self):
        oversized = "n" * (_ARTIFACT_ITEM_BYTES_CAP + 1024)
        self.recorder.record_output("gen-1", _artifact_message({"workflow": oversized}, "workflow"))
        self.recorder.flush("gen-1", terminal_status="completed")

        saved = self._saved_report()
        artifact = saved["artifacts"][0]
        self.assertIsNone(artifact["artifact_data"])
        self.assertEqual(artifact["omitted"]["reason"], "item_bytes")
        self.assertEqual(saved["artifacts_omitted"], 1)
        self.assertEqual(self._stored_files(), [])

    def test_the_report_byte_budget_omits_later_artifacts(self):
        chunk = "z" * (_ARTIFACT_ITEM_BYTES_CAP // 2)
        for _ in range(24):
            self.recorder.record_output("gen-1", _artifact_message({"diff": chunk}, "diff_text"))
        self.recorder.flush("gen-1", terminal_status="completed")

        saved = self._saved_report()
        self.assertLessEqual(saved["artifacts_bytes"], _ARTIFACTS_BYTES_CAP)
        self.assertGreater(saved["artifacts_omitted"], 0)
        reasons = {a["omitted"]["reason"] for a in saved["artifacts"] if a.get("omitted")}
        self.assertEqual(reasons, {"report_bytes"})

    def test_the_count_cap_drops_the_oldest_artifact_and_its_stored_bytes(self):
        payload = _jpeg_base64()
        for _ in range(_ARTIFACTS_CAP + 3):
            self.recorder.record_output("gen-1", _artifact_message({"compare_image": payload}))
        self.recorder.flush("gen-1", terminal_status="completed")

        saved = self._saved_report()
        self.assertEqual(len(saved["artifacts"]), _ARTIFACTS_CAP)
        self.assertTrue(saved["artifacts_truncated"])
        self.assertEqual(len(self._stored_files()), _ARTIFACTS_CAP)

    def test_an_oversized_plugin_output_is_marked_omitted(self):
        message = {
            "type": "custom_output",
            "pipe_name": "plugin-pipe",
            "blob": "q" * (_PLUGIN_OUTPUT_ITEM_BYTES_CAP + 1024),
        }
        self.recorder.record_output("gen-1", message)
        self.recorder.flush("gen-1", terminal_status="completed")

        saved = self._saved_report()
        entry = saved["plugin_outputs"]["custom_output"]
        self.assertIsNone(entry["message"])
        self.assertEqual(entry["omitted"]["reason"], "item_bytes")
        self.assertEqual(saved["plugin_outputs_omitted"], 1)

    def test_a_small_plugin_output_is_kept_whole(self):
        self.recorder.record_output(
            "gen-1", {"type": "custom_output", "pipe_name": "plugin-pipe", "value": 7}
        )
        self.recorder.flush("gen-1", terminal_status="completed")

        entry = self._saved_report()["plugin_outputs"]["custom_output"]
        self.assertEqual(entry["message"]["value"], 7)
        self.assertNotIn("omitted", entry)


class TestOwnershipAndCleanup(RunReportOffloadCase):

    def test_a_failed_save_removes_the_files_it_wrote(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))

        with patch.object(self.repository, "save", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.recorder.flush("gen-1", terminal_status="completed")

        self.assertEqual(self._stored_files(), [])
        self.assertIsNone(self._saved_report())

    def test_deleting_a_report_removes_its_row_and_its_files(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")
        self.assertEqual(len(self._stored_files()), 1)

        self.assertTrue(self.recorder.delete_report("gen-1"))

        self.assertIsNone(self._saved_report())
        self.assertEqual(self._stored_files(), [])

    def test_reflushing_a_generation_drops_the_superseded_report_files(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")

        self.assertEqual(len(self._stored_files()), 1)
        self.assertEqual(len(iter_ref_paths(self._saved_report())), 1)

    def test_deleting_the_generation_removes_the_report_owned_files(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")
        self.assertEqual(len(self._stored_files()), 1)

        generation_repo = Mock()
        generation_repo.get_files.return_value = []
        archive = GenerationHistoryArchive(generation_repo, self.file_service, Mock(), Mock())

        deleted, failed = archive._delete_generation_files("gen-1", "user-1")

        self.assertEqual((deleted, failed), (1, 0))
        self.assertEqual(self._stored_files(), [])

    def test_generation_output_files_are_never_deleted_by_the_report(self):
        self.recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        self.recorder.flush("gen-1", terminal_status="completed")
        output_path, metadata = self.file_service.save_file("gen-1", b"output-bytes", "png", "0")

        self.recorder.delete_report("gen-1")

        self.assertTrue(Path(output_path).exists())
        self.assertTrue(self.file_service.generation_exists(metadata["file_path"]))


class TestSchemaOneCompatibility(RunReportOffloadCase):

    def test_an_existing_base64_report_is_read_back_unchanged(self):
        payload = _jpeg_base64(32)
        legacy = {
            "schema_version": 1,
            "status_history": [{"at": "t", "pipe_id": 0, "step": "sampling",
                                "message": None, "progress": 0.5}],
            "status_history_truncated": False,
            "pipe_timers": {"0": {"started_at": "t", "ended_at": "t"}},
            "artifacts": [{"at": "t", "pipe_id": 0, "artifact_type": "compare_images",
                           "artifact_data": {"compare_image": payload}}],
            "artifacts_truncated": False,
            "plugin_outputs": {},
        }
        self.repository.save("gen-1", legacy)

        report = self.recorder.get_report("gen-1")

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["artifacts"][0]["artifact_data"]["compare_image"], payload)
        self.assertEqual(report["artifacts_omitted"], 0)
        self.assertEqual(report["stored_bytes"], 0)
        self.assertEqual(report["plugin_outputs_omitted"], 0)


class TestPayloadSniffing(unittest.TestCase):

    def test_a_jpeg_payload_is_recognised(self):
        self.assertEqual(sniff_image_payload(_jpeg_base64(16)), ("jpg", "image/jpeg"))

    def test_a_data_url_is_recognised(self):
        self.assertEqual(
            sniff_image_payload("data:image/jpeg;base64," + _jpeg_base64(16)),
            ("jpg", "image/jpeg"),
        )

    def test_plain_text_is_not_an_image(self):
        self.assertIsNone(sniff_image_payload("not an image at all"))


class TestStoreUnavailable(RunReportOffloadCase):

    def test_a_binary_payload_is_omitted_rather_than_inlined_without_a_store(self):
        recorder = RunReportRecorder(self.repository)

        recorder.record_output("gen-1", _artifact_message({"compare_image": _jpeg_base64()}))
        recorder.flush("gen-1", terminal_status="completed")

        artifact = self._saved_report()["artifacts"][0]
        self.assertIsNone(artifact["artifact_data"])
        self.assertEqual(artifact["omitted"]["reason"], "unstorable_payload")
