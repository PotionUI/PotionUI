from types import SimpleNamespace

from src.features.kits import operations
from src.features.kits.expansion import KitExpander, field_policies, is_kit_row, split_kit_rows
from src.features.kits.generation import expand_request
from src.features.prompt.resources import resolve_prompt_resources
from tests.features.kits.support import KitsTestBase

SPECS = [
    {"field": "references", "kind": "image", "token": "<Picture @>", "limit": 2, "limit_total": 3},
    {"field": "references", "kind": "video", "token": "<Video @>", "limit": 1},
]
ALL_KINDS = [
    {"field": "references", "kind": "image", "token": "<Picture @>"},
    {"field": "references", "kind": "video", "token": "<Video @>"},
    {"field": "references", "kind": "audio", "token": "<Audio @>"},
]


def native(path, kind="image"):
    return {"path": path, "relative_path": path, "type": kind}


def request(positive="", negative="", form_data=None, segments=None):
    return SimpleNamespace(
        prompts=[SimpleNamespace(positive=positive, negative=negative)],
        segments=segments,
        form_data=form_data if form_data is not None else {},
    )


def template(specs):
    return SimpleNamespace(prompt_resources={"refs": specs})


class ExpansionBase(KitsTestBase):

    def setUp(self):
        super().setUp()
        self.expander = KitExpander(self.repo)
        self.kit = operations.create_kit(self.repo, self.user_id, "Mara")

    def media(self, name, kind="image", filename=None):
        upload = self.upload(filename or f"{name}.{'png' if kind == 'image' else 'mp4' if kind == 'video' else 'wav'}", kind)
        return operations.add_media_item(self.repo, self.user_id, self.kit.id, upload.id, name=name), upload

    def text(self, text, name=None):
        return operations.add_text_item(self.repo, self.user_id, self.kit.id, text, name)

    def cite(self, item=None, field="references"):
        return f"@[{field}:kit:{self.kit.id}" + (f"/{item.id}" if item else "") + "]"

    def go(self, text, specs=ALL_KINDS, form_data=None, loaded=True, negative=""):
        form = form_data if form_data is not None else {"references": []}
        req = request(text, negative, form)
        rows = {"references": [self.kit.id]} if loaded is True else (loaded or {})
        expansion = expand_request(self.expander, self.user_id, req, template(specs), "refs", rows)
        return req, expansion

    def codes(self, expansion):
        return [notice.code for notice in expansion.notices]


class TestSplitting(KitsTestBase):

    def test_kit_rows_leave_the_form_and_are_listed_by_field(self):
        form = {"references": [native("a.png"), {"kit": "kt_aaaaaaaa"}, {"kit": "kt_bbbbbbbb"}], "other": "x"}

        cleaned, rows = split_kit_rows(form)

        self.assertEqual(cleaned["references"], [native("a.png")])
        self.assertEqual(rows, {"references": ["kt_aaaaaaaa", "kt_bbbbbbbb"]})
        self.assertEqual(form["references"][1], {"kit": "kt_aaaaaaaa"})

    def test_a_form_without_kit_rows_is_returned_as_the_same_object(self):
        form = {"references": [native("a.png")], "kit": "plain text"}

        cleaned, rows = split_kit_rows(form)

        self.assertIs(cleaned, form)
        self.assertEqual(rows, {})

    def test_a_media_object_that_happens_to_carry_a_kit_key_is_not_a_row(self):
        self.assertFalse(is_kit_row({"kit": "kt_aaaaaaaa", "path": "uploads/a.png"}))
        self.assertTrue(is_kit_row({"kit": "kt_aaaaaaaa"}))

    def test_policies_come_from_plain_media_specs_only(self):
        policies = field_policies(SPECS + [{"field": "bundles", "source": "stack", "kind": "image", "token": "<P @>"}])

        self.assertEqual(set(policies), {"references"})
        self.assertEqual(policies["references"].kinds, ("image", "video"))
        self.assertEqual((dict(policies["references"].limits), policies["references"].total), ({"image": 2, "video": 1}, 3))


class TestCiteOnly(ExpansionBase):

    def test_a_cited_item_joins_the_pool_named_and_the_marker_becomes_a_normal_one(self):
        face, upload = self.media("face")
        self.media("profile")

        req, expansion = self.go(f"a portrait of {self.cite(face)}")

        self.assertEqual(req.prompts[0].positive, "a portrait of @[references:uploads/face.png]")
        items = req.form_data["references"]
        self.assertEqual([item["relative_path"] for item in items], ["uploads/face.png"])
        self.assertEqual(items[0]["label"], "face")
        self.assertEqual(items[0]["kit_item"]["name"], "face")
        self.assertEqual(items[0]["type"], "image")
        self.assertEqual(expansion.notices, [])

    def test_uncited_items_and_a_loaded_kit_nobody_cites_add_nothing(self):
        self.media("face")
        self.media("profile")
        original = {"references": [native("uploads/own.png")]}

        req, expansion = self.go("an empty street", form_data=original)

        self.assertIs(req.form_data, original)
        self.assertEqual(req.prompts[0].positive, "an empty street")
        self.assertEqual(expansion.used, [])

    def test_only_the_cited_of_several_items_are_added_in_citation_order(self):
        a, _ = self.media("a")
        b, _ = self.media("b")
        c, _ = self.media("c")

        req, _ = self.go(f"{self.cite(c)} then {self.cite(a)} and {self.cite(c)} again")

        self.assertEqual([i["label"] for i in req.form_data["references"]], ["c", "a"])
        self.assertNotIn("uploads/b.png", str(req.form_data))

    def test_the_final_tokens_number_kit_items_after_the_natives_of_their_kind(self):
        face, _ = self.media("face")
        form = {"references": [native("uploads/own1.png"), native("uploads/own2.png")]}

        req, _ = self.go(f"{self.cite(face)} beside @[references:uploads/own2.png]", form_data=form)
        final, problems = resolve_prompt_resources(req.prompts[0].positive, ALL_KINDS, req.form_data)

        self.assertEqual((final, problems), ("<Picture 3> beside <Picture 2>", {}))

    def test_a_kit_item_already_present_as_a_native_is_not_added_twice(self):
        face, _ = self.media("face")
        form = {"references": [native("uploads/face.png")]}

        req, _ = self.go(self.cite(face), form_data=form)

        self.assertEqual(len(req.form_data["references"]), 1)
        self.assertNotIn("kit_item", req.form_data["references"][0])

    def test_old_style_references_and_markers_pass_through_untouched(self):
        original = {"references": [native("uploads/a.png"), native("uploads/v.mp4", "video")], "seed": 3}
        text = "@[references:uploads/a.png] runs past @[references:uploads/v.mp4]"

        req, expansion = self.go(text, form_data=original, loaded={})

        self.assertIs(req.form_data, original)
        self.assertEqual(req.prompts[0].positive, text)
        self.assertEqual((expansion.notices, expansion.used), ([], []))

    def test_negative_prompts_and_segments_are_scanned_too(self):
        face, _ = self.media("face")
        segment = SimpleNamespace(text=f"close on {self.cite(face)}")
        req = request("x", f"not {self.cite(face)}", {"references": []}, segments=[segment])

        expand_request(self.expander, self.user_id, req, template(ALL_KINDS), "refs", {"references": [self.kit.id]})

        self.assertEqual(req.prompts[0].negative, "not @[references:uploads/face.png]")
        self.assertEqual(segment.text, "close on @[references:uploads/face.png]")
        self.assertEqual(len(req.form_data["references"]), 1)


class TestBareKit(ExpansionBase):

    def test_a_bare_kit_places_media_then_text_in_kit_order(self):
        self.text("She speaks softly.", "line")
        self.media("face")
        self.media("profile")
        self.text("Short hair.", "hair")

        req, _ = self.go(f"Scene: {self.cite()} end")

        self.assertEqual(
            req.prompts[0].positive,
            "Scene: @[references:uploads/face.png] @[references:uploads/profile.png] She speaks softly. Short hair. end",
        )

    def test_a_bare_kit_stops_at_the_mode_limit_and_reports_what_did_not_fit(self):
        for name in ("a", "b", "c"):
            self.media(name)
        self.media("clip", "video")
        self.media("clip2", "video")

        req, expansion = self.go(self.cite(), specs=SPECS)

        kept = [item["label"] for item in req.form_data["references"]]
        self.assertEqual(kept, ["a", "b", "clip"])
        skipped = {notice.item: notice.code for notice in expansion.notices}
        self.assertEqual(skipped, {"c": "limit", "clip2": "limit"})
        self.assertIn("at most 2 images", next(n.message for n in expansion.notices if n.item == "c"))

    def test_the_total_limit_counts_every_kind(self):
        self.media("a")
        self.media("clip", "video")
        self.media("b")

        req, expansion = self.go(self.cite(), specs=SPECS, form_data={"references": [native("uploads/own.png")]})

        self.assertEqual([i.get("label") for i in req.form_data["references"]], [None, "a", "clip"])
        self.assertEqual([n.item for n in expansion.notices], ["b"])

    def test_natives_count_against_the_limit(self):
        self.media("a")
        form = {"references": [native("uploads/o1.png"), native("uploads/o2.png")]}

        _req, expansion = self.go(self.cite(), specs=SPECS, form_data=form)

        self.assertEqual(self.codes(expansion), ["limit"])

    def test_an_item_cited_alone_and_by_a_bare_cite_is_added_once(self):
        face, _ = self.media("face")

        req, _ = self.go(f"{self.cite(face)} {self.cite()}")

        self.assertEqual(len(req.form_data["references"]), 1)


class TestSkipped(ExpansionBase):

    def test_an_unsupported_kind_is_skipped_with_the_models_own_words(self):
        voice, _ = self.media("voice", "audio")
        face, _ = self.media("face")

        req, expansion = self.go(f"{self.cite(voice)} {self.cite(face)}", specs=SPECS)

        self.assertEqual(req.prompts[0].positive.strip(), "@[references:uploads/face.png]")
        self.assertEqual(
            [(n.code, n.message) for n in expansion.notices],
            [("unsupported_kind", "This model does not take audio: `voice` is skipped.")],
        )
        self.assertEqual([i["label"] for i in req.form_data["references"]], ["face"])

    def test_text_items_are_never_unsupported(self):
        line = self.text("quietly", "line")

        req, expansion = self.go(f"say it {self.cite(line)}", specs=[SPECS[0]])

        self.assertEqual(req.prompts[0].positive, "say it quietly")
        self.assertEqual(expansion.notices, [])
        self.assertEqual(expansion.used[0]["kind"], "text")

    def test_a_deleted_library_file_is_skipped_never_blocking(self):
        face, upload = self.media("face")
        profile, _ = self.media("profile")
        self.delete_upload(upload)

        req, expansion = self.go(f"{self.cite(face)} and {self.cite(profile)}")

        self.assertEqual(self.codes(expansion), ["file_missing"])
        self.assertEqual([i["label"] for i in req.form_data["references"]], ["profile"])

    def test_a_removed_item_is_named_in_a_notice(self):
        face, _ = self.media("face")
        marker = self.cite(face)
        operations.remove_item(self.repo, self.user_id, self.kit.id, face.id)

        req, expansion = self.go(f"x {marker} y")

        self.assertEqual(req.prompts[0].positive, "x  y")
        self.assertEqual([(n.code, n.item, n.kit) for n in expansion.notices], [("item_removed", "it_" + face.id[3:], "mara")])

    def test_a_deleted_kit_is_skipped(self):
        face, _ = self.media("face")
        marker = self.cite(face)
        operations.delete_kit(self.repo, self.user_id, self.kit.id)

        req, expansion = self.go(marker, loaded={"references": [self.kit.id]})

        self.assertEqual(req.prompts[0].positive, "")
        self.assertEqual(set(self.codes(expansion)), {"kit_deleted"})

    def test_citing_a_kit_that_is_not_loaded_does_not_load_it(self):
        face, _ = self.media("face")

        req, expansion = self.go(self.cite(face), loaded={})

        self.assertEqual(req.form_data["references"], [])
        self.assertEqual(self.codes(expansion), ["kit_not_loaded"])

    def test_another_owners_kit_reads_as_deleted(self):
        other = operations.create_kit(self.repo, self.other_user_id, "Theirs")
        upload = self.upload("t.png", user_id=self.other_user_id)
        item = operations.add_media_item(self.repo, self.other_user_id, other.id, upload.id)

        req, expansion = self.go(f"@[references:kit:{other.id}/{item.id}]", loaded={"references": [other.id]})

        self.assertEqual(req.form_data["references"], [])
        self.assertEqual(set(self.codes(expansion)), {"kit_deleted"})

    def test_a_field_the_mode_does_not_map_is_reported(self):
        face, _ = self.media("face")

        req, expansion = self.go(self.cite(face, field="elsewhere"), loaded={"elsewhere": [self.kit.id]})

        self.assertIn("field_not_supported", self.codes(expansion))
        self.assertNotIn("kit_item", str(req.form_data))


class TestPathsAndRenames(ExpansionBase):

    def test_a_plain_path_in_a_prompt_resolves_by_name(self):
        self.media("face")

        req, _ = self.go("look at @kits.mara.face now")

        self.assertEqual(req.prompts[0].positive, "look at @[references:uploads/face.png] now")

    def test_a_bare_path_and_trailing_punctuation(self):
        self.media("face")

        req, _ = self.go("It is @kits.mara.")

        self.assertEqual(req.prompts[0].positive, "It is @[references:uploads/face.png].")

    def test_a_dot_after_a_bare_path_that_is_not_an_item_stays_text(self):
        self.media("face")

        req, _ = self.go("@kits.mara.Then he left")

        self.assertEqual(req.prompts[0].positive, "@[references:uploads/face.png].Then he left")

    def test_a_renamed_kit_and_item_still_resolve_through_their_ids(self):
        face, _ = self.media("face")
        marker = self.cite(face)
        operations.update_kit(self.repo, self.user_id, self.kit.id, name="Mara Voss", handle="mara_voss")
        operations.update_item(self.repo, self.user_id, self.kit.id, face.id, name="portrait")

        req, expansion = self.go(marker)

        self.assertEqual(req.prompts[0].positive, "@[references:uploads/face.png]")
        self.assertEqual(req.form_data["references"][0]["kit_item"]["name"], "portrait")
        self.assertEqual(expansion.notices, [])

    def test_an_old_path_redirects_until_another_kit_takes_it(self):
        self.media("face")
        operations.update_kit(self.repo, self.user_id, self.kit.id, handle="mara_voss")

        req, expansion = self.go("@kits.mara.face")
        self.assertEqual(req.prompts[0].positive, "@[references:uploads/face.png]")

        operations.create_kit(self.repo, self.user_id, "Mara")
        req, expansion = self.go("@kits.mara.face")
        self.assertEqual(req.form_data["references"], [])
        self.assertEqual([n.code for n in expansion.notices], ["kit_not_loaded"])

    def test_an_email_address_is_not_a_citation(self):
        req, expansion = self.go("write to jo@kits.mara.example")

        self.assertEqual(req.prompts[0].positive, "write to jo@kits.mara.example")
        self.assertEqual(expansion.notices, [])


class TestDirector(ExpansionBase):

    def director(self, *prompts):
        return {
            "schema_version": 1, "mode": "director",
            "settings": {"fps": 24, "duration": None, "resolution": "", "seed": 7},
            "segments": [{"id": f"s{i}", "prompt": prompt, "frames": 81} for i, prompt in enumerate(prompts)],
            "media": [], "audio": [], "ic_lora": [],
        }

    def test_only_kit_items_a_shot_cites_enter_the_film_pool(self):
        face, _ = self.media("face")
        prop, _ = self.media("prop")
        self.media("never")
        document = self.director(f"{self.cite(face)} walks", f"{self.cite(prop)} on a table", "an empty street")

        req = request("", "", {"references": [native("uploads/own.png")], "video_director": document})
        expansion = expand_request(
            self.expander, self.user_id, req, template(ALL_KINDS), "refs", {"references": [self.kit.id]},
        )

        pool = req.form_data["references"]
        self.assertEqual([item.get("label") for item in pool], [None, "face", "prop"])
        self.assertNotIn("never", str(req.form_data))
        shots = [segment["prompt"] for segment in req.form_data["video_director"]["segments"]]
        self.assertEqual(shots, ["@[references:uploads/face.png] walks", "@[references:uploads/prop.png] on a table", "an empty street"])
        self.assertEqual([entry["name"] for entry in expansion.used], ["face", "prop"])
        self.assertIs(document["segments"][0]["prompt"].startswith("@[references:kit:"), True)

    def test_the_shots_normalize_into_the_expanded_pool_with_kit_names(self):
        from src.features.video_director.normalize import normalize_video_director
        from tests.features.video_director.test_normalize import H3_REFS_CAPS

        face, _ = self.media("face")
        prop, _ = self.media("prop")
        self.media("never")
        document = self.director(f"{self.cite(prop)} then {self.cite(face)}", "nothing cited")
        req = request("", "", {"references": [], "video_director": document})
        expand_request(self.expander, self.user_id, req, template(ALL_KINDS), "refs", {"references": [self.kit.id]})

        normalized = normalize_video_director(
            req.form_data["video_director"], {**H3_REFS_CAPS, "reference_fields": ["references"]},
            "/tmp/kits-storage", req.form_data, ALL_KINDS,
        )

        first, second = normalized["segments"]
        self.assertEqual(first["reference_indices"], [0, 1])
        self.assertEqual(first["prompt"], "<Picture 1> then <Picture 2>")
        self.assertEqual(second["reference_indices"], [])
        self.assertEqual(len(req.form_data["references"]), 2)

    def test_pool_entries_know_their_kit_item_names(self):
        from src.features.prompt.reference_pool import build_reference_pool, pool_entry_name
        from src.features.video_director.shot_references import derive_shot_references

        face, _ = self.media("face")
        req, _ = self.go(self.cite(face), form_data={"references": [native("uploads/own.png")]})
        pool, _problems = build_reference_pool(["references"], req.form_data, ALL_KINDS)

        self.assertEqual([pool_entry_name(entry) for entry in pool], [None, "face"])
        derived = derive_shot_references(["@[references:uploads/face.png]"], pool, ALL_KINDS)
        self.assertEqual((derived.indices, derived.names), ([1], ["face"]))
