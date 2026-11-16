import threading

from src.features.tab_drafts.validation import MAX_DOC_BYTES, doc_size
from src.platform.http.errors import ConflictError, DomainError, InvalidInputError, NotFoundError
from tests.features.edits.support import STATE, EditsTestBase


class TestEditManager(EditsTestBase):

    def make(self, user_id=None, name=None, state=None, **extra):
        return self.manager.create(user_id or self.user_id, "image", name, "library", "up_source", state or STATE, **extra)

    def test_create_starts_at_revision_one_and_keeps_the_state_and_source(self):
        edit = self.make(name="  Beach   shot ")

        self.assertEqual((edit.revision, edit.name, edit.tool), (1, "Beach shot", "image"))
        self.assertEqual((edit.source_kind, edit.source_id, edit.state), ("library", "up_source", STATE))

    def test_a_blank_name_is_stored_as_unnamed(self):
        self.assertIsNone(self.make(name="   ").name)

    def test_create_keeps_a_cleaned_source_name_and_without_one_reads_back_none(self):
        named = self.make(source_name="  observatory   dusk.png ")
        blank = self.make(source_name="   ")
        plain = self.make()

        self.assertEqual(self.manager.get(self.user_id, named.id).source_name, "observatory dusk.png")
        self.assertIsNone(blank.source_name)
        self.assertIsNone(self.manager.get(self.user_id, plain.id).source_name)

    def test_a_source_name_at_the_limit_is_kept_and_one_over_is_truncated(self):
        kept = self.make(source_name="n" * 120)
        cut = self.make(source_name="n" * 119 + "ab")

        self.assertEqual(kept.source_name, "n" * 120)
        self.assertEqual(cut.source_name, "n" * 119 + "a")

    def test_list_rows_carry_the_source_name(self):
        self.make(source_name="a.png")

        rows, _ = self.manager.list(self.user_id, None, None, 50, 0)

        self.assertEqual([edit.source_name for edit in rows], ["a.png"])

    def test_an_unknown_tool_is_refused(self):
        with self.assertRaises(InvalidInputError) as raised:
            self.manager.create(self.user_id, "audio", None, "library", "x", STATE)

        self.assertEqual(raised.exception.code, "invalid_edit_tool")

    def test_an_unknown_source_kind_is_refused(self):
        with self.assertRaises(InvalidInputError) as raised:
            self.manager.create(self.user_id, "image", None, "disk", "x", STATE)

        self.assertEqual(raised.exception.code, "invalid_edit_source")

    def test_every_source_kind_is_accepted(self):
        kinds = [self.manager.create(self.user_id, "image", None, kind, "x", STATE).source_kind for kind in ("history", "library", "upload")]

        self.assertEqual(kinds, ["history", "library", "upload"])

    def sized_state(self, size):
        state = {"b": "x" * (size - 8)}
        self.assertEqual(doc_size(state), size)
        return state

    def test_a_state_exactly_at_the_limit_is_stored_and_one_byte_over_is_refused_on_create(self):
        stored = self.make(state=self.sized_state(MAX_DOC_BYTES))

        with self.assertRaises(DomainError) as raised:
            self.make(state=self.sized_state(MAX_DOC_BYTES + 1))

        self.assertEqual((raised.exception.status_code, raised.exception.code), (413, "edit_state_too_large"))
        self.assertEqual(doc_size(self.manager.get(self.user_id, stored.id).state), MAX_DOC_BYTES)
        self.assertEqual(self.manager.list(self.user_id, None, None, 50, 0)[1], 1)

    def test_a_state_exactly_at_the_limit_saves_and_one_byte_over_is_refused_on_update_leaving_the_row_alone(self):
        edit = self.make()
        saved = self.manager.update_state(self.user_id, edit.id, 1, self.sized_state(MAX_DOC_BYTES))
        before = self.row(edit.id)

        with self.assertRaises(DomainError) as raised:
            self.manager.update_state(self.user_id, edit.id, 2, self.sized_state(MAX_DOC_BYTES + 1), True, None)

        self.assertEqual((saved.revision, raised.exception.status_code), (2, 413))
        self.assertEqual(self.row(edit.id), before)

    def test_a_thumbnail_the_caller_owns_is_kept(self):
        mine = self.upload("mine.png")

        edit = self.make(thumbnail_upload_id=mine.id)

        self.assertEqual(edit.thumbnail_upload_id, mine.id)

    def test_another_users_thumbnail_is_refused_exactly_like_a_missing_one_on_create_and_update(self):
        edit = self.make()
        theirs = self.upload("theirs.png", self.other_user_id)
        before = self.row(edit.id)
        attempts = {
            "create": lambda upload_id: self.make(thumbnail_upload_id=upload_id),
            "update": lambda upload_id: self.manager.update_state(self.user_id, edit.id, 1, STATE, True, upload_id),
        }

        for name, attempt in attempts.items():
            with self.subTest(call=name):
                with self.assertRaises(InvalidInputError) as foreign:
                    attempt(theirs.id)
                with self.assertRaises(InvalidInputError) as missing:
                    attempt("up_missing")
                self.assertEqual((foreign.exception.code, foreign.exception.message), (missing.exception.code, missing.exception.message))
                self.assertEqual(foreign.exception.code, "invalid_edit_thumbnail")

        self.assertEqual(self.row(edit.id), before)
        self.assertEqual(self.manager.list(self.user_id, None, None, 50, 0)[1], 1)

    def test_update_bumps_the_revision_and_replaces_the_state(self):
        edit = self.make()

        updated = self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "stack": "next"})

        self.assertEqual((updated.revision, updated.state), (2, {"v": 1, "stack": "next"}))

    def test_a_stale_revision_conflicts_carries_the_current_one_and_writes_nothing(self):
        edit = self.make()
        self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "stack": "first"})
        before = self.row(edit.id)

        with self.assertRaises(ConflictError) as raised:
            self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "stack": "stale"}, True, None)

        self.assertEqual((raised.exception.status_code, raised.exception.extra["revision"]), (409, 2))
        self.assertEqual(self.row(edit.id), before)

    def test_a_revision_ahead_of_the_row_conflicts_too(self):
        edit = self.make()

        with self.assertRaises(ConflictError):
            self.manager.update_state(self.user_id, edit.id, 5, STATE)

        self.assertEqual(self.row(edit.id)["revision"], 1)

    def test_two_updates_with_the_same_revision_let_exactly_one_win(self):
        edit = self.make()
        gate = threading.Barrier(2)
        results = []

        def attempt(label):
            gate.wait()
            try:
                self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "writer": label})
                results.append(("won", label))
            except ConflictError as error:
                results.append(("lost", error.extra["revision"]))

        threads = [threading.Thread(target=attempt, args=(label,)) for label in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        outcomes = sorted(kind for kind, _ in results)
        winner = next(label for kind, label in results if kind == "won")
        stored = self.row(edit.id)
        self.assertEqual(outcomes, ["lost", "won"])
        self.assertEqual([value for kind, value in results if kind == "lost"], [2])
        self.assertEqual(stored["revision"], 2)
        self.assertIn(f'"writer":"{winner}"', stored["state"])

    def test_the_thumbnail_is_kept_unless_the_update_names_it(self):
        thumb = self.upload("thumb.png")
        edit = self.make(thumbnail_upload_id=thumb.id)

        kept = self.manager.update_state(self.user_id, edit.id, 1, STATE)
        cleared = self.manager.update_state(self.user_id, edit.id, 2, STATE, True, None)

        self.assertEqual((kept.thumbnail_upload_id, cleared.thumbnail_upload_id), (thumb.id, None))

    def test_deleting_the_thumbnail_upload_leaves_the_edit_without_one(self):
        thumb = self.upload("thumb.png")
        edit = self.make(thumbnail_upload_id=thumb.id)

        self.uploads.delete("thumb.png", self.user_id)

        self.assertIsNone(self.manager.get(self.user_id, edit.id).thumbnail_upload_id)

    def test_rename_keeps_the_revision_and_the_state(self):
        edit = self.make()
        self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "stack": "x"})

        renamed = self.manager.rename(self.user_id, edit.id, "Poster")
        saved = self.manager.update_state(self.user_id, edit.id, 2, {"v": 1, "stack": "y"})

        self.assertEqual((renamed.revision, renamed.name, renamed.state), (2, "Poster", {"v": 1, "stack": "x"}))
        self.assertEqual((saved.revision, saved.name), (3, "Poster"))

    def test_rename_to_nothing_or_whitespace_clears_the_name_and_a_long_name_is_refused_unchanged(self):
        edit = self.make(name="Poster")

        for blank in (None, "", " \t\n "):
            with self.subTest(blank=blank):
                self.manager.rename(self.user_id, edit.id, "Poster")
                self.assertIsNone(self.manager.rename(self.user_id, edit.id, blank).name)
        self.manager.rename(self.user_id, edit.id, "x" * 120)
        with self.assertRaises(InvalidInputError) as raised:
            self.manager.rename(self.user_id, edit.id, "x" * 121)

        self.assertEqual(raised.exception.code, "invalid_edit_name")
        self.assertEqual(self.manager.get(self.user_id, edit.id).name, "x" * 120)

    def test_duplicate_makes_a_new_edit_for_the_caller_at_revision_one(self):
        thumb = self.upload("thumb.png")
        edit = self.make(name="Poster", thumbnail_upload_id=thumb.id)
        self.manager.update_state(self.user_id, edit.id, 1, {"v": 1, "stack": "x"})

        copy = self.manager.duplicate(self.user_id, edit.id)

        self.assertNotEqual(copy.id, edit.id)
        self.assertEqual((copy.user_id, copy.revision, copy.name), (self.user_id, 1, "Poster copy"))
        self.assertEqual((copy.state, copy.source_id, copy.thumbnail_upload_id), ({"v": 1, "stack": "x"}, "up_source", thumb.id))
        self.assertEqual(self.manager.get(self.user_id, edit.id).revision, 2)

    def test_a_copy_of_a_maximum_length_name_stays_within_the_limit(self):
        edit = self.make(name="n" * 120)

        copy = self.manager.duplicate(self.user_id, edit.id)

        self.assertEqual(copy.name, ("n" * 120 + " copy")[:120])
        self.assertEqual(len(copy.name), 120)

    def test_a_copy_and_its_original_change_independently(self):
        edit = self.make(name="Poster")
        copy = self.manager.duplicate(self.user_id, edit.id)

        self.manager.update_state(self.user_id, copy.id, 1, {"v": 1, "only": "copy"})
        self.manager.rename(self.user_id, edit.id, "Renamed")
        self.manager.delete(self.user_id, edit.id)

        survivor = self.manager.get(self.user_id, copy.id)
        self.assertEqual((survivor.name, survivor.revision, survivor.state), ("Poster copy", 2, {"v": 1, "only": "copy"}))

    def test_duplicating_an_unnamed_edit_stays_unnamed(self):
        self.assertIsNone(self.manager.duplicate(self.user_id, self.make().id).name)

    def test_a_copy_is_named_after_the_edit_then_the_source_name_without_its_extension(self):
        named = self.manager.duplicate(self.user_id, self.make(name="Poster", source_name="a.png").id)
        sourced = self.manager.duplicate(self.user_id, self.make(source_name="my.photo.final.png").id)
        bare = self.manager.duplicate(self.user_id, self.make(source_name="scan").id)

        self.assertEqual(named.name, "Poster copy")
        self.assertEqual(sourced.name, "my.photo.final copy")
        self.assertEqual(bare.name, "scan copy")
        self.assertEqual((named.source_name, sourced.source_name), ("a.png", "my.photo.final.png"))

    def test_a_copy_named_from_a_maximum_length_source_name_stays_within_the_limit(self):
        copy = self.manager.duplicate(self.user_id, self.make(source_name="n" * 120).id)

        self.assertEqual(len(copy.name), 120)

    def test_delete_removes_the_edit(self):
        edit = self.make()

        self.manager.delete(self.user_id, edit.id)

        with self.assertRaises(NotFoundError):
            self.manager.get(self.user_id, edit.id)
        with self.assertRaises(NotFoundError):
            self.manager.delete(self.user_id, edit.id)

    def test_another_users_edit_is_not_found_on_every_call(self):
        edit = self.make()
        calls = {
            "get": lambda: self.manager.get(self.other_user_id, edit.id),
            "update": lambda: self.manager.update_state(self.other_user_id, edit.id, 1, STATE),
            "rename": lambda: self.manager.rename(self.other_user_id, edit.id, "Mine"),
            "duplicate": lambda: self.manager.duplicate(self.other_user_id, edit.id),
            "delete": lambda: self.manager.delete(self.other_user_id, edit.id),
        }

        for name, call in calls.items():
            with self.subTest(call=name):
                with self.assertRaises(NotFoundError) as raised:
                    call()
                self.assertEqual(raised.exception.code, "edit_not_found")

        stored = self.row(edit.id)
        self.assertEqual((stored["revision"], stored["name"], stored["user_id"]), (1, None, self.user_id))

    def test_list_is_most_recent_first_and_a_save_moves_an_edit_to_the_top(self):
        first = self.make(name="First")
        second = self.make(name="Second")
        third = self.make(name="Third")
        self.manager.update_state(self.user_id, first.id, 1, STATE)

        edits, total = self.manager.list(self.user_id, None, None, 50, 0)

        self.assertEqual(([edit.name for edit in edits], total), (["First", "Third", "Second"], 3))
        self.assertTrue(all(edit.state is None for edit in edits))
        self.assertEqual(third.id, edits[1].id)
        self.assertEqual(second.id, edits[2].id)

    def test_list_pages_and_reports_the_total(self):
        for index in range(5):
            self.make(name=f"Edit {index}")

        page, total = self.manager.list(self.user_id, None, None, 2, 2)

        self.assertEqual(([edit.name for edit in page], total), (["Edit 2", "Edit 1"], 5))

    def test_list_searches_names_case_insensitively_and_treats_wildcards_literally(self):
        self.make(name="Beach Sunset")
        self.make(name="100% sure")
        self.make(name="Under_score")
        self.make()

        beach, _ = self.manager.list(self.user_id, None, "beach", 50, 0)
        percent, _ = self.manager.list(self.user_id, None, "%", 50, 0)
        underscore, _ = self.manager.list(self.user_id, None, "r_s", 50, 0)

        self.assertEqual([edit.name for edit in beach], ["Beach Sunset"])
        self.assertEqual([edit.name for edit in percent], ["100% sure"])
        self.assertEqual([edit.name for edit in underscore], ["Under_score"])

    def test_list_search_matches_the_name_or_the_source_name(self):
        self.make(name="Poster", source_name="observatory-dusk.png")
        self.make(name="Observatory", source_name="x.png")
        self.make(name="Plain")
        self.make(source_name="100%_done.png")

        by_source, source_total = self.manager.list(self.user_id, None, "DUSK", 50, 0)
        by_name, _ = self.manager.list(self.user_id, None, "plain", 50, 0)
        either, either_total = self.manager.list(self.user_id, None, "observatory", 50, 0)
        percent, _ = self.manager.list(self.user_id, None, "%", 50, 0)
        underscore, _ = self.manager.list(self.user_id, None, "%_d", 50, 0)
        miss, miss_total = self.manager.list(self.user_id, None, "nothing", 50, 0)

        self.assertEqual(([edit.name for edit in by_source], source_total), (["Poster"], 1))
        self.assertEqual([edit.name for edit in by_name], ["Plain"])
        self.assertEqual((len(either), either_total), (2, 2))
        self.assertEqual([edit.source_name for edit in percent], ["100%_done.png"])
        self.assertEqual([edit.source_name for edit in underscore], ["100%_done.png"])
        self.assertEqual((miss, miss_total), ([], 0))

    def test_list_search_never_matches_another_users_source_name(self):
        self.make(user_id=self.other_user_id, source_name="secret.png")

        rows, total = self.manager.list(self.user_id, None, "secret", 50, 0)

        self.assertEqual((rows, total), ([], 0))

    def test_list_search_escapes_a_backslash_and_ignores_surrounding_blanks(self):
        self.make(name="a\\b")
        self.make(name="ab")
        self.make(name="Café")

        slash, _ = self.manager.list(self.user_id, None, "a\\b", 50, 0)
        padded, _ = self.manager.list(self.user_id, None, "  ab  ", 50, 0)
        blank, blank_total = self.manager.list(self.user_id, None, "   ", 50, 0)

        self.assertEqual([edit.name for edit in slash], ["a\\b"])
        self.assertEqual([edit.name for edit in padded], ["ab"])
        self.assertEqual(blank_total, 3)

    def test_list_total_follows_search_and_tool_and_an_offset_past_the_end_is_empty(self):
        for name in ("Beach one", "Beach two", "City"):
            self.make(name=name)
        self.make(user_id=self.other_user_id, name="Beach other")

        page, total = self.manager.list(self.user_id, "image", "beach", 1, 0)
        beyond, beyond_total = self.manager.list(self.user_id, "image", "beach", 10, 2)
        far, far_total = self.manager.list(self.user_id, None, None, 10, 99)

        self.assertEqual(([edit.name for edit in page], total), (["Beach two"], 2))
        self.assertEqual((beyond, beyond_total), ([], 2))
        self.assertEqual((far, far_total), ([], 3))

    def test_list_only_returns_the_callers_edits_and_validates_the_tool_filter(self):
        self.make(name="Mine")
        self.make(user_id=self.other_user_id, name="Theirs")

        edits, total = self.manager.list(self.user_id, "image", None, 50, 0)

        self.assertEqual(([edit.name for edit in edits], total), (["Mine"], 1))
        with self.assertRaises(InvalidInputError):
            self.manager.list(self.user_id, "audio", None, 50, 0)

    def test_deleting_the_user_removes_their_edits(self):
        edit = self.make()

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = ?", (self.user_id,))

        self.assertIsNone(self.row(edit.id))
