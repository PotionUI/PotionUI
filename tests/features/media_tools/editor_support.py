import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.features.media.records import Upload
from src.features.media.store import MediaStore
from src.features.media.validators import UPLOAD_PURPOSE_DERIVED
from src.features.media_tools.routes import MediaToolController, build_router
from src.features.media_tools.service import MediaToolService
from src.features.media.media_types import MediaTypeResolver
from src.platform.media_tools import MediaTool, ToolCancelled, ToolSpec
from src.platform.media_tools.ops import register_core_tools
from src.platform.security.current_user import require_media_viewer
from tests.features.media_tools.support import FakeGuard, MediaToolsTestBase
from tests.platform.media_tools.media_factory import make_audio


class EditorGuard(FakeGuard):
    def check_items(self, request):
        self.check(request)


def artifact_store(base):
    plugins = Mock()
    plugins.execute_hook = Mock(side_effect=lambda hook, initial_data: (SimpleNamespace(data=initial_data), True))
    return MediaStore(
        file_resolver=Mock(), image_processor=Mock(), media_type_resolver=MediaTypeResolver(),
        file_repository=Mock(), generation_repository=Mock(), settings=Mock(), file_service=Mock(),
        plugin_registry=plugins, upload_repository=base.upload_repo, storage_driver=base.driver,
        limit_guard=base.guard,
    )


class VideoBlock(MediaTool):
    def __init__(self, gate):
        self.gate = gate

    def run(self, inputs, params, ctx):
        self.gate.entered.release()
        while not self.gate.release.wait(0.01):
            if ctx.is_cancelled():
                raise ToolCancelled()
        return list(inputs)

    def still(self, image, params, frame):
        return image


class EditorTestBase(MediaToolsTestBase):
    def setUp(self):
        super().setUp()
        self.guard = EditorGuard()
        self.service = self.make_service(self.driver)
        self.client.close()
        self.client = self.make_client(self.service)
        register_core_tools(self.registry)
        self.registry.register(ToolSpec(id="vblock", label="Hold", accepts=("video",), impl=VideoBlock(self.gate)))

    def make_service(self, driver):
        return MediaToolService(
            registry=self.registry,
            params_repository=self.params_repo,
            upload_repository=self.upload_repo,
            file_repository=self.file_repo,
            media_type_resolver=MediaTypeResolver(),
            storage_driver=driver,
            limit_guard=self.guard,
            jobs=self.jobs,
            scratch_root=self.scratch_root,
            artifact_store=artifact_store(self),
            thumbnail_runner=lambda work: None,
        )

    def make_client(self, service):
        client = super().make_client(service)

        async def viewer():
            return self.make_user(self.acting_user_id)

        client.app.dependency_overrides[require_media_viewer] = viewer
        return client

    def anonymous_client(self):
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(media_tool_controller=MediaToolController(self.service))))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def derived_item(self, path, media_type, user_id=None, **metadata):
        return self.upload_repo.create(Upload(
            user_id=user_id or self.user_id,
            filename=path.name,
            original_filename=path.name,
            media_type=media_type,
            mime_type=MediaTypeResolver().get_media_type(path.suffix),
            file_size=path.stat().st_size,
            purpose=UPLOAD_PURPOSE_DERIVED,
            **metadata,
        ))

    def tone_item(self, name="tone.wav", seconds=3.0, freq=1000, user_id=None, derived=False):
        path = make_audio(self.uploads_dir / name, seconds=seconds, freq=freq)
        if derived:
            return self.derived_item(path, "audio", user_id, duration_seconds=seconds)
        return self.record(path, "audio", user_id, duration_seconds=seconds)

    def stored_path(self, item):
        return self.uploads_dir / self.upload_repo.get_by_id(item.id, self.user_id).filename

    def png_bytes(self, size, color):
        buffer = io.BytesIO()
        Image.new("RGBA", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    def assert_no_paths(self, response):
        for marker in (str(self.temp_dir), str(Path(self.temp_dir).resolve()), str(self.scratch_root)):
            self.assertNotIn(marker, response.text)
