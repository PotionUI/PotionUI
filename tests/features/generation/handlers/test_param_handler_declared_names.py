import json

from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.generation.parameter_repository import GenerationParameterRepository
from src.pipelines.outputs import ParamGenerationOutput
from tests.fixtures.persistence_base import PersistenceTestBase


class TestDeclaredParameterNames(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.user_id = self.create_test_user()
        self.generation_id = self.create_test_generation(user_id=self.user_id)
        self.repo = GenerationParameterRepository()

    def _handle(self, **kwargs):
        return ParamGenerationOutputHandler(self.generation_id, self.user_id).handle(
            ParamGenerationOutput(**kwargs)
        )

    def _rows(self):
        return {r.parameter_name: r for r in self.repo.get_by_generation(self.generation_id)}

    def test_a_declared_name_nobody_registered_is_stored(self):
        self._handle(name="prompter", values=["alice"], declared=True)

        assert json.loads(self._rows()["prompter"].parameter_value) == "alice"

    def test_an_undeclared_unregistered_name_is_still_dropped(self):
        metadata = self._handle(name="prompter", values=["alice"])

        assert metadata["saved_count"] == 0
        assert self._rows() == {}

    def test_a_declared_name_still_never_stores_an_unrendered_template(self):
        self._handle(name="prompter", values=["{{ form.who }}"], declared=True)

        assert self._rows() == {}

    def test_a_declared_name_still_reduces_a_leaked_model_path(self):
        self._handle(name="adapter", values=["models/loras/detail.safetensors"], declared=True)

        assert json.loads(self._rows()["adapter"].parameter_value) == "detail.safetensors"

    def test_meta_and_emit_position_are_recorded(self):
        self._handle(name="duration", values=[5, 6], declared=True, meta={"label": "Length", "unit": "s"})
        self._handle(name="seed", values=[1, 2])
        self._handle(name="prompter", values=["a", "b"], declared=True)

        rows = self.repo.get_by_generation_and_index(self.generation_id, 1)
        by_name = {r.parameter_name: r for r in rows}

        assert json.loads(by_name["duration"].meta) == {"label": "Length", "unit": "s"}
        assert by_name["seed"].meta is None
        assert [by_name[n].position for n in ("duration", "seed", "prompter")] == [0, 1, 2]

    def test_replaying_an_output_keeps_the_first_row(self):
        self._handle(name="seed", values=[1])
        self._handle(name="seed", values=[9])

        rows = self.repo.get_by_generation(self.generation_id)

        assert len(rows) == 1
        assert json.loads(rows[0].parameter_value) == 1
