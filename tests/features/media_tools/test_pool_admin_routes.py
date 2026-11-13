import threading
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.media_tools.jobs import RunOutcome
from src.features.media_tools.routes import build_admin_router
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.media_tools.support import MediaToolsTestBase
from tests.platform.security.test_current_user_keys import FakeResolver

ADMIN_SESSION = "admin.session.jwt"
USER_SESSION = "user.session.jwt"
ADMIN_READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
ADMIN_KEY = "pui_" + "a" * 40 + "ZZZZZZ"
USER_ADMIN_KEY = "pui_" + "u" * 40 + "ZZZZZZ"
WAIT = 10.0


def person(user_id, kind):
    return User(id=user_id, username=f"name-{user_id}", email=f"{user_id}@example.com", password_hash="h", account_type=kind)


class TestPoolAdminRoutes(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        self.admin = person("admin-1", AccountType.ADMIN)
        self.member = person("member-1", AccountType.USER)
        self.people = {self.user_id: person(self.user_id, AccountType.USER)}
        container = SimpleNamespace(
            media_tool_service=self.service,
            user_repository=SimpleNamespace(get_by_id=lambda user_id: self.people.get(user_id)),
        )
        app = FastAPI()
        app.include_router(build_admin_router(container))
        self.admin_client = TestClient(app)
        self.addCleanup(self.admin_client.close)
        auth = Mock()
        auth.get_user_from_token.side_effect = lambda token: {
            ADMIN_SESSION: self.admin, USER_SESSION: self.member,
        }.get(token)
        resolver = FakeResolver()
        resolver.add(ADMIN_READ_KEY, self.admin, [S.READ, S.GENERATE])
        resolver.add(ADMIN_KEY, self.admin, [S.ADMIN])
        resolver.add(USER_ADMIN_KEY, self.member, [S.ADMIN])
        previous = (current_user._auth, current_user._key_resolver)
        current_user.set_auth(auth)
        current_user.set_key_resolver(resolver)
        current_user._key_failures.reset("testclient")
        self.addCleanup(lambda: (setattr(current_user, "_auth", previous[0]), setattr(current_user, "_key_resolver", previous[1])))
        self.release = threading.Event()
        self.started = threading.Event()
        self.addCleanup(self.release.set)

    def call(self, method, path, token=ADMIN_SESSION):
        return self.admin_client.request(
            method, f"/api/admin/media-tools{path}", headers={"Authorization": f"Bearer {token}"}
        )

    def submit(self, user_id):
        def work(control):
            self.started.set()
            while not self.release.wait(0.005):
                if control.is_cancelled():
                    break
            return RunOutcome()

        return self.jobs.submit(user_id, work, tools=["rot", "shrink"])

    def test_the_pool_has_the_documented_shape(self):
        self.submit(self.user_id)
        self.assertTrue(self.started.wait(WAIT))

        response = self.call("GET", "/pool")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(
            set(data),
            {"limits", "running", "queued", "previews", "jobs", "metrics", "cpu_cores", "setting_keys"},
        )
        self.assertEqual(
            set(data["limits"]),
            {"max_concurrent", "max_previews", "max_running_per_user", "max_queued_per_user", "ffmpeg_threads"},
        )
        self.assertEqual(set(data["previews"]), {"limit", "in_use", "clips_running"})
        self.assertEqual(data["running"], 1)
        job = data["jobs"][0]
        self.assertEqual((job["user_id"], job["username"], job["tools"]), (self.user_id, f"name-{self.user_id}", ["rot", "shrink"]))
        self.assertEqual((job["kind"], job["status"]), ("run", "running"))
        self.assertEqual(data["setting_keys"]["max_concurrent"], "media_tools_max_concurrent")
        self.assertIn("utilisation_60m_percent", data["metrics"])

    def test_a_regular_user_and_a_non_admin_key_are_refused(self):
        self.assertEqual(self.call("GET", "/pool", USER_SESSION).status_code, 403)
        self.assertEqual(self.call("GET", "/pool", ADMIN_READ_KEY).status_code, 403)
        self.assertEqual(self.call("POST", "/jobs/x/cancel", USER_SESSION).status_code, 403)
        self.assertEqual(self.call("POST", "/jobs/x/cancel", ADMIN_READ_KEY).status_code, 403)

    def test_an_admin_scoped_key_of_a_regular_user_is_refused(self):
        self.assertEqual(self.call("GET", "/pool", USER_ADMIN_KEY).status_code, 403)

    def test_an_admin_scoped_key_of_an_admin_is_served(self):
        self.assertEqual(self.call("GET", "/pool", ADMIN_KEY).status_code, 200)

    def test_an_admin_cancels_another_users_running_job(self):
        run_id = self.submit(self.user_id)
        self.assertTrue(self.started.wait(WAIT))

        response = self.call("POST", f"/jobs/{run_id}/cancel")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["run_id"], run_id)
        self.assertEqual(self.jobs.counts()["running"], 0)

    def test_cancelling_an_unknown_job_is_404(self):
        self.assertEqual(self.call("POST", "/jobs/nope/cancel").status_code, 404)

    def test_the_users_own_run_list_does_not_show_other_users_jobs(self):
        self.submit(self.other_user_id)
        self.assertTrue(self.started.wait(WAIT))

        response = self.client.get("/api/media-tools/runs")

        self.assertEqual(response.json()["data"]["runs"], [])
