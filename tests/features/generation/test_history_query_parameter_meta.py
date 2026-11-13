import json

from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.parameter_repository import GenerationParameterRepository
from src.features.generation.repository import GenerationRepository
from src.features.generation.source_repository import GenerationSourceRepository
from tests.fixtures.persistence_base import PersistenceTestBase


class TestGetParamsParameterMeta(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.user_id = self.create_test_user()
        self.repo = GenerationParameterRepository()
        self.sources = GenerationSourceRepository()
        self.query = GenerationHistoryQuery(generation_repo=GenerationRepository())

    def _generation(self, generation_id):
        return self.create_test_generation(generation_id=generation_id, user_id=self.user_id)

    def _old_row(self, generation_id, name, value, index=0):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_parameters "
                "(id, generation_id, parameter_name, parameter_value, parameter_index) VALUES (?, ?, ?, ?, ?)",
                (f"{generation_id}-{name}-{index}", generation_id, name, json.dumps(value), index),
            )

    def _link(self, child, source):
        self.sources.create_for_generation(child, [{
            "field_name": "source_image", "source_generation_id": source, "source_file_index": 0,
        }])

    def test_an_old_generation_keeps_its_shape_plus_two_empty_keys(self):
        gen = self._generation("old")
        self._old_row(gen, "steps", 20)
        self._old_row(gen, "cfg", 5)

        result = self.query.get_params(gen, 0, self.user_id)

        assert list(result) == ["generation_id", "index", "parameters", "models", "parameter_meta", "parameter_order"]
        assert result["parameters"] == {"cfg": 5, "steps": 20}
        assert list(result["parameters"]) == ["cfg", "steps"]
        assert result["models"] == []
        assert result["parameter_meta"] == {}
        assert result["parameter_order"] == ["cfg", "steps"]

    def test_recorded_meta_and_emit_order_are_returned(self):
        gen = self._generation("new")
        self.repo.create_batch(gen, "steps", [20])
        self.repo.create_batch(gen, "duration", [5], meta={"label": "Length", "unit": "s", "type": "number"})
        self.repo.create_batch(gen, "alpha", [1])

        result = self.query.get_params(gen, 0, self.user_id)

        assert list(result["parameters"]) == ["alpha", "duration", "steps"]
        assert result["parameter_order"] == ["steps", "duration", "alpha"]
        assert result["parameter_meta"] == {"duration": {"label": "Length", "unit": "s", "type": "number"}}

    def test_names_without_a_position_follow_alphabetically(self):
        gen = self._generation("mixed")
        self._old_row(gen, "zeta", 1)
        self._old_row(gen, "beta", 2)
        self.repo.create_batch(gen, "omega", [3])

        assert self.query.get_params(gen, 0, self.user_id)["parameter_order"] == ["omega", "beta", "zeta"]

    def test_a_corrupt_stored_meta_is_skipped(self):
        gen = self._generation("corrupt")
        self.repo.create_batch(gen, "steps", [20])
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generation_parameters SET meta = 'not json' WHERE generation_id = ?", (gen,))

        assert self.query.get_params(gen, 0, self.user_id)["parameter_meta"] == {}

    def test_inherited_values_bring_their_meta_and_order(self):
        source = self._generation("source")
        self.repo.create_batch(source, "prompter", ["alice"], meta={"label": "Prompted by"})
        self.repo.create_batch(source, "steps", [30])
        child = self._generation("child")
        self.repo.create_batch(child, "strength", [0.4], meta={"label": "Strength"})
        self.repo.create_batch(child, "steps", [""])
        self._link(child, source)

        result = self.query.get_params(child, 0, self.user_id)

        assert result["parameters"] == {"prompter": "alice", "steps": 30, "strength": 0.4}
        assert result["parameter_meta"] == {"prompter": {"label": "Prompted by"}, "strength": {"label": "Strength"}}
        assert result["parameter_order"] == ["strength", "steps", "prompter"]

    def test_an_own_value_keeps_its_own_meta_over_the_sources(self):
        source = self._generation("source2")
        self.repo.create_batch(source, "steps", [30], meta={"label": "Source steps"})
        child = self._generation("child2")
        self.repo.create_batch(child, "steps", [12], meta={"label": "Own steps"})
        self._link(child, source)

        assert self.query.get_params(child, 0, self.user_id)["parameter_meta"] == {"steps": {"label": "Own steps"}}
