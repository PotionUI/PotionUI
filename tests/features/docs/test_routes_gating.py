"""Router-level authorization tests for the docs API.

`/api/docs/tree` and `/api/docs/content` are available to any authenticated
user (role-filtered by `src.features.docs.operations`); only the `/live/*`
reference routes are admin-only. These drive the real FastAPI router (auth
dependency overridden to a regular user / an admin) against a real
`DocsController` over a tmp docs tree, so a bug in the route's `is_admin`
wiring - not just the operations' filtering - would be caught.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.docs.routes import DocsController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def docs_root(tmp_path):
    """A user doc (readable by anyone) and a top-level dev doc (admin-only)."""
    _write(tmp_path / "docs" / "user" / "getting-started.md", "# Getting Started\n\nHello.")
    _write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n\nInternals.")
    return tmp_path / "docs"


def _make_client(user, docs_root):
    plugin_registry = Mock()
    plugin_registry.get_enabled_plugins.return_value = []
    pipes_documenter = Mock()
    pipes_documenter.generate_documentation.return_value = {"pipes": [], "total": 0}
    controller = DocsController(plugin_registry, str(docs_root), pipes_documenter)

    container = SimpleNamespace(docs_controller=controller)
    app = FastAPI()
    app.include_router(build_router(container))

    async def _fake_active_user():
        return user

    app.dependency_overrides[get_current_active_user] = _fake_active_user
    return TestClient(app)


def test_regular_user_lists_and_reads_a_user_doc(docs_root):
    client = _make_client(_user(AccountType.USER), docs_root)

    tree = client.get("/api/docs/tree")
    assert tree.status_code == 200
    sections = {s["id"]: s for s in tree.json()["data"]["sections"]}
    assert "user" in sections
    assert any(item["id"] == "user/getting-started" for item in sections["user"]["items"])
    # The developer section (holding ARCHITECTURE.md) is omitted entirely.
    assert "developer" not in sections

    content = client.get("/api/docs/content", params={"id": "user/getting-started"})
    assert content.status_code == 200
    assert "Hello." in content.json()["data"]["markdown"]


def test_regular_user_cannot_read_an_admin_only_doc(docs_root):
    client = _make_client(_user(AccountType.USER), docs_root)

    response = client.get("/api/docs/content", params={"id": "dev/ARCHITECTURE"})
    assert response.status_code == 403


def test_admin_reads_the_same_admin_only_doc(docs_root):
    client = _make_client(_user(AccountType.ADMIN), docs_root)

    response = client.get("/api/docs/content", params={"id": "dev/ARCHITECTURE"})
    assert response.status_code == 200
    assert "Internals." in response.json()["data"]["markdown"]


def test_regular_user_denied_live_pipes_reference(docs_root):
    client = _make_client(_user(AccountType.USER), docs_root)

    response = client.get("/api/docs/live/pipes")
    assert response.status_code == 403


def test_admin_allowed_live_pipes_reference(docs_root):
    client = _make_client(_user(AccountType.ADMIN), docs_root)

    response = client.get("/api/docs/live/pipes")
    assert response.status_code == 200
