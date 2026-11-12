import io
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.kits.expansion import KitExpander
from src.features.kits.routes import KitController, build_router
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.kits.support import KitsTestBase
from tests.features.kits.test_expansion import ALL_KINDS
from tests.platform.security.test_current_user_keys import FakeResolver

READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
CONTENT_KEY = "pui_" + "c" * 40 + "ZZZZZZ"
BOTH_KEY = "pui_" + "b" * 40 + "ZZZZZZ"
OTHER_KEY = "pui_" + "o" * 40 + "ZZZZZZ"
SESSION = "a.session.jwt"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def account(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


class StoredFile:
    def __init__(self, upload_id):
        self.id = upload_id


class FakeMediaStore:
    def __init__(self):
        self.upload_id = None
        self.checked = []

    def check_upload_size(self, user_id, size, purpose, content_type):
        self.checked.append((user_id, purpose, content_type))

    async def upload_media(self, **kwargs):
        self.received = kwargs
        return StoredFile(self.upload_id)


class RoutesBase(KitsTestBase):

    def setUp(self):
        super().setUp()
        self.media_store = FakeMediaStore()
        self.template = SimpleNamespace(prompt_resources={"refs": ALL_KINDS})
        controller = KitController(
            self.repo, KitExpander(self.repo), lambda preset_id: self.template if preset_id == "h3" else None,
            self.media_store,
        )
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(kit_controller=controller)))
        self.client = TestClient(app)
        self.auth = Mock()
        self.auth.get_user_from_token.side_effect = lambda token: account(self.user_id) if token == SESSION else None
        self.resolver = FakeResolver()
        self.resolver.add(READ_KEY, account(self.user_id), [S.READ])
        self.resolver.add(CONTENT_KEY, account(self.user_id), [S.CONTENT])
        self.resolver.add(BOTH_KEY, account(self.user_id), [S.READ, S.CONTENT])
        self.resolver.add(OTHER_KEY, account(self.other_user_id), [S.READ, S.CONTENT])
        self.previous = (current_user._auth, current_user._key_resolver)
        current_user.set_auth(self.auth)
        current_user.set_key_resolver(self.resolver)
        current_user._key_failures.reset("testclient")

    def tearDown(self):
        current_user._auth, current_user._key_resolver = self.previous
        self.client.close()
        super().tearDown()

    def call(self, method, path, token=SESSION, **kwargs):
        return self.client.request(method, f"/api/kits{path}", headers=bearer(token), **kwargs)

    def kit(self, name="Mara", **extra):
        response = self.call("POST", "", json={"name": name, **extra})
        assert response.status_code == 200, response.text
        return response.json()["data"]["kit"]


class TestKitRoutes(RoutesBase):

    def test_create_returns_the_kit_with_its_stable_id_and_path(self):
        kit = self.kit("Old harbour", label="Location")

        self.assertRegex(kit["id"], r"^kt_[0-9a-f]{8}$")
        self.assertEqual((kit["path"], kit["handle"], kit["label"], kit["items"]), ("kits.old_harbour", "old_harbour", "Location", []))
        self.assertEqual(kit["ref"], f"kit:{kit['id']}")

    def test_a_taken_path_answers_409_with_a_suggestion(self):
        self.kit("Mara")

        response = self.call("POST", "", json={"name": "Other", "handle": "mara"})

        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual((detail["error"], detail["suggestion"]), ("kit_path_taken", "mara2"))

    def test_an_invalid_path_answers_400(self):
        response = self.call("POST", "", json={"name": "Mara", "handle": "1bad"})

        self.assertEqual((response.status_code, response.json()["detail"]["error"]), (400, "invalid_kit_path"))

    def test_list_carries_counts_thumbnails_labels_and_filters(self):
        kit = self.kit("Mara", label="Actor")
        self.kit("Harbour", label="Location")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id})
        self.call("POST", f"/{kit['id']}/items", json={"text": "calm"})

        everything = self.call("GET", "").json()["data"]
        actors = self.call("GET", "?label=Actor").json()["data"]

        self.assertEqual((everything["total"], everything["labels"]), (2, [{"label": "Actor", "count": 1}, {"label": "Location", "count": 1}]))
        row = actors["kits"][0]
        self.assertEqual((row["counts"], row["item_count"]), ({"image": 1, "video": 0, "audio": 0, "text": 1}, 2))
        self.assertEqual(row["thumbnails"][0]["url"], "/api/media/uploads/a.png")
        self.assertEqual(row["thumbnails"][0]["thumbnail_small"], "/api/media/uploads/a.png?size=small")
        self.assertNotIn("items", row)

    def test_patch_renames_and_clears_the_label_only_when_sent(self):
        kit = self.kit("Mara", label="Actor")

        renamed = self.call("PATCH", f"/{kit['id']}", json={"name": "Mara Voss"}).json()["data"]["kit"]
        cleared = self.call("PATCH", f"/{kit['id']}", json={"label": None}).json()["data"]["kit"]
        moved = self.call("PATCH", f"/{kit['id']}", json={"handle": "Mara V"}).json()["data"]["kit"]

        self.assertEqual((renamed["name"], renamed["handle"], renamed["label"]), ("Mara Voss", "mara", "Actor"))
        self.assertIsNone(cleared["label"])
        self.assertEqual(moved["handle"], "mara_v")

    def test_items_have_paths_refs_media_and_text(self):
        kit = self.kit("Mara")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id, "name": "face"})
        shown = self.call("POST", f"/{kit['id']}/items", json={"text": "calm voice"}).json()["data"]["kit"]

        face, line = shown["items"]
        self.assertEqual((face["path"], face["kind"], face["missing"], face["text"]), ("kits.mara.face", "image", False, None))
        self.assertEqual(face["ref"], f"kit:{kit['id']}/{face['id']}")
        self.assertEqual(face["media"]["upload_id"], upload.id)
        self.assertEqual((line["name"], line["text"], line["media"]), ("text1", "calm voice", None))
        self.assertEqual(shown["counts"]["text"], 1)

    def test_an_item_needs_exactly_one_source(self):
        kit = self.kit("Mara")

        neither = self.call("POST", f"/{kit['id']}/items", json={})
        both = self.call("POST", f"/{kit['id']}/items", json={"upload_id": "x", "text": "y"})

        self.assertEqual([neither.status_code, both.status_code], [400, 400])

    def test_rename_remove_and_reorder_items(self):
        kit = self.kit("Mara")
        for name in "abc":
            self.call("POST", f"/{kit['id']}/items", json={"upload_id": self.upload(f"{name}.png").id, "name": name})
        items = self.call("GET", f"/{kit['id']}").json()["data"]["kit"]["items"]
        a, b, c = (item["id"] for item in items)

        self.call("PATCH", f"/{kit['id']}/items/{a}", json={"name": "Face"})
        self.call("DELETE", f"/{kit['id']}/items/{b}")
        shown = self.call("PUT", f"/{kit['id']}/order", json={"ids": [c, a]}).json()["data"]["kit"]
        bad = self.call("PUT", f"/{kit['id']}/order", json={"ids": [c]})

        self.assertEqual([item["name"] for item in shown["items"]], ["c", "face"])
        self.assertEqual(bad.status_code, 400)

    def test_kits_reorder_duplicate_and_delete(self):
        first, second = self.kit("One"), self.kit("Two")

        self.call("PUT", "/order", json={"ids": [second["id"], first["id"]]})
        listed = [row["name"] for row in self.call("GET", "").json()["data"]["kits"]]
        copy = self.call("POST", f"/{first['id']}/duplicate").json()["data"]["kit"]
        deleted = self.call("DELETE", f"/{first['id']}")
        gone = self.call("GET", f"/{first['id']}")

        self.assertEqual(listed, ["Two", "One"])
        self.assertEqual(copy["name"], "One copy")
        self.assertEqual((deleted.json()["data"], gone.status_code), ({"id": first["id"], "deleted": True}, 404))

    def test_add_library_items_creates_a_kit_in_one_call(self):
        uploads = [self.upload("a.png"), self.upload("b.mp4", "video")]

        response = self.call("POST", "/add-library-items", json={
            "upload_ids": [u.id for u in uploads], "new_kit": {"name": "Props", "label": "Prop"},
            "names": {uploads[0].id: "crate"},
        })

        data = response.json()["data"]
        self.assertEqual([item["name"] for item in data["kit"]["items"]], ["crate", "video1"])
        self.assertEqual(len(data["added"]), 2)

    def test_add_library_items_into_an_existing_kit_and_the_clash_answer(self):
        kit = self.kit("Mara")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id, "name": "face"})

        clash = self.call("POST", "/add-library-items", json={
            "upload_ids": [self.upload("b.png").id], "kit_id": kit["id"], "names": {},
        })
        named = self.call("POST", "/add-library-items", json={
            "upload_ids": [self.upload("c.png").id], "kit_id": kit["id"],
            "names": {self.uploads.get_by_filename("c.png", self.user_id).id: "face"},
        })

        self.assertEqual(clash.status_code, 200)
        self.assertEqual(named.status_code, 409)

    def test_a_file_uploaded_into_a_kit_lands_in_the_library_first(self):
        kit = self.kit("Mara")
        self.media_store.upload_id = self.upload("fresh.png").id

        response = self.call(
            "POST", f"/{kit['id']}/items/upload", files={"file": ("fresh.png", io.BytesIO(b"x"), "image/png")},
            data={"name": "fresh"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["kit"]["items"][0]["path"], "kits.mara.fresh")
        self.assertEqual(self.media_store.received["purpose"], "user_upload")
        self.assertEqual(self.media_store.received["user_id"], self.user_id)

    def test_usage_reports_the_kits_a_library_file_is_in(self):
        one, two = self.kit("One"), self.kit("Two")
        upload = self.upload("a.png")
        for kit in (one, two):
            self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id})

        usage = self.call("GET", f"/usage?upload_ids={upload.id},nope").json()["data"]["usage"]

        self.assertEqual(usage[upload.id]["count"], 2)
        self.assertEqual({row["kit_name"] for row in usage[upload.id]["items"]}, {"One", "Two"})
        self.assertEqual(usage["nope"]["count"], 0)

    def test_a_deleted_library_file_leaves_the_item_missing_in_the_response(self):
        kit = self.kit("Mara")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id})

        self.delete_upload(upload)

        item = self.call("GET", f"/{kit['id']}").json()["data"]["kit"]["items"][0]
        self.assertEqual((item["missing"], item["media"], item["kind"]), (True, None, "image"))


class TestKitCitations(RoutesBase):

    def test_the_delete_confirm_lists_the_tabs_that_cite_the_kit(self):
        import json
        kit = self.kit("Mara")
        with self.db.get_cursor() as cursor:
            for tab, doc in (("t1", {"p": kit["id"]}), ("t2", {"video_director": {}, "p": kit["id"]}), ("t3", {})):
                cursor.execute(
                    "INSERT INTO tab_drafts (user_id, tab_id, doc, created_at, updated_at) VALUES (?, ?, ?, 'n', 'n')",
                    (self.user_id, tab, json.dumps(doc)),
                )

        data = self.call("GET", f"/{kit['id']}/usage").json()["data"]

        self.assertEqual((data["total"], data["sessions"], data["films"]), (2, 1, 1))
        self.assertEqual({tab["tab_id"] for tab in data["tabs"]}, {"t1", "t2"})

    def test_it_is_404_for_another_users_kit_and_needs_read_scope(self):
        kit = self.kit("Mara")

        self.assertEqual(self.call("GET", f"/{kit['id']}/usage", token=OTHER_KEY).status_code, 404)
        self.assertEqual(self.call("GET", f"/{kit['id']}/usage", token=CONTENT_KEY).status_code, 403)
        self.assertEqual(self.call("GET", f"/{kit['id']}/usage", token=READ_KEY).status_code, 200)


class TestOwnership(RoutesBase):

    def test_another_users_kit_is_404_everywhere(self):
        kit = self.kit("Mara")
        item = self.call("POST", f"/{kit['id']}/items", json={"text": "hi"}).json()["data"]["kit"]["items"][0]
        theirs = self.upload("t.png", user_id=self.other_user_id)

        for method, path, body in (
            ("GET", f"/{kit['id']}", None),
            ("PATCH", f"/{kit['id']}", {"name": "x"}),
            ("DELETE", f"/{kit['id']}", None),
            ("POST", f"/{kit['id']}/duplicate", None),
            ("POST", f"/{kit['id']}/items", {"text": "x"}),
            ("PATCH", f"/{kit['id']}/items/{item['id']}", {"name": "x"}),
            ("DELETE", f"/{kit['id']}/items/{item['id']}", None),
            ("PUT", f"/{kit['id']}/order", {"ids": [item["id"]]}),
        ):
            response = self.call(method, path, token=OTHER_KEY, **({"json": body} if body is not None else {}))
            self.assertEqual(response.status_code, 404, (method, path))
        self.assertEqual(self.call("GET", f"/{kit['id']}").status_code, 200)
        self.assertEqual(self.call("GET", "", token=OTHER_KEY).json()["data"]["kits"], [])
        mine = self.call("POST", "", json={"name": "Mine"}).json()["data"]["kit"]
        stolen = self.call("POST", f"/{mine['id']}/items", json={"upload_id": theirs.id})
        self.assertEqual(stolen.status_code, 404)

    def test_the_same_path_name_is_free_for_another_user(self):
        self.kit("Mara")

        response = self.call("POST", "", token=OTHER_KEY, json={"name": "Mara"})

        self.assertEqual(response.json()["data"]["kit"]["handle"], "mara")

    def test_the_usage_listing_never_shows_another_users_kits(self):
        kit = self.kit("Mara")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id})

        usage = self.call("GET", f"/usage?upload_ids={upload.id}", token=OTHER_KEY).json()["data"]["usage"]

        self.assertEqual(usage[upload.id]["count"], 0)


class TestKeyScopes(RoutesBase):

    def test_a_read_key_lists_and_reads_but_cannot_mutate(self):
        kit = self.kit("Mara")

        listed = self.call("GET", "", token=READ_KEY)
        shown = self.call("GET", f"/{kit['id']}", token=READ_KEY)
        usage = self.call("GET", "/usage?upload_ids=x", token=READ_KEY)
        refused = [
            self.call("POST", "", token=READ_KEY, json={"name": "x"}),
            self.call("PATCH", f"/{kit['id']}", token=READ_KEY, json={"name": "x"}),
            self.call("DELETE", f"/{kit['id']}", token=READ_KEY),
            self.call("POST", f"/{kit['id']}/items", token=READ_KEY, json={"text": "x"}),
            self.call("PUT", "/order", token=READ_KEY, json={"ids": []}),
            self.call("POST", "/add-library-items", token=READ_KEY, json={"upload_ids": ["x"], "new_kit": {"name": "x"}}),
        ]

        self.assertEqual([listed.status_code, shown.status_code, usage.status_code], [200, 200, 200])
        self.assertEqual([r.status_code for r in refused], [403] * 6)
        self.assertEqual(self.call("GET", f"/{kit['id']}").json()["data"]["kit"]["name"], "Mara")

    def test_a_content_key_mutates_but_cannot_read(self):
        created = self.call("POST", "", token=CONTENT_KEY, json={"name": "Via key"})
        kit_id = created.json()["data"]["kit"]["id"]

        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.call("GET", "", token=CONTENT_KEY).status_code, 403)
        self.assertEqual(self.call("GET", f"/{kit_id}", token=CONTENT_KEY).status_code, 403)
        self.assertEqual(self.call("DELETE", f"/{kit_id}", token=CONTENT_KEY).status_code, 200)

    def test_a_key_never_reaches_beyond_its_owner(self):
        mara = self.kit("Mara")

        theirs = self.call("POST", "", token=OTHER_KEY, json={"name": "Theirs"}).json()["data"]["kit"]

        self.assertEqual(self.call("GET", f"/{mara['id']}", token=OTHER_KEY).status_code, 404)
        self.assertEqual(self.call("GET", f"/{theirs['id']}", token=BOTH_KEY).status_code, 404)
        owned = [row["name"] for row in self.call("GET", "", token=BOTH_KEY).json()["data"]["kits"]]
        self.assertEqual(owned, ["Mara"])

    def test_resolve_is_a_read_even_though_it_is_a_post(self):
        self.assertEqual(self.call("POST", "/resolve", token=READ_KEY, json={"text": "x"}).status_code, 200)
        self.assertEqual(self.call("POST", "/resolve", token=CONTENT_KEY, json={"text": "x"}).status_code, 403)


class TestResolveRoute(RoutesBase):

    def setUp(self):
        super().setUp()
        self.mara = self.kit("Mara")
        self.face = self.add_image("face")
        self.voice = self.add_audio("voice")
        self.line = self.call("POST", f"/{self.mara['id']}/items", json={"text": "She whispers.", "name": "line"}).json()["data"]["kit"]["items"][-1]

    def add_image(self, name):
        return self.call(
            "POST", f"/{self.mara['id']}/items", json={"upload_id": self.upload(f"{name}.png").id, "name": name},
        ).json()["data"]["kit"]["items"][-1]

    def add_audio(self, name):
        return self.call(
            "POST", f"/{self.mara['id']}/items", json={"upload_id": self.upload(f"{name}.wav", "audio").id, "name": name},
        ).json()["data"]["kit"]["items"][-1]

    def resolve(self, **body):
        response = self.call("POST", "/resolve", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_a_path_resolves_to_what_would_be_sent(self):
        data = self.resolve(cites=["kits.mara.face"], preset_id="h3", mode="refs")

        self.assertEqual(data["text"], "<Picture 1>")
        self.assertEqual([item["name"] for item in data["items"]], ["face"])
        self.assertEqual(data["skipped"], [])
        self.assertEqual(data["field"], "references")

    def test_stable_ids_resolve_and_text_is_inlined(self):
        data = self.resolve(
            text="Scene:", cites=[f"{self.mara['id']}/{self.line['id']}", f"{self.mara['id']}/{self.face['id']}"],
            preset_id="h3", mode="refs",
        )

        self.assertEqual(data["text"], "Scene: She whispers. <Picture 1>")

    def test_existing_form_media_is_numbered_first_and_counts_toward_limits(self):
        own = [{"path": "uploads/o1.png", "relative_path": "uploads/o1.png", "type": "image"},
               {"path": "uploads/o2.png", "relative_path": "uploads/o2.png", "type": "image"}]

        data = self.resolve(
            cites=["kits.mara.face"], preset_id="h3", mode="refs",
            form_data={"references": own + [{"kit": self.mara["id"]}]},
        )

        self.assertEqual(data["text"], "<Picture 3>")

        self.template.prompt_resources = {"refs": [{**ALL_KINDS[0], "limit": 2}, *ALL_KINDS[1:]]}
        capped = self.resolve(cites=["kits.mara.face"], preset_id="h3", mode="refs", form_data={"references": own})
        self.assertEqual([s["code"] for s in capped["skipped"]], ["limit"])

    def test_a_bare_kit_expands_in_kit_order_with_media_before_text(self):
        data = self.resolve(cites=["kits.mara"], preset_id="h3", mode="refs")

        self.assertEqual(data["text"], "<Picture 1> <Audio 1> She whispers.")
        self.assertEqual(data["takes"], {"image": True, "video": True, "audio": True, "text": True})

    def test_unsupported_kinds_are_listed_as_skipped(self):
        self.template.prompt_resources = {"refs": [ALL_KINDS[0]]}

        data = self.resolve(cites=["kits.mara"], preset_id="h3", mode="refs")

        self.assertEqual(data["text"], "<Picture 1> She whispers.")
        self.assertEqual([(s["code"], s["item"]) for s in data["skipped"]], [("unsupported_kind", "voice")])
        self.assertEqual(data["takes"]["audio"], False)

    def test_loaded_decides_which_kits_may_be_cited(self):
        data = self.resolve(cites=["kits.mara.face"], loaded=[], preset_id="h3", mode="refs")

        self.assertEqual([s["code"] for s in data["skipped"]], ["kit_not_loaded"])
        self.assertEqual(data["text"], "")

    def test_without_a_preset_every_kind_is_accepted_and_nothing_is_limited(self):
        data = self.resolve(cites=["kits.mara.voice"])

        self.assertEqual([item["name"] for item in data["items"]], ["voice"])
        self.assertEqual(data["limits"], {})

    def test_an_unknown_preset_is_404_and_an_unknown_kit_is_reported_not_raised(self):
        self.assertEqual(self.call("POST", "/resolve", json={"preset_id": "nope"}).status_code, 404)

        data = self.resolve(cites=["kits.ghost.face"])

        self.assertEqual([s["code"] for s in data["skipped"]], ["kit_deleted"])
