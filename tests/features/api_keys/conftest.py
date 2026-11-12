from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys import routes as api_key_routes
from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.api_keys.operations.resolver import ApiKeyResolver
from src.features.api_keys.repository import ApiKeyEventRepository, ApiKeyRepository, UserApiAccessRepository
from src.features.mcp import routes as mcp_routes
from src.features.users.repository import UserRepository
from src.platform.database import get_cursor
from src.platform.security import current_user
from src.platform.security.current_user import get_current_user
from src.platform.security.user import User
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.conftest import TestDatabase
from tests.features.api_keys.support import World


@pytest.fixture
def keys_db():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        yield test_database
    test_database.close()


class Stack:
    def __init__(self):
        self.settings = Settings(SettingRepository())
        self.users = UserRepository()
        self.keys = ApiKeyRepository()
        self.events = ApiKeyEventRepository()
        self.access = UserApiAccessRepository()
        self.collaborators = ApiKeyCollaborators(
            keys=self.keys, events=self.events, access=self.access, settings=self.settings
        )
        self.resolver = ApiKeyResolver(self.keys, self.access, self.users, self.settings)

    def user(self, name="alice", admin=False) -> User:
        created = self.users.create(username=name, email=f"{name}@example.com", password_hash="x")
        if admin:
            self.promote(created.id)
            created = self.users.get_by_id(created.id)
        return created

    def promote(self, user_id: str) -> None:
        with get_cursor() as cursor:
            cursor.execute("UPDATE users SET account_type = 'ADMIN' WHERE id = ?", (user_id,))


@pytest.fixture
def stack(keys_db):
    return Stack()


@pytest.fixture
def world(stack, monkeypatch):
    world = World(stack)
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: world.sessions.get(token)
    auth.authenticate_websocket.side_effect = lambda token: (
        (world.sessions[token], None) if token in world.sessions else (None, "Invalid token")
    )
    auth.users = stack.users
    previous_auth, previous_resolver = current_user._auth, current_user._key_resolver
    current_user.set_auth(auth)
    current_user.set_key_resolver(stack.resolver)
    current_user._key_failures.reset("testclient")
    world.tool_counts_calls = []
    world.tool_counts = lambda pairs: (world.tool_counts_calls.append(list(pairs)), {key_id: (7, 12) for key_id, _ in pairs})[1]
    world.handle_method = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mcp_routes, "handle_method", world.handle_method)
    yield world
    current_user._auth = previous_auth
    current_user.set_key_resolver(previous_resolver)
    current_user._key_failures.reset("testclient")


@pytest.fixture
def client(world):
    container = SimpleNamespace(
        api_key_collaborators=world.stack.collaborators,
        user_repository=world.stack.users,
        mcp_tool_collaborators=Mock(),
        mcp_tool_counts=world.tool_counts,
    )
    app = FastAPI()
    app.include_router(api_key_routes.build_router(container))
    app.include_router(api_key_routes.build_admin_router(container))
    app.include_router(mcp_routes.build_router(container))

    undeclared = APIRouter(prefix="/api/undeclared")

    @undeclared.get("/me")
    async def undeclared_me(user=Depends(get_current_user)):
        return {"id": user.id}

    app.include_router(undeclared)
    return TestClient(app, raise_server_exceptions=False)
