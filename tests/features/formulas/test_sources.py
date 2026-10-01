from types import SimpleNamespace

from src.features.formulas.sources import ModelRepositoryRefChecker, flatten_fields
from src.platform.security.user import AccountType
from tests.features.formulas.conftest import make_user


def test_flatten_reaches_fields_nested_in_containers():
    schema = {
        "properties": {
            "tabs": {
                "type": "tabs", "name": "tabs",
                "children": [
                    {"type": "tab", "children": [
                        {"type": "row", "children": [{"type": "slider", "name": "steps"}]},
                        {"type": "select", "name": "sampler"},
                    ]},
                ],
            },
            "seed": {"type": "seed", "name": "seed"},
        }
    }

    assert set(flatten_fields(schema)) == {"tabs", "steps", "sampler", "seed"}


class FakeModelRepository:
    def __init__(self):
        self.model = SimpleNamespace(model_type="lora", tags=[SimpleNamespace(id="t1")], is_available=True)

    def get_by_id(self, model_id, include_providers=True, include_tags=True):
        return self.model if model_id == "m1" else None

    def get_available_model_ids_for_user(self, user_id):
        return ["m1"] if user_id == "user-1" else []


def test_checker_reports_type_tags_and_availability():
    info = ModelRepositoryRefChecker(FakeModelRepository()).inspect("m1", make_user("user-1"))

    assert (info.model_type, info.tag_ids, info.available) == ("lora", ["t1"], True)


def test_checker_hides_unknown_models_and_models_outside_the_users_assignments():
    checker = ModelRepositoryRefChecker(FakeModelRepository())

    assert checker.inspect("missing", make_user("user-1")) is None
    assert checker.inspect("m1", make_user("user-2")) is None


def test_checker_lets_admins_see_every_model():
    checker = ModelRepositoryRefChecker(FakeModelRepository())

    assert checker.inspect("m1", make_user("user-2", AccountType.ADMIN)) is not None
