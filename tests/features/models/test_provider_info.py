import asyncio
import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.file_repository import file_repo
from src.features.models.repository import ModelRepository
from src.features.models.collaborators import build_model_index_collaborators
from src.features.models.records import Model
from src.features.models import operations
from src.features.models.provider_info import MAX_PROVIDER_PREVIEW_MEDIA, _infer_preview_type
from src.features.providers.base_provider import ProviderModelInfo
from src.platform.filesystem.storage_driver import LocalFileStorageDriver


def _png_bytes(color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG")
    return buf.getvalue()


class TestInferPreviewType:
    def test_extension_wins_over_content_type(self):
        assert _infer_preview_type("https://x.test/a.png", "video/mp4") == "image"

    def test_falls_back_to_content_type(self):
        assert _infer_preview_type("https://x.test/a", "video/mp4") == "video"

    def test_unrecognized_returns_none(self):
        assert _infer_preview_type("https://x.test/a", "application/json") is None


class TestProviderInfoPreviewMedia(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = ModelRepository()

        self._storage_root = tempfile.mkdtemp()
        self.storage = Path(self._storage_root)
        (self.storage / "uploads").mkdir(parents=True)

        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = str(self.storage)
        self.settings.get_models_media_directory.return_value = str(self.storage / "models")

        from src.features.tags.repository import tag_repo
        self.collaborators = build_model_index_collaborators(
            self.repo, tag_repo, Mock(), self.settings, Mock(), models_root=self.storage
        )
        self.fetcher = self.collaborators.provider_info

    def tearDown(self):
        shutil.rmtree(self._storage_root, ignore_errors=True)
        super().tearDown()

    def _seed_model(self, sha256="a" * 64):
        return self.repo.create(Model(
            filename="detail.safetensors",
            file_path="/models/loras/detail.safetensors",
            model_type="lora",
            sha256=sha256,
        ))

    def _fake_fetch(self, data=None, content_type="image/png"):
        payload = data if data is not None else _png_bytes()
        return AsyncMock(return_value=(payload, content_type))

    def test_attach_provider_previews_stores_a_preview(self):
        model = self._seed_model()

        with patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch()):
            asyncio.run(self.fetcher._attach_provider_previews(
                model.id, ["https://example.com/a.png"]
            ))

        previews = operations.list_model_previews(self.collaborators, model.id)
        assert len(previews) == 1
        assert previews[0]["type"] == "image"
        reloaded = self.repo.get_by_id(model.id)
        assert reloaded.preview_media is not None

    def test_skips_urls_of_unrecognized_media_type(self):
        model = self._seed_model()

        with patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch(content_type="application/json")):
            asyncio.run(self.fetcher._attach_provider_previews(
                model.id, ["https://example.com/a.json"]
            ))

        assert operations.list_model_previews(self.collaborators, model.id) == []

    def test_skips_when_model_already_has_a_preview(self):
        model = self._seed_model()
        (self.storage / "uploads" / "existing.png").write_bytes(_png_bytes())
        operations.add_model_preview(
            self.collaborators, model.id,
            {"source_path": "uploads/existing.png", "type": "image"},
        )

        mocked = self._fake_fetch()
        with patch.object(self.fetcher, "_fetch_media_bytes", mocked):
            asyncio.run(self.fetcher._attach_provider_previews(
                model.id, ["https://example.com/a.png"]
            ))

        mocked.assert_not_called()
        assert len(operations.list_model_previews(self.collaborators, model.id)) == 1

    def test_second_attach_is_a_no_op(self):
        model = self._seed_model()

        with patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch()):
            asyncio.run(self.fetcher._attach_provider_previews(
                model.id, ["https://example.com/a.png"]
            ))
        assert len(operations.list_model_previews(self.collaborators, model.id)) == 1

        with patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch()):
            asyncio.run(self.fetcher._attach_provider_previews(
                model.id, ["https://example.com/a.png", "https://example.com/b.png"]
            ))

        assert len(operations.list_model_previews(self.collaborators, model.id)) == 1

    def test_media_url_count_is_capped(self):
        model = self._seed_model()
        urls = [f"https://example.com/{i}.png" for i in range(MAX_PROVIDER_PREVIEW_MEDIA + 5)]

        mocked = self._fake_fetch()
        with patch.object(self.fetcher, "_fetch_media_bytes", mocked):
            asyncio.run(self.fetcher._attach_provider_previews(model.id, urls))

        assert mocked.await_count == MAX_PROVIDER_PREVIEW_MEDIA

    def test_stored_preview_reads_back_real_bytes_through_the_storage_driver(self):
        real_driver = LocalFileStorageDriver(str(self.storage))
        collaborators = build_model_index_collaborators(
            self.repo, self._tag_repo(), Mock(), self.settings, Mock(),
            models_root=self.storage, storage_driver=real_driver,
        )
        fetcher = collaborators.provider_info
        model = self._seed_model()
        payload = _png_bytes(color=(200, 30, 40))

        with patch.object(fetcher, "_fetch_media_bytes", self._fake_fetch(data=payload)):
            asyncio.run(fetcher._attach_provider_previews(
                model.id, ["https://example.com/real.png"]
            ))

        previews = operations.list_model_previews(collaborators, model.id)
        assert len(previews) == 1
        file_record = file_repo.get_by_id(previews[0]["file_id"])
        assert file_record is not None
        assert real_driver.exists(file_record.file_path)
        assert real_driver.get_bytes(file_record.file_path) == payload

    def _tag_repo(self):
        from src.features.tags.repository import tag_repo
        return tag_repo

    def test_run_provider_fetch_attaches_preview_from_provider_media(self):
        model = self._seed_model()

        provider_info = ProviderModelInfo(
            provider_id="fake-provider",
            provider_model_id="123",
            name="Fake Model",
            media_urls=["https://example.com/a.png"],
        )
        fake_registry = Mock()
        fake_registry.get_model_by_hash = AsyncMock(return_value=provider_info)

        with patch(
            "src.features.providers.registry.get_provider_registry",
            return_value=fake_registry,
        ), patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch()):
            asyncio.run(self.fetcher.run_provider_fetch("fake-provider", model_ids=[model.id]))

        previews = operations.list_model_previews(self.collaborators, model.id)
        assert len(previews) == 1

        updated = self.repo.get_by_id(model.id, include_providers=True)
        assert updated.providers[0].provider == "fake-provider"

    def test_two_consecutive_fetches_do_not_duplicate_previews(self):
        model = self._seed_model()

        provider_info = ProviderModelInfo(
            provider_id="fake-provider",
            provider_model_id="123",
            name="Fake Model",
            media_urls=["https://example.com/a.png"],
        )
        fake_registry = Mock()
        fake_registry.get_model_by_hash = AsyncMock(return_value=provider_info)

        with patch(
            "src.features.providers.registry.get_provider_registry",
            return_value=fake_registry,
        ), patch.object(self.fetcher, "_fetch_media_bytes", self._fake_fetch()):
            asyncio.run(self.fetcher.run_provider_fetch(
                "fake-provider", model_ids=[model.id], force_refresh=True
            ))
            first_count = len(operations.list_model_previews(self.collaborators, model.id))

            asyncio.run(self.fetcher.run_provider_fetch(
                "fake-provider", model_ids=[model.id], force_refresh=True
            ))
            second_count = len(operations.list_model_previews(self.collaborators, model.id))

        assert first_count == 1
        assert second_count == first_count

    def _fetch_with_description(self, model_id, description):
        provider_info = ProviderModelInfo(
            provider_id="fake-provider",
            provider_model_id="123",
            name="Fake Model",
            description=description,
        )
        fake_registry = Mock()
        fake_registry.get_model_by_hash = AsyncMock(return_value=provider_info)
        with patch(
            "src.features.providers.registry.get_provider_registry",
            return_value=fake_registry,
        ):
            asyncio.run(self.fetcher.run_provider_fetch("fake-provider", model_ids=[model_id]))

    def test_provider_description_fills_an_empty_model_description(self):
        model = self._seed_model()

        self._fetch_with_description(model.id, "From the provider")

        assert self.repo.get_by_id(model.id).description == "From the provider"

    def test_provider_description_never_overwrites_an_existing_one(self):
        model = self._seed_model()
        self.repo.update_description(model.id, "Written by an admin")

        self._fetch_with_description(model.id, "From the provider")

        assert self.repo.get_by_id(model.id).description == "Written by an admin"

    def _fetch_with_trigger_words(self, model_id, words):
        provider_info = ProviderModelInfo(
            provider_id="fake-provider",
            provider_model_id="123",
            name="Fake Model",
            trigger_words=words,
        )
        fake_registry = Mock()
        fake_registry.get_model_by_hash = AsyncMock(return_value=provider_info)
        with patch(
            "src.features.providers.registry.get_provider_registry",
            return_value=fake_registry,
        ):
            asyncio.run(self.fetcher.run_provider_fetch("fake-provider", model_ids=[model_id]))

    def _seed_triggers_definition(self):
        from src.features.models.attributes.repository import AttributeDefinitionRepository
        from src.features.models.attributes.seeding import ensure_builtin_attribute_definitions
        ensure_builtin_attribute_definitions(AttributeDefinitionRepository())

    def test_provider_trigger_words_fill_the_empty_triggers_attribute(self):
        self._seed_triggers_definition()
        model = self._seed_model()

        self._fetch_with_trigger_words(model.id, ["sks style", "detailed"])

        assert self.repo.get_by_id(model.id).model_metadata.get("triggers") == ["sks style", "detailed"]

    def test_provider_trigger_words_never_replace_existing_ones(self):
        self._seed_triggers_definition()
        model = self._seed_model()
        self.repo.update_model_metadata(model.id, {"triggers": ["mine"], "strength": [0.5, 0.8]})

        self._fetch_with_trigger_words(model.id, ["sks style"])

        metadata = self.repo.get_by_id(model.id).model_metadata
        assert metadata.get("triggers") == ["mine"]
        assert metadata.get("strength") == [0.5, 0.8]
