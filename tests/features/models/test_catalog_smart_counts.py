from unittest.mock import MagicMock

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.catalog import ListModelsParams, ModelCatalog
from src.features.models.records import Model, ModelInfo
from src.features.models.repository import ModelRepository
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid


class TestCatalogSmartCounts(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.models = ModelRepository()
        self.collections = ModelCollectionRepository()
        self.meta = UserModelMetaRepository()
        self.catalog = ModelCatalog(self.models, ModelAccessPolicy(self.models), MagicMock(), locator=object())
        self.user_id = self.create_test_user()
        self.admin_id = self.create_test_user("admin_user", "admin", "admin@example.com")
        self.user = User(username="u", email="u@x", password_hash="x", id=self.user_id)
        self.admin = User(username="a", email="a@x", password_hash="x", id=self.admin_id, account_type=AccountType.ADMIN)
        self.assigned = self._model("assigned")
        self.assigned_favorite = self._model("assigned_favorite")
        self.assigned_filed = self._model("assigned_filed")
        self.assigned_nsfw = self._model("assigned_nsfw", nsfw=True)
        self.unassigned = self._model("unassigned")
        self.undefined = self._model("undefined", model_type="undefined")
        for model_id in (self.assigned, self.assigned_favorite, self.assigned_filed, self.assigned_nsfw):
            self.models.assign_model_to_user(model_id, self.user_id)
        self.meta.set_favorite(self.user_id, self.assigned_favorite, True)
        self.meta.set_favorite(self.user_id, self.unassigned, True)
        folder = self.collections.create("folder", self.user_id)
        self.collections.add_members(folder.id, [self.assigned_filed, self.unassigned], self.user_id)

    def _model(self, name, nsfw=False, model_type="checkpoint"):
        model_id = self.models.create(
            Model(id=generate_ulid(), filename=f"{name}.safetensors", file_size=1, model_type=model_type)
        ).id
        if nsfw:
            self.models.create_provider(ModelInfo(model_id=model_id, provider="p", nsfw=True))
        return model_id

    def _listed(self, user, **kwargs):
        params = ListModelsParams(limit=None, **kwargs)
        return self.catalog.list_models(params, user)["total"]

    def test_restricted_user_counts_only_assigned_models(self):
        counts = self.catalog.smart_counts(self.user)
        self.assertEqual(counts, {"all": 4, "favorites": 1, "unsorted": 3})

    def test_counts_equal_the_listing_totals(self):
        counts = self.catalog.smart_counts(self.user)
        self.assertEqual(counts["all"], self._listed(self.user))
        self.assertEqual(counts["favorites"], self._listed(self.user, favorites_only=True))
        self.assertEqual(counts["unsorted"], self._listed(self.user, unsorted=True))

    def test_content_restricted_user_never_counts_flagged_models(self):
        counts = self.catalog.smart_counts(self.user, restricted=True)
        self.assertEqual(counts, {"all": 3, "favorites": 1, "unsorted": 2})

    def test_all_models_flag_is_ignored_for_non_admins(self):
        self.assertEqual(self.catalog.smart_counts(self.user, all_models=True)["all"], 4)

    def test_admin_with_all_models_matches_the_unscoped_listing(self):
        counts = self.catalog.smart_counts(self.admin, all_models=True)
        self.assertEqual(counts["all"], self._listed(self.admin, all_models=True))
