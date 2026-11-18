from tests.features.edits.support import STATE
from tests.features.edits.test_routes import OTHER_SESSION, SESSION, RoutesBase

CLIP_STATE = {
    "v": 1,
    "stack": {"steps": [{"id": "s1", "tool": "trim", "params": {"start": 1, "end": 4}}], "cursor": 1},
    "clip": {"duration_seconds": 12.5, "width": 1280, "height": 720},
}
CLIP_SOURCE = {"kind": "library", "id": "up_clip", "name": "beach.mp4"}


class TestVideoEdits(RoutesBase):

    def make(self, token=SESSION, state=CLIP_STATE, **extra):
        return self.create(token=token, tool="video", source=CLIP_SOURCE, state=state, **extra)

    def test_a_clip_edit_is_created_with_its_steps_and_clip_facts(self):
        edit = self.make(name="Beach")

        self.assertEqual((edit["tool"], edit["revision"], edit["state"]), ("video", 1, CLIP_STATE))
        self.assertEqual(edit["clip"], {"duration_seconds": 12.5, "width": 1280, "height": 720})
        self.assertEqual(edit["source"], CLIP_SOURCE)

    def test_the_list_filters_by_tool_and_carries_the_clip_facts_without_the_state(self):
        self.create(name="Picture", state=STATE)
        clip = self.make(name="Clip")

        videos = self.call("GET", "?tool=video").json()["data"]
        images = self.call("GET", "?tool=image").json()["data"]
        everything = self.call("GET").json()["data"]

        self.assertEqual([edit["id"] for edit in videos["edits"]], [clip["id"]])
        self.assertEqual(videos["edits"][0]["clip"], clip["clip"])
        self.assertNotIn("state", videos["edits"][0])
        self.assertEqual((images["total"], everything["total"]), (1, 2))
        self.assertNotIn("clip", images["edits"][0])

    def test_saving_updates_the_state_and_the_clip_facts_and_a_stale_revision_conflicts(self):
        edit = self.make()
        shorter = {**CLIP_STATE, "clip": {"duration_seconds": 3, "width": 640, "height": 360}}

        saved = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": shorter})
        stale = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": CLIP_STATE})
        opened = self.call("GET", f"/{edit['id']}").json()["data"]["edit"]
        listed = self.call("GET", "?tool=video").json()["data"]["edits"][0]

        self.assertEqual(saved.status_code, 200)
        detail = stale.json()["detail"]
        self.assertEqual((stale.status_code, detail["error"], detail["revision"]), (409, "edit_revision_conflict", 2))
        self.assertEqual((opened["state"], opened["clip"]), (shorter, shorter["clip"]))
        self.assertEqual(listed["clip"], shorter["clip"])

    def test_malformed_clip_facts_are_dropped_not_stored_as_given(self):
        state = {"v": 1, "clip": {"duration_seconds": -4, "width": "wide", "height": 720.9}}

        edit = self.make(state=state)

        self.assertEqual(edit["clip"], {"height": 720})
        self.assertIsNone(self.make(state={"v": 1})["clip"])

    def test_rename_duplicate_and_delete_work_for_clips(self):
        edit = self.make()

        renamed = self.call("PATCH", f"/{edit['id']}", json={"name": "  Cut   one "}).json()["data"]["edit"]
        copy = self.call("POST", f"/{edit['id']}/duplicate").json()["data"]["edit"]
        deleted = self.call("DELETE", f"/{edit['id']}")

        self.assertEqual((renamed["name"], renamed["clip"]), ("Cut one", edit["clip"]))
        self.assertEqual((copy["tool"], copy["name"], copy["state"], copy["clip"]), ("video", "Cut one copy", CLIP_STATE, edit["clip"]))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.call("GET", f"/{edit['id']}").status_code, 404)

    def test_another_users_clip_edit_is_invisible_and_untouchable(self):
        edit = self.make()
        path = f"/{edit['id']}"
        attempts = (
            ("GET", path, {}),
            ("PUT", path, {"json": {"revision": 1, "state": CLIP_STATE}}),
            ("PATCH", path, {"json": {"name": "x"}}),
            ("POST", f"{path}/duplicate", {}),
            ("DELETE", path, {}),
        )

        for method, target, extra in attempts:
            with self.subTest(method=method, target=target):
                response = self.call(method, target, token=OTHER_SESSION, **extra)
                self.assertEqual((response.status_code, response.json()["detail"]["error"]), (404, "edit_not_found"))
        self.assertEqual(self.call("GET", "?tool=video", token=OTHER_SESSION).json()["data"]["total"], 0)
        self.assertEqual(self.call("GET", path).status_code, 200)

    def test_a_clip_state_over_the_limit_answers_413(self):
        edit = self.make()

        saved = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": {"blob": "x" * (256 * 1024)}})

        self.assertEqual((saved.status_code, saved.json()["detail"]["error"]), (413, "edit_state_too_large"))
