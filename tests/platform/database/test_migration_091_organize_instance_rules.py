import sqlite3

from tests.platform.database._support import MigrationCase

_STAMP = "2026-10-05T00:00:00+00:00"


class TestMigration091OrganizeInstanceRules(MigrationCase):
    MIGRATION = "091_organize_instance_rules"
    PREREQUISITES = ("001_baseline", "052_organize")

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            for user_id in ("u1", "u2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )

    def _populate(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, conditions_json, actions_json, "
                "position, paused_reason, created_at, updated_at) VALUES ('r1', 'u1', 'Mine', 'model', 't', "
                "'[{\"fact\": \"name\"}]', '[{\"action\": \"add_to_collection\"}]', 3, 'rate_limited', ?, ?)",
                (_STAMP, _STAMP),
            )
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at) "
                "VALUES ('r2', 'u2', 'Theirs', 'generation', 't', ?, ?)",
                (_STAMP, _STAMP),
            )
            cursor.execute(
                "INSERT INTO organize_runs (id, rule_id, rule_name, user_id, subject, kind, matched, applied, started_at) "
                "VALUES ('run1', 'r1', 'Mine', 'u1', 'model', 'live', 4, 3, ?)",
                (_STAMP,),
            )
            cursor.execute(
                "INSERT INTO organize_applications (id, run_id, rule_id, user_id, item_type, item_id, action_kind, "
                "target_type, target_id, target_name, change_json, created_at) VALUES ('a1', 'run1', 'r1', 'u1', "
                "'model', 'm1', 'add_to_collection', 'model_collection', 'c1', 'LoRAs', '{\"created\": true}', ?)",
                (_STAMP,),
            )
            cursor.execute(
                "INSERT INTO organize_handled (rule_id, item_type, item_id, handled_at) VALUES ('r1', 'model', 'm1', ?)",
                (_STAMP,),
            )
            cursor.execute(
                "INSERT INTO organize_controls (scope_key, paused, rule_cap, updated_at) VALUES ('u1', 1, 7, ?)", (_STAMP,)
            )

    def _rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _user_id_required(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1]: bool(row[3]) for row in cursor.fetchall()}["user_id"]

    def test_an_empty_database_gets_nullable_owners_and_actor_columns(self):
        self.migration.up()

        for table in ("organize_rules", "organize_runs", "organize_applications"):
            self.assertFalse(self._user_id_required(table), table)
        self.assertTrue({"created_by", "updated_by"} <= self.columns("organize_rules"))
        self.assertTrue({"started_by", "undone_by"} <= self.columns("organize_runs"))
        self.assertIn("organize_outcomes", {r["name"] for r in self._rows("SELECT name FROM sqlite_master WHERE type = 'table'")})

    def test_existing_rows_keep_every_value(self):
        self._populate()

        self.migration.up()

        rules = {r["id"]: r for r in self._rows("SELECT * FROM organize_rules")}
        self.assertEqual(set(rules), {"r1", "r2"})
        self.assertEqual(
            (rules["r1"]["user_id"], rules["r1"]["name"], rules["r1"]["position"], rules["r1"]["paused_reason"]),
            ("u1", "Mine", 3, "rate_limited"),
        )
        self.assertEqual(rules["r1"]["conditions_json"], '[{"fact": "name"}]')
        self.assertEqual((rules["r1"]["created_by"], rules["r1"]["updated_by"]), ("u1", "u1"))
        run = self._rows("SELECT * FROM organize_runs")[0]
        self.assertEqual((run["id"], run["user_id"], run["matched"], run["applied"]), ("run1", "u1", 4, 3))
        application = self._rows("SELECT * FROM organize_applications")[0]
        self.assertEqual((application["id"], application["user_id"], application["target_name"]), ("a1", "u1", "LoRAs"))
        self.assertEqual(application["change_json"], '{"created": true}')
        self.assertEqual(len(self._rows("SELECT * FROM organize_handled")), 1)
        self.assertEqual(self._rows("SELECT rule_cap FROM organize_controls WHERE scope_key = 'u1'"), [{"rule_cap": 7}])

    def test_the_handled_ledger_survives_the_rebuild_of_its_parent_table(self):
        self._populate()

        self.migration.up()

        self.assertEqual(
            self._rows("SELECT rule_id, item_id FROM organize_handled"), [{"rule_id": "r1", "item_id": "m1"}]
        )

    def test_it_replays_without_touching_data(self):
        self._populate()
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at, created_by) "
                "VALUES ('inst', NULL, 'Instance', 'model', 't', ?, ?, 'u1')",
                (_STAMP, _STAMP),
            )

        self.migration.up()
        self.migration.up()

        self.assertEqual({r["id"] for r in self._rows("SELECT id FROM organize_rules")}, {"r1", "r2", "inst"})
        self.assertEqual(len(self._rows("SELECT * FROM organize_runs")), 1)
        self.assertEqual(len(self._rows("SELECT * FROM organize_handled")), 1)

    def test_instance_rows_can_have_no_owner(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at) "
                "VALUES ('inst', NULL, 'Instance', 'model', 't', ?, ?)",
                (_STAMP, _STAMP),
            )
            cursor.execute(
                "INSERT INTO organize_runs (id, rule_id, rule_name, user_id, subject, kind, started_at, started_by) "
                "VALUES ('run', 'inst', 'Instance', NULL, 'model', 'backfill', ?, 'u1')",
                (_STAMP,),
            )
            cursor.execute(
                "INSERT INTO organize_applications (id, run_id, rule_id, user_id, item_type, item_id, action_kind, "
                "target_type, target_id, created_at) VALUES ('a', 'run', 'inst', NULL, 'model', 'm', 'x', 'user_group', 'g', ?)",
                (_STAMP,),
            )

        self.assertEqual(len(self._rows("SELECT * FROM organize_applications WHERE user_id IS NULL")), 1)

    def test_deleting_a_user_keeps_instance_rules_and_clears_the_actor(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at, created_by, updated_by) "
                "VALUES ('inst', NULL, 'Instance', 'model', 't', ?, ?, 'u1', 'u1')",
                (_STAMP, _STAMP),
            )
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at) "
                "VALUES ('mine', 'u1', 'Mine', 'model', 't', ?, ?)",
                (_STAMP, _STAMP),
            )
            cursor.execute("DELETE FROM users WHERE id = 'u1'")

        rules = {r["id"]: r for r in self._rows("SELECT * FROM organize_rules")}
        self.assertEqual(set(rules), {"inst"})
        self.assertIsNone(rules["inst"]["created_by"])
        self.assertIsNone(rules["inst"]["updated_by"])

    def test_deleting_a_rule_removes_its_ledger_and_outcomes(self):
        self._populate()
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_outcomes (rule_id, item_type, item_id, outcome, code, updated_at) "
                "VALUES ('r1', 'model', 'm9', 'waiting', 'unreachable', ?)",
                (_STAMP,),
            )
            cursor.execute("DELETE FROM organize_rules WHERE id = 'r1'")

        self.assertEqual(self._rows("SELECT * FROM organize_handled"), [])
        self.assertEqual(self._rows("SELECT * FROM organize_outcomes"), [])

    def test_outcomes_accept_only_waiting_or_skipped(self):
        self._populate()
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO organize_outcomes (rule_id, item_type, item_id, outcome, code, updated_at) "
                    "VALUES ('r1', 'model', 'm9', 'done', 'x', ?)",
                    (_STAMP,),
                )

    def test_foreign_keys_are_enforced_again_afterwards(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
