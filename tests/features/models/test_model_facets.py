import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.models.catalog import ListModelsParams, ModelCatalog
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.models.routes import ModelController, build_router
from src.features.models.search_filter import ModelSearchFilter
from src.features.tags.repository import TagRepository
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.fixtures.persistence_base import PersistenceTestBase


class TestFacetCounts(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repository = ModelRepository()
        self.tags = TagRepository()
        self.anime = self.tags.create_tag("anime", type="MODEL")
        self.photo = self.tags.create_tag("photo", type="MODEL")
        self.lora_a = self._model("krea2_alice_onetrainer.safetensors", "lora", [self.anime])
        self.lora_b = self._model("krea2_bob_onetrainer.safetensors", "lora", [self.anime, self.photo])
        self.lora_c = self._model("flux_style.safetensors", "lora", [self.photo])
        self.vae = self._model("krea2_vae.safetensors", "vae", [])
        self.ckpt = self._model("sdxl_base.safetensors", "checkpoint", [self.photo])
        self.catalog = ModelCatalog(
            self.repository,
            Mock(),
            Mock(models_dir=Path(self.temp_dir) / "depot", MODEL_TYPE_MAPPING={}),
            user_attribute_repository=Mock(),
        )

    def _model(self, filename, model_type, tags):
        model = self.repository.create(Model(
            filename=filename,
            file_path=os.path.join("/models", filename),
            file_size=1,
            sha256=filename.ljust(64, "0")[:64],
            model_type=model_type,
        ))
        for tag in tags:
            self.tags.add_tag_to_model(model.id, tag.id)
        return model

    def _admin(self):
        user = Mock(spec=User)
        user.id = "admin-1"
        user.account_type = AccountType.ADMIN
        return user

    def _counts(self, result):
        return {entry["type"]: entry["count"] for entry in result["types"]}

    def test_type_counts_follow_search_and_ignore_the_type_itself(self):
        counts = self.repository.count_filtered_by_type(search_filter=ModelSearchFilter(wildcard="krea2_*"))
        self.assertEqual(counts, {"lora": 2, "vae": 1})

    def test_type_counts_follow_regex_and_tags(self):
        counts = self.repository.count_filtered_by_type(
            tag_ids=[self.photo.id], search_filter=ModelSearchFilter(regex="^(krea2|sdxl)")
        )
        self.assertEqual(counts, {"lora": 1, "checkpoint": 1})

    def test_tag_counts_follow_every_filter(self):
        counts = self.repository.count_filtered_by_tag(
            model_type="lora", search_filter=ModelSearchFilter(wildcard="krea2_*")
        )
        self.assertEqual(counts, {self.anime.id: 2, self.photo.id: 1})

    def test_count_total_matches_the_type_facet_sum(self):
        search_filter = ModelSearchFilter(regex="onetrainer|style")
        self.assertEqual(self.repository.count_total(tag_ids=[self.photo.id], search_filter=search_filter), 2)
        self.assertEqual(
            sum(self.repository.count_filtered_by_type(tag_ids=[self.photo.id], search_filter=search_filter).values()),
            2,
        )

    def test_catalog_keeps_every_type_and_reports_zero_for_unmatched(self):
        facets = ListModelsParams(search_filter=ModelSearchFilter(wildcard="krea2_*_onetrainer"))
        result = self.catalog.get_model_types(self._admin(), facets=facets)
        self.assertEqual(self._counts(result), {"lora": 2, "vae": 0, "checkpoint": 0})
        self.assertEqual(result["total"], 2)

    def test_catalog_without_facets_reports_global_counts(self):
        result = self.catalog.get_model_types(self._admin())
        self.assertEqual(self._counts(result), {"lora": 3, "vae": 1, "checkpoint": 1})
        self.assertEqual(result["total"], 5)
        self.assertNotIn("tag_counts", result)

    def test_catalog_tag_counts_respect_the_selected_type(self):
        facets = ListModelsParams(model_type="lora", search="krea2")
        result = self.catalog.get_model_types(self._admin(), facets=facets, include_tag_counts=True)
        self.assertEqual(self._counts(result), {"lora": 2, "vae": 1, "checkpoint": 0})
        self.assertEqual(result["tag_counts"], {self.anime.id: 2, self.photo.id: 1})

    def test_catalog_strips_usage_filters_for_non_admins(self):
        user = Mock(spec=User)
        user.id = "user-1"
        user.account_type = AccountType.USER
        self.repository.get_available_model_ids_for_user = Mock(
            return_value=[self.lora_a.id, self.lora_b.id, self.vae.id]
        )
        facets = ListModelsParams(search_filter=ModelSearchFilter(wildcard="krea2_*", used="used"))
        result = self.catalog.get_model_types(user, user_scoped=True, facets=facets)
        self.assertEqual(self._counts(result), {"lora": 2, "vae": 1})


class TestModelTypesRoute:
    @pytest.fixture
    def captured(self):
        return {}

    @pytest.fixture
    def app(self, captured):
        def get_model_types(user, user_scoped, include_empty, facets, include_tag_counts):
            captured["facets"] = facets
            captured["include_tag_counts"] = include_tag_counts
            return {"types": [], "total_types": 0, "total": 0}

        collaborators = Mock()
        collaborators.catalog.get_model_types = get_model_types
        user = Mock(spec=User)
        user.id = "admin-1"
        user.account_type = AccountType.ADMIN
        container = Mock()
        container.model_controller = ModelController(collaborators, Mock(), Mock())
        fastapi_app = FastAPI()
        fastapi_app.include_router(build_router(container))
        fastapi_app.dependency_overrides[get_current_active_user] = lambda: user
        return fastapi_app

    async def _get(self, app, params):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/models/types", params=params)

    @pytest.mark.asyncio
    async def test_without_filters_no_facets_are_built(self, app, captured):
        response = await self._get(app, {})
        assert response.status_code == 200
        assert captured["facets"] is None

    @pytest.mark.asyncio
    async def test_filters_reach_the_catalog(self, app, captured):
        response = await self._get(app, {
            "search": "krea2_*",
            "tag_ids": "t1,t2",
            "used": "never",
            "model_type": "lora",
            "include_tag_counts": "true",
        })
        assert response.status_code == 200
        facets = captured["facets"]
        assert facets.search is None
        assert facets.tag_ids == ["t1", "t2"]
        assert facets.model_type == "lora"
        assert facets.search_filter == ModelSearchFilter(wildcard="krea2_*", used="never")
        assert captured["include_tag_counts"] is True

    @pytest.mark.asyncio
    async def test_invalid_regex_returns_422(self, app, captured):
        response = await self._get(app, {"search": r"(a)\1", "q_mode": "regex"})
        assert response.status_code == 422
        assert "not supported" in response.text
        assert "facets" not in captured
