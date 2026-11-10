from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.plugins.operations import page_stylesheet
from src.features.plugins.records import PluginPage
from src.features.plugins.routes import PluginController
from src.platform.plugins.loader import PluginManifest


def _manifest(plugin_dir: Path) -> PluginManifest:
    return PluginManifest(
        id="styled",
        name="Styled",
        version="1.0.0",
        description="",
        author="",
        plugin_type="frontend-only",
        plugin_dir=plugin_dir,
    )


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "styled" / "frontend" / "dist"
    dist.mkdir(parents=True)
    return dist


def _registry(manifest) -> Mock:
    registry = Mock()
    registry.get_plugin.side_effect = lambda plugin_id: manifest if plugin_id == "styled" else None
    return registry


def test_a_page_whose_dist_holds_a_stylesheet_of_its_name_gets_it(tmp_path):
    dist = _dist(tmp_path)
    (dist / "StyledPage.js").write_text("export {}")
    (dist / "StyledPage.css").write_text(".a{}")

    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "StyledPage.js") == "StyledPage.css"


def test_a_page_without_a_stylesheet_gets_none(tmp_path):
    dist = _dist(tmp_path)
    (dist / "StyledPage.js").write_text("export {}")
    (dist / "Other.css").write_text(".a{}")

    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "StyledPage.js") is None


def test_a_page_in_a_subfolder_gets_the_stylesheet_next_to_it(tmp_path):
    dist = _dist(tmp_path)
    (dist / "pages").mkdir()
    (dist / "pages" / "Editor.css").write_text(".a{}")

    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "pages/Editor.js") == "pages/Editor.css"


def test_a_component_path_that_leaves_the_dist_folder_gets_none(tmp_path):
    _dist(tmp_path)
    (tmp_path / "styled" / "frontend" / "Outside.css").write_text(".a{}")

    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "../Outside.js") is None


def test_a_folder_named_like_the_stylesheet_is_not_a_stylesheet(tmp_path):
    dist = _dist(tmp_path)
    (dist / "StyledPage.css").mkdir()

    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "StyledPage.js") is None


def test_a_plugin_the_registry_does_not_know_gets_none(tmp_path):
    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "gone", "StyledPage.js") is None


def test_an_empty_component_path_gets_none(tmp_path):
    _dist(tmp_path)
    registry = _registry(_manifest(tmp_path / "styled"))

    assert page_stylesheet(registry, "styled", "") is None


@pytest.fixture
def controller(tmp_path):
    dist = _dist(tmp_path)
    (dist / "StyledPage.css").write_text(".a{}")
    repository = Mock()
    repository.get_all_active_pages.return_value = [
        PluginPage(id=1, plugin_id="styled", route="styled", component_path="StyledPage.js", label="Styled"),
        PluginPage(id=2, plugin_id="styled", route="plain", component_path="PlainPage.js", label="Plain"),
    ]
    return PluginController(plugin_repository=repository, plugin_registry=_registry(_manifest(tmp_path / "styled")))


async def test_the_page_route_answers_with_the_stylesheet_of_the_page(controller):
    response = await controller.get_plugin_page_by_route("styled")

    assert response.data["stylesheet"] == "StyledPage.css"


async def test_the_page_list_gives_each_page_its_own_stylesheet_or_none(controller):
    response = await controller.get_plugin_pages()

    assert {page["route"]: page["stylesheet"] for page in response.data} == {"styled": "StyledPage.css", "plain": None}
