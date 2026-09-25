import os
from datetime import date
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.features.models.records import Model, ModelInfo
from src.features.models.repository import ModelRepository
from src.features.models.routes import ModelController, build_router
from src.features.models.search_filter import (
    InvalidModelSearch,
    ModelSearchFilter,
    parse_model_search,
)
from src.platform.database.sql_functions import regexp
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


class TestParseModelSearch:
    def test_substring_mode_keeps_search_and_leaves_filter_empty(self):
        search, search_filter = parse_model_search(search="flux", q_mode=None)
        assert search == "flux"
        assert search_filter.is_empty

    def test_regex_mode_moves_pattern_into_filter(self):
        search, search_filter = parse_model_search(search=r"^flux.*\.safetensors$", q_mode="regex")
        assert search is None
        assert search_filter.regex == r"^flux.*\.safetensors$"

    def test_invalid_regex_is_rejected_with_reason(self):
        with pytest.raises(InvalidModelSearch, match="Invalid regular expression"):
            parse_model_search(search="([a-z", q_mode="regex")

    def test_overlong_regex_is_rejected(self):
        with pytest.raises(InvalidModelSearch, match="longer than"):
            parse_model_search(search="a" * 201, q_mode="regex")

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(InvalidModelSearch):
            parse_model_search(search="x", q_mode="glob")

    def test_dates_parse_as_days(self):
        _, search_filter = parse_model_search(
            search=None, q_mode=None, indexed_from="2026-01-02", indexed_to="2026-01-05T10:00:00Z"
        )
        assert search_filter.indexed_from == date(2026, 1, 2)
        assert search_filter.indexed_to == date(2026, 1, 5)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"indexed_from": "yesterday"},
            {"indexed_from": "2026-02-01", "indexed_to": "2026-01-01"},
            {"used": "sometimes"},
            {"min_uses": 0},
            {"used": "never", "min_uses": 2},
            {"used": "never", "last_used_from": "2026-01-01"},
        ],
    )
    def test_invalid_combinations_are_rejected(self, kwargs):
        with pytest.raises(InvalidModelSearch):
            parse_model_search(search=None, q_mode=None, **kwargs)


class TestRegexpFunction:
    def test_matches_case_insensitively(self):
        assert regexp("^FLUX", "flux-dev.safetensors")

    def test_null_value_never_matches(self):
        assert not regexp("x", None)

    def test_invalid_pattern_never_matches(self):
        assert not regexp("([", "anything")


class TestModelSearchRepository(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repository = ModelRepository()
        self.flux = self._model("flux-dev.safetensors", "2026-01-10 08:00:00")
        self.sdxl = self._model("sdxl-base.safetensors", "2026-02-15 23:30:00")
        self.lora = self._model("detail-tweaker.safetensors", "2026-03-01 00:00:00")
        self.repository.create_provider(ModelInfo(model_id=self.lora.id, provider="civitai", name="Juggernaut Detail"))
        self._use(self.flux.id, "g1", "2026-03-01 10:00:00")
        self._use(self.flux.id, "g2", "2026-03-05 10:00:00")
        self._use(self.flux.id, "g3", "2026-03-09 10:00:00")
        self._use(self.sdxl.id, "g4", "2026-02-20 10:00:00")

    def _model(self, filename: str, indexed_at: str) -> Model:
        model = self.repository.create(Model(
            filename=filename,
            file_path=os.path.join("/models", filename),
            file_size=1,
            sha256=filename.ljust(64, "0")[:64],
            model_type="checkpoint",
        ))
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE models SET indexed_at = ? WHERE id = ?", (indexed_at, model.id))
        return model

    def _use(self, model_id: str, generation_id: str, at: str) -> None:
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT OR IGNORE INTO generations (id, form_data) VALUES (?, ?)", (generation_id, "{}"))
            cursor.execute(
                "INSERT INTO generation_models (id, generation_id, model_id, created_at) VALUES (?, ?, ?, ?)",
                (f"gm-{generation_id}-{model_id}", generation_id, model_id, at),
            )

    def _ids(self, search_filter: ModelSearchFilter, **kwargs) -> list:
        return [m.id for m in self.repository.get_all(search_filter=search_filter, include_files=False, **kwargs)]

    def _names(self, search_filter: ModelSearchFilter, **kwargs) -> set:
        return {m.filename for m in self.repository.get_all(search_filter=search_filter, include_files=False, **kwargs)}

    def test_regex_matches_filename(self):
        self.assertEqual(self._names(ModelSearchFilter(regex=r"^(flux|sdxl)-")), {"flux-dev.safetensors", "sdxl-base.safetensors"})

    def test_regex_matches_provider_display_name(self):
        self.assertEqual(self._names(ModelSearchFilter(regex=r"jugger")), {"detail-tweaker.safetensors"})

    def test_regex_matches_library_custom_name(self):
        user_id = self.create_test_user()
        UserModelMetaRepository().set_custom_name(user_id, self.sdxl.id, "My Portrait Base")
        self.assertEqual(self._names(ModelSearchFilter(regex=r"portrait"), library_user_id=user_id), {"sdxl-base.safetensors"})
        self.assertEqual(self._names(ModelSearchFilter(regex=r"portrait")), set())

    def test_indexed_range_is_inclusive_by_day(self):
        found = self._names(ModelSearchFilter(indexed_from=date(2026, 1, 10), indexed_to=date(2026, 2, 15)))
        self.assertEqual(found, {"flux-dev.safetensors", "sdxl-base.safetensors"})

    def test_indexed_range_reads_iso_timestamps_with_offset(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE models SET indexed_at = ? WHERE id = ?", ("2026-03-01T00:00:00.123456+00:00", self.lora.id))
        found = self._names(ModelSearchFilter(indexed_from=date(2026, 3, 1), indexed_to=date(2026, 3, 1)))
        self.assertEqual(found, {"detail-tweaker.safetensors"})

    def test_never_used(self):
        self.assertEqual(self._names(ModelSearchFilter(used="never")), {"detail-tweaker.safetensors"})

    def test_used(self):
        self.assertEqual(self._names(ModelSearchFilter(used="used")), {"flux-dev.safetensors", "sdxl-base.safetensors"})

    def test_min_uses(self):
        self.assertEqual(self._names(ModelSearchFilter(used="used", min_uses=2)), {"flux-dev.safetensors"})

    def test_last_used_range_uses_latest_generation(self):
        found = self._names(ModelSearchFilter(last_used_from=date(2026, 2, 1), last_used_to=date(2026, 2, 28)))
        self.assertEqual(found, {"sdxl-base.safetensors"})

    def test_combined_regex_never_used_and_indexed_range(self):
        search_filter = ModelSearchFilter(
            regex=r"\.safetensors$", used="never", indexed_from=date(2026, 2, 1), indexed_to=date(2026, 3, 31)
        )
        self.assertEqual(self._names(search_filter), {"detail-tweaker.safetensors"})
        self.assertEqual(self.repository.count_total(search_filter=search_filter), 1)

    def test_count_total_honours_usage(self):
        search_filter = ModelSearchFilter(used="used")
        self.assertEqual(self.repository.count_total(search_filter=search_filter), 2)

    def test_usage_is_exposed_when_requested(self):
        models = {m.filename: m for m in self.repository.get_all(include_usage=True, include_files=False)}
        self.assertEqual(models["flux-dev.safetensors"].use_count, 3)
        self.assertEqual(models["flux-dev.safetensors"].last_used_at.isoformat(), "2026-03-09T10:00:00+00:00")
        self.assertEqual(models["detail-tweaker.safetensors"].use_count, 0)
        self.assertIsNone(models["detail-tweaker.safetensors"].last_used_at)
        payload = models["flux-dev.safetensors"].to_dict(admin=True)
        self.assertEqual(payload["use_count"], 3)
        self.assertNotIn("use_count", models["flux-dev.safetensors"].to_dict(admin=False))

    def test_usage_omitted_by_default(self):
        model = self.repository.get_all(include_files=False)[0]
        self.assertIsNone(model.use_count)
        self.assertNotIn("use_count", model.to_dict(admin=True))

    def test_sort_by_uses_desc(self):
        ordered = [m.filename for m in self.repository.get_all(sort_by="uses", sort_order="desc", include_files=False)]
        self.assertEqual(ordered, ["flux-dev.safetensors", "sdxl-base.safetensors", "detail-tweaker.safetensors"])

    def test_sort_by_last_used_desc_puts_unused_last(self):
        ordered = [m.filename for m in self.repository.get_all(sort_by="last_used", sort_order="desc", include_files=False)]
        self.assertEqual(ordered, ["flux-dev.safetensors", "sdxl-base.safetensors", "detail-tweaker.safetensors"])


class TestListModelsSearchRoute:
    @pytest.fixture
    def captured(self):
        return {}

    @pytest.fixture
    def app(self, captured):
        def list_models(params, user):
            captured["params"] = params
            return {"models": [], "total": 0}

        collaborators = Mock()
        collaborators.catalog.list_models = list_models
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
            return await client.get("/api/models", params=params)

    @pytest.mark.asyncio
    async def test_invalid_regex_returns_422_with_message(self, app, captured):
        response = await self._get(app, {"search": "([", "q_mode": "regex"})
        assert response.status_code == 422
        assert "Invalid regular expression" in response.text
        assert "params" not in captured

    @pytest.mark.asyncio
    async def test_filters_reach_the_catalog(self, app, captured):
        response = await self._get(app, {
            "search": "^flux",
            "q_mode": "regex",
            "used": "never",
            "indexed_from": "2026-01-01",
            "indexed_to": "2026-01-31",
            "sort_by": "uses",
        })
        assert response.status_code == 200
        params = captured["params"]
        assert params.search is None
        assert params.sort_by == "uses"
        assert params.search_filter == ModelSearchFilter(
            regex="^flux", used="never", indexed_from=date(2026, 1, 1), indexed_to=date(2026, 1, 31)
        )


class TestCatalogUsageVisibility:
    def _catalog(self):
        from src.features.models.catalog import ModelCatalog

        repo = Mock()
        repo.get_all.return_value = []
        repo.count_total.return_value = 0
        policy = Mock()
        policy.get_allowed_model_ids.return_value = None
        scanner = Mock()
        catalog = ModelCatalog(repo, policy, scanner, user_attribute_repository=Mock(get_maps=Mock(return_value={})))
        catalog.get_model_stats = Mock(return_value={})
        return catalog, repo

    def _user(self, account_type):
        user = Mock(spec=User)
        user.id = "u1"
        user.account_type = account_type
        return user

    def test_non_admin_cannot_filter_or_sort_by_usage(self):
        from src.features.models.catalog import ListModelsParams

        catalog, repo = self._catalog()
        search_filter = ModelSearchFilter(regex="x", used="never")
        catalog.list_models(ListModelsParams(search_filter=search_filter, sort_by="uses"), self._user(AccountType.USER))
        kwargs = repo.get_all.call_args.kwargs
        assert kwargs["search_filter"] == ModelSearchFilter(regex="x")
        assert kwargs["sort_by"] == "indexed_at"
        assert kwargs["include_usage"] is False
        assert repo.count_total.call_args.kwargs["search_filter"] == ModelSearchFilter(regex="x")

    def test_admin_gets_usage_filters_and_columns(self):
        from src.features.models.catalog import ListModelsParams

        catalog, repo = self._catalog()
        search_filter = ModelSearchFilter(used="never")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.features.models.catalog.model_availability_repo", Mock(backend_ids_by_model=Mock(return_value={}), has_any=Mock(return_value=False)))
            catalog.list_models(ListModelsParams(search_filter=search_filter, sort_by="uses"), self._user(AccountType.ADMIN))
        kwargs = repo.get_all.call_args.kwargs
        assert kwargs["search_filter"] == search_filter
        assert kwargs["sort_by"] == "uses"
        assert kwargs["include_usage"] is True
