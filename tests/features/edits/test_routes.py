from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.edits.routes import EditController, build_router
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.edits.support import SOURCE, STATE, EditsTestBase
from tests.platform.security.test_current_user_keys import FakeResolver

READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
CONTENT_KEY = "pui_" + "c" * 40 + "ZZZZZZ"
GENERATE_KEY = "pui_" + "g" * 40 + "ZZZZZZ"
OTHER_KEY = "pui_" + "o" * 40 + "ZZZZZZ"
SESSION = "a.session.jwt"
OTHER_SESSION = "b.session.jwt"
ADMIN_SESSION = "c.session.jwt"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def account(user_id, account_type=AccountType.USER):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=account_type)


class RoutesBase(EditsTestBase):

    def setUp(self):
        super().setUp()
        controller = EditController(self.manager)
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(edit_controller=controller)))
        self.client = TestClient(app)
        self.admin_id = self.create_test_user("admin_user", "admin", "admin@example.com")
        accounts = {
            SESSION: account(self.user_id),
            OTHER_SESSION: account(self.other_user_id),
            ADMIN_SESSION: account(self.admin_id, AccountType.ADMIN),
        }
        self.auth = Mock()
        self.auth.get_user_from_token.side_effect = lambda token: accounts.get(token)
        self.resolver = FakeResolver()
        self.resolver.add(READ_KEY, account(self.user_id), [S.READ])
        self.resolver.add(CONTENT_KEY, account(self.user_id), [S.CONTENT])
        self.resolver.add(GENERATE_KEY, account(self.user_id), [S.GENERATE])
        self.resolver.add(OTHER_KEY, account(self.other_user_id), [S.READ, S.CONTENT])
        self.previous = (current_user._auth, current_user._key_resolver)
        current_user.set_auth(self.auth)
        current_user.set_key_resolver(self.resolver)
        current_user._key_failures.reset("testclient")

    def tearDown(self):
        current_user._auth, current_user._key_resolver = self.previous
        self.client.close()
        super().tearDown()

    def call(self, method, path="", token=SESSION, **kwargs):
        return self.client.request(method, f"/api/edits{path}", headers=bearer(token), **kwargs)

    def create(self, token=SESSION, **extra):
        body = {"tool": "image", "source": SOURCE, "state": STATE, **extra}
        response = self.call("POST", token=token, json=body)
        assert response.status_code == 200, response.text
        return response.json()["data"]["edit"]


class TestEditRoutes(RoutesBase):

    def test_create_returns_the_edit_with_its_state_at_revision_one(self):
        edit = self.create(name="Poster")

        self.assertEqual((edit["revision"], edit["name"], edit["tool"], edit["state"]), (1, "Poster", "image", STATE))
        self.assertEqual(edit["source"], SOURCE)
        self.assertIsNone(edit["thumbnail_upload_id"])

    def test_create_without_a_tool_means_image_and_without_a_state_an_empty_object(self):
        response = self.call("POST", json={"source": SOURCE})

        edit = response.json()["data"]["edit"]
        self.assertEqual((response.status_code, edit["tool"], edit["state"]), (200, "image", {}))

    def test_create_refuses_an_unknown_tool_and_source_kind_with_400(self):
        tool = self.call("POST", json={"tool": "audio", "source": SOURCE})
        source = self.call("POST", json={"source": {"kind": "disk", "id": "x"}})

        self.assertEqual((tool.status_code, tool.json()["detail"]["error"]), (400, "invalid_edit_tool"))
        self.assertEqual((source.status_code, source.json()["detail"]["error"]), (400, "invalid_edit_source"))

    def test_the_state_must_be_a_json_object(self):
        edit = self.create()
        before = self.row(edit["id"])

        for bad in ([1, 2], "text", 5, None):
            with self.subTest(state=bad):
                created = self.call("POST", json={"source": SOURCE, "state": bad})
                saved = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": bad})
                self.assertEqual((created.status_code, saved.status_code), (422, 422))

        self.assertEqual(self.row(edit["id"]), before)
        self.assertEqual(self.call("GET").json()["data"]["total"], 1)

    def test_a_state_over_the_limit_answers_413_on_create_and_save_and_writes_nothing(self):
        edit = self.create()
        before = self.row(edit["id"])
        over = {"blob": "x" * (256 * 1024)}

        saved = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": over})
        created = self.call("POST", json={"source": SOURCE, "state": over})

        for response in (saved, created):
            self.assertEqual((response.status_code, response.json()["detail"]["error"]), (413, "edit_state_too_large"))
        self.assertEqual(self.row(edit["id"]), before)
        self.assertEqual(self.call("GET").json()["data"]["total"], 1)

    def test_get_returns_the_state_and_the_list_omits_it(self):
        edit = self.create(name="Poster")

        one = self.call("GET", f"/{edit['id']}").json()["data"]["edit"]
        listed = self.call("GET").json()["data"]

        self.assertEqual(one["state"], STATE)
        self.assertEqual((listed["total"], listed["limit"], listed["offset"]), (1, 50, 0))
        self.assertNotIn("state", listed["edits"][0])
        self.assertEqual(listed["edits"][0]["id"], edit["id"])

    def test_list_orders_pages_searches_and_filters_by_tool(self):
        names = ["Alpha", "Beta", "Gamma"]
        for name in names:
            self.create(name=name)

        everything = self.call("GET").json()["data"]
        second = self.call("GET", "?limit=1&offset=1").json()["data"]
        found = self.call("GET", "?search=amm").json()["data"]
        image = self.call("GET", "?tool=image").json()["data"]
        video = self.call("GET", "?tool=video")

        self.assertEqual([edit["name"] for edit in everything["edits"]], ["Gamma", "Beta", "Alpha"])
        self.assertEqual(([edit["name"] for edit in second["edits"]], second["total"]), (["Beta"], 3))
        self.assertEqual([edit["name"] for edit in found["edits"]], ["Gamma"])
        self.assertEqual(image["total"], 3)
        self.assertEqual((video.status_code, video.json()["detail"]["error"]), (400, "invalid_edit_tool"))

    def test_list_rejects_out_of_range_paging(self):
        self.assertEqual(self.call("GET", "?limit=0").status_code, 422)
        self.assertEqual(self.call("GET", "?limit=201").status_code, 422)
        self.assertEqual(self.call("GET", "?offset=-1").status_code, 422)

    def test_save_returns_the_new_revision_without_the_state(self):
        edit = self.create()

        first = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": {"v": 1, "n": 1}})
        second = self.call("PUT", f"/{edit['id']}", json={"revision": 2, "state": {"v": 1, "n": 2}})

        self.assertEqual(first.json()["data"]["edit"]["revision"], 2)
        self.assertEqual(second.json()["data"]["edit"]["revision"], 3)
        self.assertNotIn("state", second.json()["data"]["edit"])
        self.assertEqual(self.call("GET", f"/{edit['id']}").json()["data"]["edit"]["state"], {"v": 1, "n": 2})

    def test_a_stale_revision_answers_409_with_the_current_one_and_leaves_the_row_alone(self):
        edit = self.create()
        self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": {"v": 1, "n": 1}})
        before = self.row(edit["id"])

        stale = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": {"v": 1, "n": 99}})

        self.assertEqual(stale.status_code, 409)
        detail = stale.json()["detail"]
        self.assertEqual((detail["error"], detail["revision"]), ("edit_revision_conflict", 2))
        self.assertEqual(self.row(edit["id"]), before)

    def test_save_sets_and_clears_the_thumbnail_only_when_it_is_sent(self):
        thumb = self.upload("thumb.png")
        edit = self.create()

        set_it = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": STATE, "thumbnail_upload_id": thumb.id})
        kept = self.call("PUT", f"/{edit['id']}", json={"revision": 2, "state": STATE})
        cleared = self.call("PUT", f"/{edit['id']}", json={"revision": 3, "state": STATE, "thumbnail_upload_id": None})
        bad = self.call("PUT", f"/{edit['id']}", json={"revision": 4, "state": STATE, "thumbnail_upload_id": "nope"})

        self.assertEqual(set_it.json()["data"]["edit"]["thumbnail_upload_id"], thumb.id)
        self.assertEqual(kept.json()["data"]["edit"]["thumbnail_upload_id"], thumb.id)
        self.assertIsNone(cleared.json()["data"]["edit"]["thumbnail_upload_id"])
        self.assertEqual((bad.status_code, bad.json()["detail"]["error"]), (400, "invalid_edit_thumbnail"))

    def test_a_save_needs_a_positive_integer_revision_and_a_revision_ahead_conflicts(self):
        edit = self.create()
        before = self.row(edit["id"])
        path = f"/{edit['id']}"

        for bad in ("missing", 0, -3, "abc", 1.5, None, [1]):
            with self.subTest(revision=bad):
                body = {"state": STATE} if bad == "missing" else {"revision": bad, "state": STATE}
                self.assertEqual(self.call("PUT", path, json=body).status_code, 422)
        ahead = self.call("PUT", path, json={"revision": 7, "state": {"v": 1, "ahead": True}})

        self.assertEqual((ahead.status_code, ahead.json()["detail"]["revision"]), (409, 1))
        self.assertEqual(self.row(edit["id"]), before)

    def test_another_users_thumbnail_is_refused_like_a_missing_one_on_create_and_save(self):
        edit = self.create()
        theirs = self.upload("theirs.png", self.other_user_id)
        before = self.row(edit["id"])
        path = f"/{edit['id']}"

        for upload_id in (theirs.id, "up_missing"):
            with self.subTest(upload=upload_id):
                created = self.call("POST", json={"source": SOURCE, "thumbnail_upload_id": upload_id})
                saved = self.call("PUT", path, json={"revision": 1, "state": STATE, "thumbnail_upload_id": upload_id})
                self.assertEqual((created.status_code, saved.status_code), (400, 400))
                self.assertEqual(created.json()["detail"], saved.json()["detail"])
                self.assertEqual(created.json()["detail"]["error"], "invalid_edit_thumbnail")
        foreign = self.call("PUT", path, json={"revision": 1, "state": STATE, "thumbnail_upload_id": theirs.id})
        missing = self.call("PUT", path, json={"revision": 1, "state": STATE, "thumbnail_upload_id": "up_missing"})

        self.assertEqual(foreign.json(), missing.json())
        self.assertEqual(self.row(edit["id"]), before)
        self.assertEqual(self.call("GET").json()["data"]["total"], 1)

    def test_rename_does_not_touch_the_revision_so_the_next_autosave_still_lands(self):
        edit = self.create(name="Old")

        renamed = self.call("PATCH", f"/{edit['id']}", json={"name": "New"})
        saved = self.call("PUT", f"/{edit['id']}", json={"revision": 1, "state": {"v": 1, "n": 1}})

        self.assertEqual((renamed.json()["data"]["edit"]["name"], renamed.json()["data"]["edit"]["revision"]), ("New", 1))
        self.assertEqual((saved.status_code, saved.json()["data"]["edit"]["revision"], saved.json()["data"]["edit"]["name"]), (200, 2, "New"))

    def test_rename_with_null_or_blanks_clears_the_name_and_a_long_name_answers_400_unchanged(self):
        edit = self.create(name="Old")
        path = f"/{edit['id']}"

        for blank in (None, "   "):
            with self.subTest(blank=blank):
                self.call("PATCH", path, json={"name": "Old"})
                renamed = self.call("PATCH", path, json={"name": blank})
                self.assertIsNone(renamed.json()["data"]["edit"]["name"])
        self.call("PATCH", path, json={"name": "Old"})
        long = self.call("PATCH", path, json={"name": "x" * 121})

        self.assertEqual((long.status_code, long.json()["detail"]["error"]), (400, "invalid_edit_name"))
        self.assertEqual(self.call("GET", path).json()["data"]["edit"]["name"], "Old")

    def test_duplicate_returns_a_new_edit_at_revision_one(self):
        edit = self.create(name="Poster")

        copy = self.call("POST", f"/{edit['id']}/duplicate").json()["data"]["edit"]

        self.assertNotEqual(copy["id"], edit["id"])
        self.assertEqual((copy["name"], copy["revision"], copy["state"]), ("Poster copy", 1, STATE))
        self.assertEqual(self.call("GET").json()["data"]["total"], 2)

    def test_delete_removes_the_edit_then_answers_404(self):
        edit = self.create()

        deleted = self.call("DELETE", f"/{edit['id']}")
        again = self.call("DELETE", f"/{edit['id']}")

        self.assertEqual((deleted.status_code, deleted.json()["data"]), (200, {"id": edit["id"], "deleted": True}))
        self.assertEqual(again.status_code, 404)
        self.assertEqual(self.call("GET", f"/{edit['id']}").status_code, 404)

    def test_another_users_edit_is_404_on_every_route_and_stays_untouched(self):
        edit = self.create()
        before = self.row(edit["id"])
        id_path = f"/{edit['id']}"
        attempts = {
            "get": ("GET", id_path, None),
            "save": ("PUT", id_path, {"revision": 1, "state": {"v": 1, "stolen": True}}),
            "rename": ("PATCH", id_path, {"name": "Stolen"}),
            "duplicate": ("POST", f"{id_path}/duplicate", None),
            "delete": ("DELETE", id_path, None),
        }
        unknown = self.call("GET", "/ed_missing", token=OTHER_SESSION)

        for name, (method, path, body) in attempts.items():
            for who, token in (("user", OTHER_SESSION), ("admin", ADMIN_SESSION), ("key", OTHER_KEY)):
                with self.subTest(route=name, caller=who):
                    response = self.call(method, path, token=token, json=body) if body else self.call(method, path, token=token)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["detail"], unknown.json()["detail"])

        self.assertEqual(self.row(edit["id"]), before)
        self.assertEqual(self.call("GET", token=OTHER_SESSION).json()["data"]["total"], 0)
        self.assertEqual(self.call("GET", token=ADMIN_SESSION).json()["data"]["total"], 0)

    def test_a_read_key_may_read_but_not_write_and_a_content_key_may_do_both_a_generate_key_neither(self):
        edit = self.create()
        path = f"/{edit['id']}"

        self.assertEqual(self.call("GET", token=READ_KEY).status_code, 200)
        self.assertEqual(self.call("GET", path, token=READ_KEY).status_code, 200)
        self.assertEqual(self.call("POST", token=READ_KEY, json={"source": SOURCE}).status_code, 403)
        self.assertEqual(self.call("PUT", path, token=READ_KEY, json={"revision": 1, "state": STATE}).status_code, 403)
        self.assertEqual(self.call("PATCH", path, token=READ_KEY, json={"name": "x"}).status_code, 403)
        self.assertEqual(self.call("POST", f"{path}/duplicate", token=READ_KEY).status_code, 403)
        self.assertEqual(self.call("DELETE", path, token=READ_KEY).status_code, 403)
        self.assertEqual(self.call("POST", token=CONTENT_KEY, json={"source": SOURCE}).status_code, 200)
        self.assertEqual(self.call("PUT", path, token=CONTENT_KEY, json={"revision": 1, "state": STATE}).status_code, 200)
        self.assertEqual(self.call("GET", token=GENERATE_KEY).status_code, 403)
        self.assertEqual(self.call("POST", token=GENERATE_KEY, json={"source": SOURCE}).status_code, 403)
