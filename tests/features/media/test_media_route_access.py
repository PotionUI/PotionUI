from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from src.features.content_safety.policy import EffectivePolicy
from src.features.generation.file_repository import FileRepository
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import build_router as build_generation_router
from src.features.media.access import MediaAccess
from src.features.media.dto import MediaResult
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.routes import MediaController, build_router as build_media_router
from src.features.media.store import MediaStore
from src.features.media.upload_repository import UploadRepository
from src.features.models.repository import ModelRepository
from src.features.users.routes import build_router as build_users_router
from src.platform.filesystem.file_store import FileStore
from src.platform.security import current_user
from src.platform.security.media_session import media_cookie_name
from src.platform.security.user import AccountType, User
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

PIXELS = b"\x89PNG-owner-pixels"


class _NoopHookContext:
    data: dict = {}


class _NoopPluginRegistry:
    def execute_hook(self, hook, initial_data=None):
        context = _NoopHookContext()
        context.data = dict(initial_data or {})
        return context, []


class _Policies:
    def __init__(self, restricted_ids):
        self.restricted_ids = restricted_ids

    def resolve(self, user_id):
        if user_id in self.restricted_ids:
            return EffectivePolicy("blocked", restricted=True)
        return EffectivePolicy("allowed")


class _Tokens:
    def __init__(self, users):
        self.users = users

    def get_user_from_token(self, token):
        return self.users.get(token)


def _user(name, account_type=AccountType.USER):
    return User(
        id=generate_ulid(), username=name, email=f"{name}@example.com",
        password_hash="h", account_type=account_type,
    )


class World:
    def __init__(self, db, storage: Path):
        self.db = db
        self.storage = storage
        self.owner = _user("owner")
        self.other = _user("other")
        self.admin = _user("admin", AccountType.ADMIN)
        self.kid = _user("kid")
        with db.get_cursor() as cursor:
            for user in (self.owner, self.other, self.admin, self.kid):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, ?, ?)",
                    (user.id, user.username, user.email, "h", user.account_type.value),
                )

    def generation(self, user, status="completed", content=PIXELS):
        generation_id = generate_ulid()
        key = f"generations/2026-10-05/{generation_id}/0.png"
        path = self.storage / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        file_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, user_id, status) VALUES (?, ?, ?, ?, ?)",
                (generation_id, "preset-1", "{}", user.id, status),
            )
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, is_final, user_id) VALUES (?, ?, 'IMAGE', 1, ?)",
                (file_id, key, user.id),
            )
            cursor.execute(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                (generate_ulid(), generation_id, file_id),
            )
        return SimpleNamespace(id=generation_id, file_id=file_id, key=key, filename="0.png")

    def upload(self, user, content=PIXELS):
        filename = f"{generate_ulid()}.png"
        path = self.storage / "uploads" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) VALUES (?, ?, ?, 'image')",
                (generate_ulid(), user.id, filename),
            )
        return filename

    def temp(self, generation_id, content=PIXELS):
        filename = f"tmp_video_{generation_id}_{generate_ulid()}.mp4"
        path = self.storage / "tmp" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return filename

    def model_preview(self, assigned_to):
        model_id = generate_ulid()
        file_id = generate_ulid()
        key = f"models/previews/{file_id}.png"
        path = self.storage / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PIXELS)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO models (id, filename, model_type) VALUES (?, ?, 'checkpoint')",
                (model_id, f"{model_id}.safetensors"),
            )
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type) VALUES (?, ?, 'IMAGE')", (file_id, key)
            )
            cursor.execute(
                "INSERT INTO model_files (id, model_id, file_id) VALUES (?, ?, ?)",
                (generate_ulid(), model_id, file_id),
            )
            cursor.execute(
                "INSERT INTO user_models (id, user_id, model_id) VALUES (?, ?, ?)",
                (generate_ulid(), assigned_to.id, model_id),
            )
        return file_id

    def rate(self, key, state):
        score = 0.99 if state == "flagged" else 0.01
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO content_ratings (key, nsfw_score, state, rater, source, rated_at) "
                "VALUES (?, ?, ?, 'test', 'gate', '2026-10-05T00:00:00Z')",
                (key, score, state),
            )


@pytest.fixture
def world(mock_db, test_storage):
    return World(mock_db, Path(test_storage))


@pytest.fixture
def client(world, test_storage):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(test_storage)
    settings.get_setting.side_effect = lambda key, default=None, **_: default

    media_store = MediaStore(
        file_resolver=FilePathResolver(settings),
        image_processor=ImageProcessor(),
        media_type_resolver=MediaTypeResolver(),
        file_repository=FileRepository(),
        generation_repository=GenerationRepository(),
        settings=settings,
        file_service=FileStore(str(test_storage)),
        plugin_registry=_NoopPluginRegistry(),
        upload_repository=UploadRepository(),
    )
    content_safety = ContentSafetyManager(
        settings=settings,
        resolver=_Policies({world.kid.id}),
        ledger=ContentLedger(settings),
        gate=Mock(),
    )
    access = MediaAccess(
        generation_repository=GenerationRepository(),
        file_repository=FileRepository(),
        upload_repository=UploadRepository(),
        model_repository=ModelRepository(),
        content_safety=content_safety,
    )
    run_report_controller = Mock()
    run_report_controller.get_run_report_artifact = AsyncMock(
        side_effect=lambda generation_id, name: Response(content=b"artifact", media_type="image/png")
    )
    user_controller = Mock()
    user_controller.get_avatar = AsyncMock(
        side_effect=lambda filename: Response(content=b"avatar", media_type="image/png")
    )
    container = SimpleNamespace(
        media_controller=MediaController(media_store, access),
        media_access=access,
        _generation_controller=run_report_controller,
        user_controller=user_controller,
    )
    app = FastAPI()
    app.include_router(build_media_router(container))
    app.include_router(build_generation_router(container))
    app.include_router(build_users_router(container))

    tokens = {
        "tok-owner": world.owner,
        "tok-other": world.other,
        "tok-admin": world.admin,
        "tok-kid": world.kid,
    }
    previous_auth = current_user._auth
    previous_fallback = current_user._media_bearer_fallback
    current_user.set_auth(_Tokens(tokens))
    current_user.set_media_bearer_fallback(None)
    with TestClient(app) as test_client:
        yield test_client
    current_user._auth = previous_auth
    current_user._media_bearer_fallback = previous_fallback


def _get(client, url, token=None, via="cookie"):
    client.cookies.clear()
    headers = {}
    if token and via == "cookie":
        client.cookies.set(media_cookie_name(), token)
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    return client.get(url, headers=headers)


def _owner_routes(world):
    generation = world.generation(world.owner)
    return generation, {
        "generation": f"/api/media/generations/{generation.id}/{generation.filename}",
        "upload": f"/api/media/uploads/{world.upload(world.owner)}",
        "temp": f"/api/media/tmp/{world.temp(generation.id)}",
        "file": f"/api/media/files/{generation.file_id}",
        "model_preview": f"/api/media/files/{world.model_preview(world.owner)}",
        "run_report": f"/api/generations/{generation.id}/run-report/artifacts/compare.png",
    }


class TestSignedOut:
    def test_every_private_media_route_refuses_a_signed_out_request(self, client, world):
        _, routes = _owner_routes(world)
        routes["avatar"] = "/api/users/avatars/someone.png"
        for name, url in routes.items():
            response = _get(client, url)
            assert response.status_code == 401, name

    def test_a_forged_cookie_is_not_a_session(self, client, world):
        _, routes = _owner_routes(world)
        for name, url in routes.items():
            assert _get(client, url, "tok-forged").status_code == 401, name


class TestOwnership:
    @pytest.mark.parametrize("via", ["cookie", "header"])
    def test_the_owner_gets_the_bytes(self, client, world, via):
        _, routes = _owner_routes(world)
        for name, url in routes.items():
            response = _get(client, url, "tok-owner", via=via)
            assert response.status_code == 200, name
            assert response.content in (PIXELS, b"artifact"), name

    def test_another_user_gets_a_404_identical_to_a_missing_file(self, client, world):
        _, routes = _owner_routes(world)
        missing = {
            "generation": f"/api/media/generations/{generate_ulid()}/0.png",
            "upload": f"/api/media/uploads/{generate_ulid()}.png",
            "temp": f"/api/media/tmp/tmp_video_{generate_ulid()}_{generate_ulid()}.mp4",
            "file": f"/api/media/files/{generate_ulid()}",
            "run_report": f"/api/generations/{generate_ulid()}/run-report/artifacts/compare.png",
        }
        for name, url in routes.items():
            response = _get(client, url, "tok-other")
            assert response.status_code == 404, name
            assert PIXELS not in response.content, name
        for name, url in missing.items():
            assert _get(client, url, "tok-other").status_code == 404, name

    def test_an_admin_can_view_any_users_media(self, client, world):
        _, routes = _owner_routes(world)
        for name, url in routes.items():
            assert _get(client, url, "tok-admin").status_code == 200, name

    def test_a_temp_file_without_a_generation_owner_is_admin_only(self, client, world):
        filename = f"tmp_video_{generate_ulid()}.mp4"
        (world.storage / "tmp").mkdir(parents=True, exist_ok=True)
        (world.storage / "tmp" / filename).write_bytes(PIXELS)
        url = f"/api/media/tmp/{filename}"

        assert _get(client, url, "tok-owner").status_code == 404
        assert _get(client, url, "tok-admin").status_code == 200

    def test_any_signed_in_user_sees_avatars(self, client, world):
        assert _get(client, "/api/users/avatars/someone.png", "tok-other").status_code == 200

    def test_private_media_is_never_marked_publicly_cacheable(self, client, world):
        generation, routes = _owner_routes(world)
        for name in ("generation", "file"):
            response = _get(client, routes[name], "tok-owner")
            assert "public" not in response.headers.get("cache-control", ""), name
            assert "private" in response.headers.get("cache-control", ""), name


class TestPathTraversal:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/media/uploads/..%2F..%2Fsecret.png",
            "/api/media/tmp/..%2F..%2Fsecret.png",
            "/api/media/generations/x/..%2F..%2Fsecret.png",
        ],
    )
    def test_traversal_is_refused_even_for_an_admin(self, client, world, url):
        (world.storage.parent / "secret.png").write_bytes(b"secret")
        response = _get(client, url, "tok-admin")
        assert response.status_code == 404
        assert b"secret" not in response.content


class TestRestrictedViewer:
    def test_a_restricted_user_is_refused_their_own_flagged_generation(self, client, world):
        flagged = world.generation(world.kid)
        world.rate(flagged.key, "flagged")
        urls = [
            f"/api/media/generations/{flagged.id}/{flagged.filename}",
            f"/api/media/files/{flagged.file_id}",
            f"/api/media/tmp/{world.temp(flagged.id)}",
            f"/api/generations/{flagged.id}/run-report/artifacts/compare.png",
        ]
        for url in urls:
            assert _get(client, url, "tok-kid").status_code == 404, url

    def test_a_restricted_user_is_refused_an_unrated_finished_generation_like_history(self, client, world):
        unrated = world.generation(world.kid)
        url = f"/api/media/generations/{unrated.id}/{unrated.filename}"
        assert _get(client, url, "tok-kid").status_code == 404

    def test_a_restricted_user_sees_their_own_safe_generation(self, client, world):
        safe = world.generation(world.kid)
        world.rate(safe.key, "safe")
        urls = [
            f"/api/media/generations/{safe.id}/{safe.filename}",
            f"/api/media/files/{safe.file_id}",
            f"/api/generations/{safe.id}/run-report/artifacts/compare.png",
        ]
        for url in urls:
            assert _get(client, url, "tok-kid").status_code == 200, url

    def test_a_running_generation_streams_previews_to_a_restricted_owner(self, client, world):
        running = world.generation(world.kid, status="running")
        url = f"/api/media/tmp/{world.temp(running.id)}"
        assert _get(client, url, "tok-kid").status_code == 200

    def test_a_restricted_user_is_refused_a_flagged_upload_but_keeps_their_others(self, client, world):
        flagged = world.upload(world.kid)
        world.rate(f"uploads/{flagged}", "flagged")
        clean = world.upload(world.kid)

        assert _get(client, f"/api/media/uploads/{flagged}", "tok-kid").status_code == 404
        assert _get(client, f"/api/media/uploads/{clean}", "tok-kid").status_code == 200

    def test_a_restricted_user_never_sees_model_preview_media(self, client, world):
        file_id = world.model_preview(world.kid)
        assert _get(client, f"/api/media/files/{file_id}", "tok-kid").status_code == 404

    def test_a_flagged_file_still_reaches_an_unrestricted_owner(self, client, world):
        flagged = world.generation(world.owner)
        world.rate(flagged.key, "flagged")
        url = f"/api/media/generations/{flagged.id}/{flagged.filename}"
        assert _get(client, url, "tok-owner").status_code == 200


class TestPublicAssets:
    def test_preset_covers_and_examples_load_signed_out(self):
        store = Mock(spec=MediaStore)
        store.get_preset_file.return_value = MediaResult(
            content=b"cover", media_type="image/png", headers={"ETag": "\"cover\""}, use_streaming=False
        )
        container = SimpleNamespace(media_controller=MediaController(store, Mock()))
        app = FastAPI()
        app.include_router(build_media_router(container))
        with TestClient(app) as test_client:
            for path in ("public/cover.png", "examples/a/b.png"):
                response = test_client.get(f"/api/media/presets/marketplace-sdxl/{path}")
                assert response.status_code == 200, path
                assert response.content == b"cover"

    def test_plugin_assets_need_no_media_session(self):
        from src.features.plugins.routes import build_router as build_plugins_router

        container = Mock()
        router = build_plugins_router(container)
        route = next(r for r in router.routes if r.path == "/api/plugins/{plugin_id}/assets/{file_path:path}")
        stack = [route.dependant]
        calls = []
        while stack:
            dependant = stack.pop()
            calls.append(dependant.call)
            stack.extend(dependant.dependencies)
        assert current_user.require_media_viewer not in calls
        assert current_user.get_current_user not in calls


class TestMcpBearer:
    def test_an_mcp_token_reaches_its_owners_media(self, client, world):
        generation = world.generation(world.owner)
        url = f"/api/media/generations/{generation.id}/{generation.filename}"
        current_user.set_media_bearer_fallback(
            lambda token: world.owner if token == "pui_mcp_owner" else None
        )

        assert _get(client, url, "pui_mcp_owner", via="header").status_code == 200
        assert _get(client, url, "pui_mcp_unknown", via="header").status_code == 401
