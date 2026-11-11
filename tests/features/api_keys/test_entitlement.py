from dataclasses import replace
from unittest.mock import Mock

import pytest

from src.features.formulas.sources import ModelRepositoryRefChecker
from src.features.forms.routes import FormController
from src.features.generation.policy import GenerationPolicy
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.exceptions import ModelAccessDeniedException
from src.features.presets import operations as preset_operations
from src.features.presets.exceptions import PermissionDeniedException, PresetNotFoundException
from src.platform.security.api_scopes import ApiScope
from src.platform.security.principal import KeyGrant
from src.platform.security.user import AccountType, User, owner_is_admin


def person(account_type, user_id="u1"):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="x", account_type=account_type)


def downgraded(owner):
    grant = KeyGrant(key_id="k1", user_id=owner.id, scopes=frozenset({ApiScope.GENERATE}), display_prefix="pui_aaaaaaaa")
    return replace(owner, key=grant, account_type=AccountType.USER)


@pytest.fixture
def admin_key_without_admin():
    return downgraded(person(AccountType.ADMIN, "root"))


@pytest.fixture
def plain_user():
    return person(AccountType.USER, "plain")


class TestTheDowngradeKeepsTheOwnersRight:
    def test_the_user_a_key_without_admin_carries_is_not_an_admin_but_its_owner_is(self, admin_key_without_admin):
        assert admin_key_without_admin.account_type == AccountType.USER
        assert admin_key_without_admin.owner_account_type == AccountType.ADMIN
        assert owner_is_admin(admin_key_without_admin) is True
        assert GenerationPolicy.is_admin(admin_key_without_admin) is False

    def test_a_plain_user_is_neither(self, plain_user):
        assert owner_is_admin(plain_user) is False

    def test_objects_without_an_owner_type_fall_back_to_their_account_type(self):
        assert owner_is_admin(Mock(account_type=AccountType.ADMIN, owner_account_type=None)) is True
        assert owner_is_admin(Mock(account_type=AccountType.USER, owner_account_type="x")) is False
        assert owner_is_admin(None) is False


class TestPresetEntitlement:
    def repo(self, assigned):
        repo = Mock()
        repo.get_available_preset_ids_for_user.return_value = assigned
        return repo

    def test_an_admin_owned_key_may_use_a_preset_only_admins_see(self, admin_key_without_admin):
        repo = self.repo([])

        preset_operations.require_preset_access(
            repo, admin_key_without_admin.id, "admin-only-preset", preset_operations.is_admin_user(admin_key_without_admin)
        )

    def test_a_plain_user_may_not(self, plain_user):
        with pytest.raises(PresetNotFoundException):
            preset_operations.require_preset_access(
                self.repo([]), plain_user.id, "admin-only-preset", preset_operations.is_admin_user(plain_user)
            )

    def test_the_same_key_still_cannot_install_a_preset(self, admin_key_without_admin):
        collaborators = Mock()

        with pytest.raises(PermissionDeniedException):
            preset_operations.install_preset(collaborators, "admin-only-preset", admin_key_without_admin)

    def test_the_form_routes_apply_the_same_rule(self, admin_key_without_admin, plain_user):
        controller = FormController(Mock(), Mock(), self.repo([]))

        controller.require_preset_access("admin-only-preset", admin_key_without_admin)
        with pytest.raises(Exception) as refused:
            controller.require_preset_access("admin-only-preset", plain_user)

        assert getattr(refused.value, "status_code", None) == 404


class TestModelEntitlement:
    def policy(self, assigned):
        repo = Mock()
        repo.get_available_model_ids_for_user.return_value = assigned
        repo.get_by_id.return_value = Mock(id="m-admin-only")
        return ModelAccessPolicy(repo)

    def test_an_admin_owned_key_asking_for_every_model_is_unrestricted(self, admin_key_without_admin):
        assert self.policy(["m1"]).get_allowed_model_ids(admin_key_without_admin, all_models=True) is None

    def test_a_plain_user_and_an_admin_not_asking_for_all_stay_scoped(self, plain_user, admin_key_without_admin):
        policy = self.policy(["m1"])

        assert policy.get_allowed_model_ids(plain_user, all_models=True) == ["m1"]
        assert policy.get_allowed_model_ids(admin_key_without_admin, all_models=False) == ["m1"]

    def test_an_admin_owned_key_reaches_a_model_assigned_to_nobody(self, admin_key_without_admin):
        assert self.policy([]).verify_model_access("m-admin-only", admin_key_without_admin).id == "m-admin-only"

    def test_a_plain_user_does_not(self, plain_user):
        with pytest.raises(ModelAccessDeniedException):
            self.policy([]).verify_model_access("m-admin-only", plain_user)

    def test_the_same_key_is_still_not_an_admin_for_authority_checks(self, admin_key_without_admin):
        assert admin_key_without_admin.account_type != AccountType.ADMIN


class TestFormulaModelReferences:
    def checker(self, assigned):
        repo = Mock()
        repo.get_by_id.return_value = Mock(model_type="checkpoint", tags=[])
        repo.get_available_model_ids_for_user.return_value = assigned
        return ModelRepositoryRefChecker(repo)

    def test_an_admin_owned_key_resolves_a_model_only_admins_see(self, admin_key_without_admin):
        assert self.checker([]).inspect("m-admin-only", admin_key_without_admin) is not None

    def test_a_plain_user_does_not(self, plain_user):
        assert self.checker([]).inspect("m-admin-only", plain_user) is None
