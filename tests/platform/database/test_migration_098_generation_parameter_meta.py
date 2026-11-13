import json

from tests.platform.database._support import MigrationCase


class TestMigration098GenerationParameterMeta(MigrationCase):
    MIGRATION = "098_generation_parameter_meta"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO generations (id, form_data) VALUES ('g1', '{}')")
            cursor.executemany(
                "INSERT INTO generation_parameters "
                "(id, generation_id, parameter_name, parameter_value, parameter_index) VALUES (?, 'g1', ?, ?, ?)",
                [("p1", "seed", json.dumps(7), 0), ("p2", "steps", json.dumps(20), 1)],
            )

    def rows(self, columns="id, parameter_name, parameter_value, parameter_index, meta, position"):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT {columns} FROM generation_parameters ORDER BY id")
            return [tuple(row) for row in cursor.fetchall()]

    def test_adds_the_two_nullable_columns(self):
        assert not {"meta", "position"} & self.columns("generation_parameters")

        self.migration.up()

        assert {"meta", "position"} <= self.columns("generation_parameters")

    def test_old_rows_stay_readable_with_null_meta_and_position(self):
        before = self.rows("id, parameter_name, parameter_value, parameter_index")

        self.migration.up()

        after = self.rows()
        assert [row[:4] for row in after] == before
        assert all(row[4] is None and row[5] is None for row in after)

    def test_running_twice_is_harmless(self):
        self.migration.up()
        self.migration.up()

        assert len(self.rows()) == 2

    def test_new_rows_can_carry_meta_and_position(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_parameters "
                "(id, generation_id, parameter_name, parameter_value, parameter_index, meta, position) "
                "VALUES ('p3', 'g1', 'duration', '5', 0, ?, 2)",
                (json.dumps({"label": "Length"}),),
            )

        assert self.rows()[-1][4:] == (json.dumps({"label": "Length"}), 2)
