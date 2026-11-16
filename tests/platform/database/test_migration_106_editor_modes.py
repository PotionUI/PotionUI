import contextlib
import io
import json
from pathlib import Path

import yaml

from tests.platform.database._support import MigrationCase


class TestMigration106EditorModes(MigrationCase):
    MIGRATION = "106_editor_modes"
    PREREQUISITES = (
        "001_baseline",
        "039_session_pinned",
        "078_session_write_version",
        "103_editor_tasks",
        "105_editor_task_defaults",
    )

    def pre_state(self):
        self.root = Path(self.temp_dir) / "presets"
        self.preset("P-ONE", {"edit": ("edit", "edit_region"), "inpaint": ("inpaint", "edit_region")})
        self.preset("P-TWO", {"fast": ("edit", "edit_region"), "slow": ("edit", "edit_region")})
        self.preset("P-RM", {"edit": ("edit", "edit_region"), "remove": ("inpaint", "remove_object")})
        self.preset("P-NONE", {})
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u2', 'u2', 'u2@example.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u3', 'u3', 'u3@example.com', 'x')"
            )

    def preset(self, preset_id, modes):
        directory = self.root / preset_id
        directory.mkdir(parents=True)
        (directory / "preset.yml").write_text(yaml.safe_dump({"id": preset_id, "name": preset_id}), encoding="utf-8")
        for name, (source, task) in modes.items():
            target = directory / "editor_modes" / name
            target.mkdir(parents=True)
            document = {"schema_version": 1, "task": task, "from": source, "region": "crop", "inputs": {"source": "x"}}
            (target / "editor_mode.yml").write_text(yaml.safe_dump(document), encoding="utf-8")

    def sql(self, statement, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(statement, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def session(self, session_id, preset, data, kind="editor"):
        self.sql(
            "INSERT INTO sessions (id, user_id, preset_id, name, data, kind) VALUES (?, 'u1', ?, ?, ?, ?)",
            (session_id, preset, session_id, json.dumps(data), kind),
        )

    def default(self, task, preset, mode, variant="custom", user="u1"):
        self.sql(
            "INSERT INTO editor_task_defaults (user_id, task, preset_id, mode, variant, setup_kind, set_at) "
            "VALUES (?, ?, ?, ?, ?, 'defaults', 'a')",
            (user, task, preset, mode, variant),
        )

    def data(self, session_id):
        return json.loads(self.sql("SELECT data FROM sessions WHERE id = ?", (session_id,))[0][0])

    def defaults(self):
        return {row[0]: row[1:] for row in self.sql("SELECT task, preset_id, mode, variant FROM editor_task_defaults")}

    def run_up(self):
        self.migration._preset_roots = lambda: [self.root]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.migration.up()
        return buffer.getvalue()

    def test_editor_session_keys_get_the_prefix_of_the_editor_mode_derived_from_them(self):
        self.session("s1", "P-ONE", {"edit": {"formData": {"seed": 1}}, "inpaint": {"formData": {}}})

        self.run_up()

        assert self.data("s1") == {"editor:edit": {"formData": {"seed": 1}}, "editor:inpaint": {"formData": {}}}

    def test_a_key_maps_to_the_editor_mode_that_names_it_as_its_from_mode(self):
        self.preset("P-FROM", {"quick": ("edit", "edit_region")})
        self.session("s1", "P-FROM", {"edit": {"formData": {}}})

        self.run_up()

        assert list(self.data("s1")) == ["editor:quick"]

    def test_a_default_is_prefixed_and_loses_its_variant(self):
        self.default("edit_region", "P-ONE", "edit", "custom")

        self.run_up()

        assert self.defaults() == {"edit_region": ("P-ONE", "editor:edit", None)}

    def test_a_default_that_used_the_fallback_maps_through_the_task_it_fell_back_to(self):
        self.default("remove_object", "P-ONE", "inpaint")

        self.run_up()

        assert self.defaults()["remove_object"][1] == "editor:inpaint"

    def test_several_editor_modes_for_one_generation_mode_are_told_apart_by_the_task(self):
        self.default("remove_object", "P-RM", "inpaint")
        self.default("edit_region", "P-RM", "edit")

        self.run_up()

        assert self.defaults()["remove_object"][1] == "editor:remove"
        assert self.defaults()["edit_region"][1] == "editor:edit"

    def test_an_exact_task_match_beats_the_fallback_task_and_the_fallback_is_used_when_nothing_matches(self):
        self.preset("P-FB", {"plain": ("edit", "edit_region"), "removal": ("edit", "remove_object")})
        self.preset("P-FB2", {"one": ("edit", "edit_region"), "two": ("edit", "edit_region")})
        self.default("remove_object", "P-FB", "edit")
        self.session("s-fb", "P-FB", {"edit": {"formData": {}}})
        self.default("remove_object", "P-FB2", "edit", user="u2")
        self.preset("P-FB3", {"plain": ("edit", "edit_region"), "other": ("edit", "acme.relight")})
        self.default("remove_object", "P-FB3", "edit", user="u3")

        self.run_up()

        rows = {row[0]: row[1:] for row in self.sql("SELECT preset_id, task, mode FROM editor_task_defaults")}
        assert rows["P-FB"] == ("remove_object", "editor:removal")
        assert rows["P-FB2"] == ("remove_object", "edit")
        assert rows["P-FB3"] == ("remove_object", "editor:plain")
        assert self.data("s-fb") == {"edit": {"formData": {}}}

    def test_rows_that_cannot_be_mapped_are_left_untouched_and_counted(self):
        self.session("ambiguous", "P-TWO", {"edit": {"formData": {}}})
        self.session("nomodes", "P-NONE", {"edit": {"formData": {}}})
        self.session("unknown", "P-ONE", {"gone": {"formData": {}}})
        self.default("edit_region", "P-TWO", "edit")

        log = self.run_up()

        assert self.data("ambiguous") == {"edit": {"formData": {}}}
        assert self.data("nomodes") == {"edit": {"formData": {}}}
        assert self.data("unknown") == {"gone": {"formData": {}}}
        assert self.defaults()["edit_region"] == ("P-TWO", "edit", "custom")
        assert "sessions keys prefixed 0, left 3" in log and "defaults prefixed 0, left 1" in log

    def test_the_log_line_counts_what_changed(self):
        self.session("s1", "P-ONE", {"edit": {"formData": {}}})
        self.default("edit_region", "P-ONE", "edit")

        log = self.run_up()

        assert "106_editor_modes" in log
        assert "sessions keys prefixed 1, left 0" in log and "defaults prefixed 1, left 0" in log

    def test_a_second_run_changes_nothing(self):
        self.session("s1", "P-ONE", {"edit": {"formData": {"seed": 1}}})
        self.default("edit_region", "P-ONE", "edit")
        self.run_up()
        sessions, defaults = self.data("s1"), self.defaults()

        log = self.run_up()

        assert self.data("s1") == sessions and self.defaults() == defaults
        assert "sessions keys prefixed 0" in log and "defaults prefixed 0" in log

    def test_prefixed_rows_and_generation_sessions_are_never_touched(self):
        self.session("done", "P-ONE", {"editor:edit": {"formData": {}}})
        self.session("generation", "P-ONE", {"edit": {"formData": {}}}, kind="generation")
        self.default("edit_region", "P-ONE", "editor:inpaint", None)

        self.run_up()

        assert self.data("done") == {"editor:edit": {"formData": {}}}
        assert self.data("generation") == {"edit": {"formData": {}}}
        assert self.defaults()["edit_region"] == ("P-ONE", "editor:inpaint", None)

    def test_a_key_that_would_collide_with_an_existing_prefixed_one_is_left(self):
        self.session("s1", "P-ONE", {"edit": {"formData": {"seed": 1}}, "editor:edit": {"formData": {"seed": 2}}})

        self.run_up()

        assert self.data("s1") == {"edit": {"formData": {"seed": 1}}, "editor:edit": {"formData": {"seed": 2}}}

    def test_a_row_of_a_preset_that_is_not_on_disk_is_left_and_the_migration_never_fails(self):
        self.session("s1", "P-GONE", {"edit": {"formData": {}}})
        self.default("edit_region", "P-GONE", "edit")

        log = self.run_up()

        assert self.data("s1") == {"edit": {"formData": {}}}
        assert "left 1" in log

    def test_a_corrupt_session_payload_is_skipped(self):
        self.sql(
            "INSERT INTO sessions (id, user_id, preset_id, name, data, kind) VALUES ('bad', 'u1', 'P-ONE', 'bad', 'not json', 'editor')"
        )

        self.run_up()

        assert self.sql("SELECT data FROM sessions WHERE id = 'bad'") == [("not json",)]

    def test_the_shipped_tree_is_scanned_by_default(self):
        roots = self.migration._preset_roots()

        assert [path.name for path in roots] == ["marketplace", "local"]
        assert all(path.parent.name == "presets" for path in roots)
        assert (roots[0] / "Flux3" / "editor_modes" / "edit" / "editor_mode.yml").is_file()
