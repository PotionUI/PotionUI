from unittest.mock import patch

from src.features.kits import operations
from src.features.kits.repository import KitRepository
from src.features.kits.catalog import KitCatalog
from src.features.media.records import Upload
from src.platform.http.errors import ConflictError, InvalidInputError, NotFoundError
from tests.features.kits.support import KitsTestBase


class TestKits(KitsTestBase):

    def make(self, name="Mara", **kwargs):
        return operations.create_kit(self.repo, self.user_id, name, **kwargs)

    def test_create_derives_the_path_name_and_a_clash_gets_a_number(self):
        first = self.make("Old harbour")
        second = self.make("Old Harbour")

        self.assertEqual(first.handle, "old_harbour")
        self.assertEqual(second.handle, "old_harbour2")
        self.assertTrue(first.id.startswith("kt_") and len(first.id) == 11)

    def test_an_explicit_path_that_is_taken_is_refused_with_a_suggestion(self):
        self.make("Mara")

        with self.assertRaises(ConflictError) as caught:
            self.make("Other", handle="MARA")

        self.assertEqual(caught.exception.code, "kit_path_taken")
        self.assertEqual(caught.exception.extra["suggestion"], "mara2")

    def test_paths_are_unique_per_owner_not_global(self):
        self.make("Mara")

        other = operations.create_kit(self.repo, self.other_user_id, "Mara")

        self.assertEqual(other.handle, "mara")

    def test_an_invalid_explicit_path_is_refused(self):
        with self.assertRaises(InvalidInputError):
            self.make("Mara", handle="9lives")

    def test_a_blank_name_is_refused(self):
        with self.assertRaises(InvalidInputError):
            self.make("   ")

    def test_renaming_the_display_name_keeps_the_path(self):
        kit = self.make("Mara")

        renamed = operations.update_kit(self.repo, self.user_id, kit.id, name="Mara Voss")

        self.assertEqual((renamed.name, renamed.handle), ("Mara Voss", "mara"))

    def test_a_renamed_path_still_resolves_the_old_one_until_another_kit_takes_it(self):
        kit = self.make("Mara")
        operations.update_kit(self.repo, self.user_id, kit.id, handle="Mara Voss")

        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara").id, kit.id)
        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara_voss").id, kit.id)

        taker = self.make("Mara")
        self.assertEqual(taker.handle, "mara")
        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara").id, taker.id)
        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara_voss").id, kit.id)

    def test_renaming_back_to_a_former_path_drops_the_alias(self):
        kit = self.make("Mara")
        operations.update_kit(self.repo, self.user_id, kit.id, handle="mara2")
        operations.update_kit(self.repo, self.user_id, kit.id, handle="mara")

        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara").id, kit.id)
        self.assertEqual(self.repo.get_by_handle(self.user_id, "mara2").id, kit.id)

    def test_renaming_onto_another_kits_path_is_refused(self):
        self.make("Mara")
        other = self.make("Tomas")

        with self.assertRaises(ConflictError):
            operations.update_kit(self.repo, self.user_id, other.id, handle="mara")

    def test_label_is_free_form_and_none_clears_it(self):
        kit = self.make("Mara", label="  Actor ")
        self.assertEqual(kit.label, "Actor")

        cleared = operations.update_kit(self.repo, self.user_id, kit.id, label=None)

        self.assertIsNone(cleared.label)
        self.assertEqual(operations.update_kit(self.repo, self.user_id, kit.id, name="X").label, None)

    def test_another_owners_kit_is_not_found(self):
        kit = self.make("Mara")

        for call in (
            lambda: operations.get_kit(self.repo, self.other_user_id, kit.id),
            lambda: operations.update_kit(self.repo, self.other_user_id, kit.id, name="x"),
            lambda: operations.delete_kit(self.repo, self.other_user_id, kit.id),
            lambda: operations.duplicate_kit(self.repo, self.other_user_id, kit.id),
        ):
            with self.assertRaises(NotFoundError):
                call()

    def test_listing_filters_by_label_and_searches_name_or_path(self):
        self.make("Mara", label="Actor")
        self.make("Old harbour", label="Location")

        by_label, total = self.repo.list_kits(self.user_id, label="actor")
        searched, _ = self.repo.list_kits(self.user_id, search="harb")

        self.assertEqual(([kit.name for kit in by_label], total), (["Mara"], 1))
        self.assertEqual([kit.name for kit in searched], ["Old harbour"])
        self.assertEqual(self.repo.labels(self.user_id), [{"label": "Actor", "count": 1}, {"label": "Location", "count": 1}])

    def test_label_counts_cover_every_kit_with_that_label(self):
        for name in ("A", "B", "C"):
            self.make(name, label="Actor")
        self.make("D")

        self.assertEqual(self.repo.labels(self.user_id), [{"label": "Actor", "count": 3}])
        self.assertEqual(self.repo.labels(self.other_user_id), [])

    def test_listing_paginates(self):
        for name in ("A", "B", "C"):
            self.make(name)

        page, total = self.repo.list_kits(self.user_id, limit=2, offset=2)

        self.assertEqual(([kit.name for kit in page], total), (["C"], 3))

    def test_kits_are_reordered_and_unlisted_ones_follow(self):
        a, _b, c = self.make("A"), self.make("B"), self.make("C")

        operations.reorder_kits(self.repo, self.user_id, [c.id, a.id])

        self.assertEqual([kit.name for kit in self.repo.list_kits(self.user_id)[0]], ["C", "A", "B"])

    def test_reordering_with_a_foreign_kit_is_refused(self):
        a = self.make("A")
        foreign = operations.create_kit(self.repo, self.other_user_id, "F")

        with self.assertRaises(InvalidInputError):
            operations.reorder_kits(self.repo, self.user_id, [a.id, foreign.id])

    def test_delete_removes_items_and_aliases_but_keeps_library_files(self):
        kit = self.make("Mara")
        upload = self.upload("a.png")
        operations.add_media_item(self.repo, self.user_id, kit.id, upload.id)
        operations.update_kit(self.repo, self.user_id, kit.id, handle="mara2")

        operations.delete_kit(self.repo, self.user_id, kit.id)

        self.assertIsNone(self.repo.get(kit.id, self.user_id))
        self.assertEqual(self.repo.items(kit.id), [])
        self.assertIsNone(self.repo.get_by_handle(self.user_id, "mara"))
        self.assertIsNotNone(self.uploads.get_by_id(upload.id, self.user_id))

    def test_duplicate_copies_items_by_reference_with_new_ids(self):
        kit = self.make("Mara", label="Actor")
        upload = self.upload("a.png")
        operations.add_media_item(self.repo, self.user_id, kit.id, upload.id, name="face")
        operations.add_text_item(self.repo, self.user_id, kit.id, "calm voice")

        copy = operations.duplicate_kit(self.repo, self.user_id, kit.id)

        self.assertEqual((copy.name, copy.handle, copy.label), ("Mara copy", "mara_copy", "Actor"))
        originals, copies = self.repo.items(kit.id), self.repo.items(copy.id)
        self.assertEqual([(i.kind, i.name, i.media_ref, i.text) for i in copies],
                         [(i.kind, i.name, i.media_ref, i.text) for i in originals])
        self.assertTrue({i.id for i in originals}.isdisjoint({i.id for i in copies}))
        self.assertEqual(operations.duplicate_kit(self.repo, self.user_id, kit.id).handle, "mara_copy2")


class TestItems(KitsTestBase):

    def setUp(self):
        super().setUp()
        self.kit = operations.create_kit(self.repo, self.user_id, "Mara")

    def add(self, upload, **kwargs):
        return operations.add_media_item(self.repo, self.user_id, self.kit.id, upload.id, **kwargs)

    def names(self):
        return [(item.name, item.kind) for item in self.repo.items(self.kit.id)]

    def test_default_names_are_by_kind_in_order(self):
        self.add(self.upload("a.png"))
        self.add(self.upload("b.png"))
        self.add(self.upload("v.mp4", "video"))
        self.add(self.upload("s.wav", "audio"))
        operations.add_text_item(self.repo, self.user_id, self.kit.id, "hello")

        self.assertEqual(self.names(), [
            ("image1", "image"), ("image2", "image"), ("video1", "video"), ("audio1", "audio"), ("text1", "text"),
        ])

    def test_an_item_name_is_unique_within_the_kit_and_a_clash_is_suggested(self):
        self.add(self.upload("a.png"), name="face")

        with self.assertRaises(ConflictError) as caught:
            self.add(self.upload("b.png"), name="Face")

        self.assertEqual(caught.exception.extra["suggestion"], "face2")

    def test_the_same_name_can_live_in_two_kits(self):
        other = operations.create_kit(self.repo, self.user_id, "Tomas")
        self.add(self.upload("a.png"), name="face")

        operations.add_media_item(self.repo, self.user_id, other.id, self.upload("b.png").id, name="face")

    def test_rename_and_clash_on_rename(self):
        first = self.add(self.upload("a.png"), name="face")
        second = self.add(self.upload("b.png"), name="profile")

        renamed = operations.update_item(self.repo, self.user_id, self.kit.id, second.id, name="Profile Left")
        self.assertEqual(renamed.name, "profile_left")
        with self.assertRaises(ConflictError):
            operations.update_item(self.repo, self.user_id, self.kit.id, second.id, name="face")
        self.assertEqual(operations.update_item(self.repo, self.user_id, self.kit.id, first.id, name="face").name, "face")

    def test_a_file_that_is_not_the_callers_library_item_is_not_found(self):
        foreign = self.upload("x.png", user_id=self.other_user_id)
        derived = self.uploads.create(Upload(
            user_id=self.user_id, filename="mask.png", media_type="image", purpose="derived_artifact",
        ))

        for upload_id in (foreign.id, derived.id, "nope"):
            with self.assertRaises(NotFoundError):
                operations.add_media_item(self.repo, self.user_id, self.kit.id, upload_id)

    def test_text_items_are_bounded(self):
        with self.assertRaises(InvalidInputError):
            operations.add_text_item(self.repo, self.user_id, self.kit.id, "  ")
        with self.assertRaises(InvalidInputError):
            operations.add_text_item(self.repo, self.user_id, self.kit.id, "x" * 4001)
        operations.add_text_item(self.repo, self.user_id, self.kit.id, "x" * 4000)

    def test_editing_text_only_works_on_text_items(self):
        text = operations.add_text_item(self.repo, self.user_id, self.kit.id, "old")
        image = self.add(self.upload("a.png"))

        edited = operations.update_item(self.repo, self.user_id, self.kit.id, text.id, text="new")
        self.assertEqual(edited.text, "new")
        with self.assertRaises(InvalidInputError):
            operations.update_item(self.repo, self.user_id, self.kit.id, image.id, text="nope")

    def test_a_kit_holds_fifty_items_and_the_next_is_refused(self):
        for index in range(50):
            operations.add_text_item(self.repo, self.user_id, self.kit.id, f"line {index}")

        with self.assertRaises(ConflictError) as caught:
            operations.add_text_item(self.repo, self.user_id, self.kit.id, "one too many")
        self.assertEqual(caught.exception.code, "kit_full")
        self.assertEqual(self.repo.count_items(self.kit.id), 50)

    def test_remove_keeps_positions_contiguous(self):
        a, b, c = (self.add(self.upload(f"{n}.png")) for n in "abc")

        operations.remove_item(self.repo, self.user_id, self.kit.id, b.id)

        self.assertEqual([(i.id, i.position) for i in self.repo.items(self.kit.id)], [(a.id, 0), (c.id, 1)])

    def test_reorder_sets_the_order_models_see(self):
        a, b, c = (self.add(self.upload(f"{n}.png")) for n in "abc")

        operations.reorder_items(self.repo, self.user_id, self.kit.id, [c.id, a.id, b.id])

        self.assertEqual([i.id for i in self.repo.items(self.kit.id)], [c.id, a.id, b.id])
        with self.assertRaises(InvalidInputError):
            operations.reorder_items(self.repo, self.user_id, self.kit.id, [c.id, a.id])

    def test_add_library_items_into_a_new_kit_in_one_call(self):
        uploads = [self.upload("a.png"), self.upload("v.mp4", "video")]

        kit, added = operations.add_library_items(
            self.repo, self.user_id, [u.id for u in uploads], new_kit_name="Props", names={uploads[0].id: "Crate"},
        )

        self.assertEqual((kit.handle, [i.name for i in added]), ("props", ["crate", "video1"]))
        self.assertEqual(self.repo.count_items(kit.id), 2)

    def test_add_library_items_into_an_existing_kit_continues_its_names(self):
        self.add(self.upload("a.png"))

        _kit, added = operations.add_library_items(
            self.repo, self.user_id, [self.upload("b.png").id], kit_id=self.kit.id,
        )

        self.assertEqual([i.name for i in added], ["image2"])

    def test_add_library_items_writes_nothing_when_one_is_invalid(self):
        good = self.upload("a.png")

        with self.assertRaises(NotFoundError):
            operations.add_library_items(self.repo, self.user_id, [good.id, "ghost"], kit_id=self.kit.id)
        with self.assertRaises(ConflictError):
            operations.add_library_items(
                self.repo, self.user_id, [good.id, self.upload("b.png").id], kit_id=self.kit.id,
                names={good.id: "same", self.uploads.get_by_filename("b.png", self.user_id).id: "same"},
            )
        self.assertEqual(self.repo.count_items(self.kit.id), 0)

    def test_add_library_items_needs_exactly_one_target(self):
        upload = self.upload("a.png")
        with self.assertRaises(InvalidInputError):
            operations.add_library_items(self.repo, self.user_id, [upload.id])
        with self.assertRaises(InvalidInputError):
            operations.add_library_items(self.repo, self.user_id, [upload.id], kit_id=self.kit.id, new_kit_name="x")

    def test_add_library_items_respects_the_fifty_item_limit(self):
        for index in range(49):
            operations.add_text_item(self.repo, self.user_id, self.kit.id, f"line {index}")
        uploads = [self.upload("a.png"), self.upload("b.png")]

        with self.assertRaises(ConflictError):
            operations.add_library_items(self.repo, self.user_id, [u.id for u in uploads], kit_id=self.kit.id)
        self.assertEqual(self.repo.count_items(self.kit.id), 49)


class TestDeletedLibraryFile(KitsTestBase):

    def test_the_item_stays_as_missing_when_its_library_file_is_deleted(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        upload = self.upload("a.png")
        item = operations.add_media_item(self.repo, self.user_id, kit.id, upload.id, name="face")

        self.delete_upload(upload)

        stored = self.repo.get_item(kit.id, item.id)
        self.assertIsNotNone(stored)
        self.assertIsNone(stored.media_ref)
        view = KitCatalog(self.repo, self.user_id).by_id(kit.id).items[0]
        self.assertTrue(view.missing)
        self.assertEqual(view.item.kind, "image")

    def test_usage_lists_the_kits_a_file_is_in(self):
        one = operations.create_kit(self.repo, self.user_id, "One")
        two = operations.create_kit(self.repo, self.user_id, "Two")
        upload = self.upload("a.png")
        operations.add_media_item(self.repo, self.user_id, one.id, upload.id)
        operations.add_media_item(self.repo, self.user_id, two.id, upload.id)

        usage = self.repo.usage_for_uploads(self.user_id, [upload.id])

        self.assertEqual({row["kit_id"] for row in usage[upload.id]}, {one.id, two.id})
        self.assertEqual(self.repo.usage_for_uploads(self.other_user_id, [upload.id])[upload.id], [])

    def test_counts_and_thumbnails_summarise_a_kit(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        for index in range(6):
            operations.add_media_item(self.repo, self.user_id, kit.id, self.upload(f"{index}.png").id)
        operations.add_text_item(self.repo, self.user_id, kit.id, "hi")

        counts = self.repo.counts_for([kit.id])[kit.id]
        thumbnails = self.repo.thumbnails_for([kit.id])[kit.id]

        self.assertEqual(counts, {"image": 6, "video": 0, "audio": 0, "text": 1})
        self.assertEqual(len(thumbnails), 4)


class TestCitedInTabs(KitsTestBase):

    def draft(self, tab_id, doc, user_id=None):
        import json
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO tab_drafts (user_id, tab_id, doc, created_at, updated_at) VALUES (?, ?, ?, 'n', 'n')",
                (user_id or self.user_id, tab_id, json.dumps(doc)),
            )

    def test_tabs_citing_the_kit_by_id_are_found_and_split_into_sessions_and_films(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        other = operations.create_kit(self.repo, self.user_id, "Other")
        cite = f"@[references:kit:{kit.id}/it_00000001]"
        self.draft("t1", {"prompt": cite})
        self.draft("t2", {"video_director": {"segments": [{"prompt": cite}]}})
        self.draft("t3", {"prompt": f"@[references:kit:{other.id}]"})
        self.draft("t4", {"prompt": cite}, user_id=self.other_user_id)

        total, films, tabs = self.repo.cited_in_tabs(self.user_id, kit.id)

        self.assertEqual((total, films), (2, 1))
        self.assertEqual({tab["tab_id"]: tab["director"] for tab in tabs}, {"t1": False, "t2": True})

    def test_the_list_is_bounded_but_the_total_is_not(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        for index in range(5):
            self.draft(f"t{index}", {"prompt": kit.id})

        total, _films, tabs = self.repo.cited_in_tabs(self.user_id, kit.id, limit=2)

        self.assertEqual((total, len(tabs)), (5, 2))


def stale_once(repo):
    real = repo.handle_taken
    calls = []

    def check(owner_id, handle, except_kit_id=None):
        calls.append(handle)
        return False if len(calls) == 1 else real(owner_id, handle, except_kit_id)

    return patch.object(repo, "handle_taken", side_effect=check)


class TestConcurrentWrites(KitsTestBase):

    def test_two_creates_of_the_same_path_answer_the_clash_not_a_server_error(self):
        operations.create_kit(self.repo, self.user_id, "Mara")

        with stale_once(self.repo):
            with self.assertRaises(ConflictError) as caught:
                operations.create_kit(self.repo, self.user_id, "Mara")

        self.assertEqual(caught.exception.code, "kit_path_taken")
        self.assertEqual(caught.exception.extra["suggestion"], "mara2")
        self.assertEqual(self.repo.list_kits(self.user_id)[1], 1)

    def test_two_renames_onto_one_path_answer_the_clash(self):
        operations.create_kit(self.repo, self.user_id, "Mara")
        other = operations.create_kit(self.repo, self.user_id, "Tomas")

        with stale_once(self.repo):
            with self.assertRaises(ConflictError) as caught:
                operations.update_kit(self.repo, self.user_id, other.id, handle="mara")

        self.assertEqual(caught.exception.extra["suggestion"], "mara2")
        self.assertEqual(self.repo.get(other.id, self.user_id).handle, "tomas")

    def test_two_adds_with_the_same_item_name_answer_the_clash(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        operations.add_text_item(self.repo, self.user_id, kit.id, "one", "line")

        with patch("src.features.kits.operations.items._item_name", return_value="line"):
            with self.assertRaises(ConflictError) as caught:
                operations.add_text_item(self.repo, self.user_id, kit.id, "two", "line")

        self.assertEqual(caught.exception.code, "item_path_taken")
        self.assertEqual(self.repo.count_items(kit.id), 1)

    def test_a_rename_onto_a_taken_item_name_answers_the_clash(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        operations.add_text_item(self.repo, self.user_id, kit.id, "one", "line")
        second = operations.add_text_item(self.repo, self.user_id, kit.id, "two", "other")
        item = self.repo.get_item(kit.id, second.id)
        item.name = "line"

        with self.assertRaises(ConflictError) as caught:
            self.repo.update_item(item)

        self.assertEqual(caught.exception.extra["suggestion"], "line2")

    def test_the_fifty_item_cap_holds_even_when_the_count_check_was_stale(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        for index in range(49):
            operations.add_text_item(self.repo, self.user_id, kit.id, f"line {index}")

        with patch.object(KitRepository, "count_items", return_value=49):
            operations.add_text_item(self.repo, self.user_id, kit.id, "the fiftieth")
            with self.assertRaises(ConflictError) as caught:
                operations.add_text_item(self.repo, self.user_id, kit.id, "the fifty-first")

        self.assertEqual(caught.exception.code, "kit_full")
        self.assertEqual(self.repo.items(kit.id).__len__(), 50)

    def test_positions_stay_distinct_when_adds_race_on_a_stale_view(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        with patch.object(KitRepository, "count_items", return_value=0):
            for index in range(4):
                operations.add_text_item(self.repo, self.user_id, kit.id, f"line {index}")

        self.assertEqual([item.position for item in self.repo.items(kit.id)], [0, 1, 2, 3])
