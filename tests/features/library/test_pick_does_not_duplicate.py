"""The library must not grow when a user merely browses or picks from it.

Reported as "I have one media in the library, I pick it in a media loader
field, and now the library holds two of it". Picking makes no request at all
(the field builds `uploads/<filename>` from the item it was handed), so the
only way a row could appear is if one of the requests that surround a pick -
the picker's own listing, the metadata refresh, or serving the bytes - wrote
one. Each of those is asserted here against the REAL media and library routers,
a real migrated database and real bytes on disk.

The upload count itself is the assertion, not the response bodies: a listing
that returned the right page while inserting a row would satisfy every other
test in this tree and still be the bug being chased.
"""

import asyncio
import io
import unittest
import types
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.features.forms.binding import bind_form
from src.features.library import operations
from src.features.library.routes import LibraryController, build_router as build_library_router
from src.features.media import ImageProcessor, MediaStore, MediaTypeResolver
from src.features.media.routes import MediaController, build_router as build_media_router
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from src.platform.plugins.registry import PluginRegistry
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid

from tests.features.library.test_operations import LibraryTestBase


class _NoopPlugins:
    """The hook chain's shape with nothing registered on it.

    `MediaStore` runs `before_upload`/`after_upload` through the registry and
    reads `.data` off what comes back, so the upload path needs a registry that
    answers in that shape - not a `None` that turns every upload into a 400.
    """

    def execute_hook(self, hook_name, initial_data=None, **kwargs):
        return types.SimpleNamespace(data=dict(initial_data or {})), []


class _Container:
    def __init__(self, media_controller, library_controller):
        self.media_controller = media_controller
        self.library_controller = library_controller


class LibraryOverHTTPTestBase(LibraryTestBase):
    """The media and library routers over a real database and storage tree."""

    def setUp(self):
        super().setUp()

        media_store = MediaStore(
            file_resolver=self.file_resolver,
            image_processor=ImageProcessor(),
            media_type_resolver=MediaTypeResolver(),
            file_repository=self.file_repo,
            generation_repository=None,
            settings=None,
            file_service=self.file_store,
            plugin_registry=_NoopPlugins(),
            upload_repository=self.upload_repo,
            storage_driver=self.storage_driver,
        )

        container = _Container(
            media_controller=MediaController(media_store),
            library_controller=LibraryController(self.collaborators),
        )

        app = FastAPI()
        app.include_router(build_media_router(container))
        app.include_router(build_library_router(container))

        async def _current_user():
            return User(
                id=self.user_id, username="testuser", email="test@example.com",
                password_hash="h", account_type=AccountType.USER,
            )

        app.dependency_overrides[get_current_active_user] = _current_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    # --- helpers ---

    def _png_bytes(self):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 48), "red").save(buffer, format="PNG")
        return buffer.getvalue()

    def _upload_count(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS c FROM uploads WHERE user_id = ?", (self.user_id,))
            return cursor.fetchone()["c"]

    def _upload_one(self, filename="cat.png"):
        """An upload made the way the field makes it, returned as its wire result."""
        response = self.client.post(
            "/api/media/upload", files={"file": (filename, self._png_bytes(), "image/png")}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class TestPickingFromLibraryDoesNotDuplicate(LibraryOverHTTPTestBase):

    def test_one_upload_records_exactly_one_row(self):
        response = self.client.post(
            "/api/media/upload", files={"file": ("cat.png", self._png_bytes(), "image/png")}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._upload_count(), 1)

    def test_browsing_and_picking_adds_nothing(self):
        """Every request a pick can be surrounded by, against a library of one."""
        uploaded = self.client.post(
            "/api/media/upload", files={"file": ("cat.png", self._png_bytes(), "image/png")}
        ).json()["data"]
        self.assertEqual(self._upload_count(), 1)

        filename = uploaded["filename"]

        # The picker modal opening (it reads the Library, not /api/media/uploads).
        self.assertEqual(self.client.get("/api/library/items").status_code, 200)
        # The editors' upload-row lookup for a `uploads/<name>` field value.
        self.assertEqual(self.client.get("/api/media/uploads").status_code, 200)
        # A field refreshing metadata for a value it was given without any.
        self.assertEqual(self.client.get(f"/api/media/uploads/{filename}/info").status_code, 200)
        # The thumbnail the picked item renders from.
        self.assertEqual(self.client.get(f"/api/media/uploads/{filename}").status_code, 200)
        # Reopening the picker, which is where the duplicate was reported.
        listing = self.client.get("/api/library/items").json()["data"]

        self.assertEqual(self._upload_count(), 1)
        self.assertEqual(listing["total"], 1)
        self.assertEqual([item["filename"] for item in listing["items"]], [filename])

    def test_the_same_row_is_never_listed_twice(self):
        """A page is one row per resource - no join in the filtered read side
        can fan a row out, however many tags or collections it belongs to."""
        item = self._upload(filename="a.png")

        collection_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collections (id, name, user_id) VALUES (?, ?, ?)",
                (collection_id, "set", self.user_id),
            )
            cursor.execute(
                "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)",
                (collection_id, item.id),
            )

        tag_ids = [self.tag_repo.create_tag(n, "UPLOAD", self.user_id).id for n in ("cats", "blue")]
        operations.set_tags(self.collaborators, item.id, tag_ids, self.user_id)

        listing = self.client.get(
            "/api/library/items", params={"tag_ids": tag_ids, "collection_id": collection_id}
        ).json()["data"]

        self.assertEqual(listing["total"], 1)
        self.assertEqual([i["id"] for i in listing["items"]], [item.id])


class TestSubmittingAPickedValueDoesNotDuplicate(LibraryOverHTTPTestBase):
    """Generating from a picked library item must not re-save it.

    The suspicion was that submission re-writes an `uploads/<filename>` value
    to a fresh unique name, which would run `_record_upload_ownership` again
    and leave the user with two of the same media. Binding is where that would
    have to happen: it is the only step between the wire payload and
    persistence that touches a media value at all, and until commit 11c3650b
    it DID rewrite relative string paths (to absolutes, not to new files).

    `bind_form` here is the real one, with the real `Image` validator wired
    through `_INPUT_VALIDATORS` and the real `_check_media_containment`
    against the real storage root - only the preset template is constructed,
    because a preset is data and the code under test is the binder.
    """

    def _preset(self, field_name="input_image", multi=False):
        configuration = {"multi": True} if multi else None
        field = FieldTemplate(
            type="image", name=field_name, default=None, required=False,
            configuration=configuration, children=None,
        )
        return PresetTemplate(
            id="preset_1", name="Preset One", version="1.0.0", path="/presets/preset_1",
            modes={"img2img": ModeTemplate(
                forms=[FormTemplate(name="custom", fields=[field], default=True, order=0)],
                pipes=[],
            )},
        )

    def _picked_item(self, uploaded):
        """Exactly what `handleSelectFromUpload` builds from a library item."""
        relative_path = f"uploads/{uploaded['filename']}"
        return {
            "path": relative_path,
            "relative_path": relative_path,
            "url": uploaded["url"],
            "name": "cat.png",
            "type": "image",
            "metadata": {"width": uploaded["width"], "height": uploaded["height"]},
        }

    def test_binding_a_picked_dict_value_adds_no_row(self):
        uploaded = self._upload_one()
        storage_dir = str(self.file_resolver.get_storage_directory(self.user_id))
        picked = self._picked_item(uploaded)

        bound = bind_form(
            self._preset(), "img2img", None, {"input_image": picked},
            self.user_id, storage_dir=storage_dir,
        )

        self.assertEqual(self._upload_count(), 1)
        # Validate-only: the path travels through binding untouched, so a
        # replayed `form_data` keeps pointing at the one row it was picked from.
        self.assertEqual(bound.values["input_image"]["path"], picked["path"])
        self.assertEqual(bound.values["input_image"]["relative_path"], picked["relative_path"])

    def test_binding_a_picked_string_value_adds_no_row(self):
        """The bare-string shape the same value collapses to once replayed."""
        uploaded = self._upload_one()
        storage_dir = str(self.file_resolver.get_storage_directory(self.user_id))
        stored_path = f"uploads/{uploaded['filename']}"

        bound = bind_form(
            self._preset(), "img2img", None, {"input_image": stored_path},
            self.user_id, storage_dir=storage_dir,
        )

        self.assertEqual(self._upload_count(), 1)
        self.assertEqual(bound.values["input_image"], stored_path)

    def test_binding_the_same_item_picked_twice_into_a_multi_field_adds_no_row(self):
        """A multi field can legitimately hold one library item twice; that is
        two references to one resource, and must stay one row."""
        uploaded = self._upload_one()
        storage_dir = str(self.file_resolver.get_storage_directory(self.user_id))
        picked = self._picked_item(uploaded)

        bound = bind_form(
            self._preset(multi=True), "img2img", None, {"input_image": [picked, dict(picked)]},
            self.user_id, storage_dir=storage_dir,
        )

        self.assertEqual(self._upload_count(), 1)
        self.assertEqual(len(bound.values["input_image"]), 2)

    def test_submitting_through_the_orchestrator_adds_no_row(self):
        """The whole submit path, with the REAL `bind_form` inside it.

        Everything the orchestrator needs to reach persistence is stubbed -
        pipeline builder, backend, websocket, repository - because none of
        them can write an `uploads` row; binding and the media value are what
        this is watching.
        """
        from src.features.generation.orchestrator import GenerationOrchestrator
        from src.features.generation.pipeline_builder import BuiltPipeline

        uploaded = self._upload_one()
        picked = self._picked_item(uploaded)
        preset_template = self._preset()

        pipeline_builder = Mock()
        pipeline_builder.build_pipeline = Mock(return_value=BuiltPipeline(
            generation_id="gen_dup_test", preset_id="preset_1",
            preset_template=preset_template, pipes=[{"name": "generator", "config": {}}],
        ))

        backend = Mock()
        backend.backend_id = "local_1"
        backend.name = "Local"
        backend.engine = "native"
        backend.start_generation = AsyncMock()
        backend_registry = Mock()
        backend_registry.select_backend_for_generation = Mock(return_value=backend)

        connection_hub = Mock()
        connection_hub.broadcast_to_generation = AsyncMock()

        settings = Mock()
        settings.get_file_storage_directory = Mock(
            return_value=str(self.file_resolver.get_storage_directory(self.user_id))
        )
        settings.get_setting = Mock(return_value="/outputs")

        output_processor = Mock()
        output_processor.process_output = AsyncMock(return_value={"processed": True})

        preset_template_loader = Mock()
        preset_template_loader.load_preset_by_id = Mock(return_value=preset_template)

        request = Mock()
        request.preset_id = "preset_1"
        request.form_data = {"input_image": picked}
        request.prompts = None
        request.prompt_state = None
        request.mode = "img2img"
        request.backend_id = None
        request.tag_ids = None
        request.collection_ids = None
        request.segments = None
        request.form_name = None

        orchestrator = GenerationOrchestrator(
            pipeline_builder=pipeline_builder,
            backend_registry=backend_registry,
            connection_hub=connection_hub,
            settings=settings,
            output_processor=output_processor,
            preset_template_loader=preset_template_loader,
            plugin_registry=PluginRegistry(),
        )

        with patch("src.features.generation.orchestrator.generation_repo") as repo:
            repo.create = Mock()
            repo.update_status = Mock()
            repo.get_by_id = Mock(return_value=Mock(user_id=self.user_id))
            asyncio.run(orchestrator.start_generation(request, self.user_id))

        self.assertEqual(self._upload_count(), 1)
        # The value that got persisted still names the row it was picked from.
        persisted = repo.create.call_args[0][0].form_data["input_image"]
        self.assertEqual(persisted["path"], f"uploads/{uploaded['filename']}")

        listing = self.client.get("/api/library/items").json()["data"]
        self.assertEqual(listing["total"], 1)


class TestMasksLandInTheLibrary(LibraryOverHTTPTestBase):
    """A painted inpainting mask is servable but never clutters the Library.

    `MediaEditors.svelte` stores a mask through `POST /api/media/upload` with
    `purpose=derived_artifact` - the mask genuinely is a new file that has to
    be addressable by path (the `${name}_inpaint_mask` sibling channel), but
    it is not a resource the user meant to browse. The Library listing (and
    facets) filter to `purpose='user_upload'` (migration 120); the file stays
    on disk and servable at its `/api/media/uploads/{filename}` URL either way.
    """

    def _upload_mask(self, filename="mask-1723545600000.png"):
        response = self.client.post(
            "/api/media/upload",
            files={"file": (filename, self._png_bytes(), "image/png")},
            data={"purpose": "derived_artifact"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]

    def test_a_stored_mask_does_not_show_up_as_a_library_item(self):
        self._upload_one("cat.png")
        mask = self._upload_mask()

        listing = self.client.get("/api/library/items").json()["data"]

        self.assertEqual(listing["total"], 1)
        self.assertNotIn(
            "mask-1723545600000.png",
            [item["original_filename"] for item in listing["items"]],
        )

        # Still servable by path - the sibling channel addresses it directly,
        # never through the Library.
        served = self.client.get(f"/api/media/uploads/{mask['filename']}")
        self.assertEqual(served.status_code, 200)


if __name__ == "__main__":
    unittest.main()
