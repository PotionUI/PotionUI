import sqlite3
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

import src.features.sessions.repository as session_repository_module
import src.features.sessions.version_insight as version_insight_module
import src.features.sessions.version_repository as version_repository_module
from src.features.sessions.dto import Session as SessionDTO
from src.features.sessions.repository import SessionRepository
from src.features.sessions.routes import SessionController
from src.features.sessions.version_insight import changed_fields_between, changes_between, prompt_preview, trim_preview
from src.features.sessions.version_repository import SessionVersionRepository

from tests.features.sessions.test_session_versions import _SCHEMA


def slot(prompt="a cat", **extra):
    return {"selectedMode": "txt2img", "prompt": prompt, "negativePrompt": "", "formData": {}, **extra}


def wrap(s, mode="txt2img"):
    return {mode: s}


class TestPromptPreview:
    def test_plain_prompt(self):
        assert prompt_preview(wrap(slot("a red fox"))) == "a red fox"

    def test_segments_win_and_skip_disabled(self):
        segments = [
            {"content": "one", "enabled": True},
            {"content": "hidden", "enabled": False},
            {"content": "two", "prefix": "(", "suffix": ")"},
            {"content": "   "},
        ]
        assert prompt_preview(wrap(slot("stale", promptSegments=segments))) == "one (two)"

    def test_segment_chips_resolved(self):
        segments = [{"content": "a #hair cat", "chips": {"c": {"categoryPath": "hair", "value": "red"}}}]
        assert prompt_preview(wrap(slot("", promptSegments=segments))) == "a red cat"

    def test_trims_on_word_boundary_with_ellipsis(self):
        text = " ".join(["abcdef"] * 20)
        out = trim_preview(text)
        assert out.endswith("…")
        assert len(out) <= 81
        assert not out[:-1].endswith("abc")

    def test_short_text_untouched(self):
        assert trim_preview("short") == "short"

    def test_picks_changed_mode(self):
        prev = {"txt2img": slot("old"), "img2img": slot("other", selectedMode="img2img")}
        cur = {"txt2img": slot("old"), "img2img": slot("edited", selectedMode="img2img")}
        assert prompt_preview(cur, prev) == "edited"

    def test_flat_data_and_empty(self):
        assert prompt_preview({"prompt": "flat"}) == "flat"
        assert prompt_preview({}) == ""


class TestChanges:
    @pytest.mark.parametrize(
        "mutate,label",
        [
            (lambda s: s.update(prompt="different"), "prompt"),
            (lambda s: s.update(negativePrompt="ugly"), "negative prompt"),
            (lambda s: s.update(selectedMode="img2img"), "mode"),
        ],
    )
    def test_single_label(self, mutate, label):
        prev = slot()
        cur = slot()
        mutate(cur)
        assert changes_between(wrap(cur), wrap(prev)) == [label]

    def test_form_only_changes_carry_no_label(self):
        prev = slot()
        cur = slot(formData={"steps": 30, "diffusion_model": "m2"})
        assert changes_between(wrap(cur), wrap(prev)) == []

    def test_mode_label_when_mode_slot_added(self):
        prev = wrap(slot())
        cur = {**prev, "img2img": slot(selectedMode="img2img")}
        assert changes_between(cur, prev) == ["mode"]

    def test_ordered_and_identical_empty(self):
        prev = wrap(slot())
        cur = wrap(slot("new", negativePrompt="x", selectedMode="img2img"))
        assert changes_between(cur, prev) == ["prompt", "negative prompt", "mode"]
        assert changes_between(prev, prev) == []


class TestChangedFields:
    def test_lists_differing_form_keys_sorted(self):
        prev = wrap(slot(formData={"zeta": 1, "alpha": 1, "same": 3}))
        cur = wrap(slot(formData={"zeta": 2, "alpha": 5, "same": 3, "added": True}))
        assert changed_fields_between(cur, prev) == ["added", "alpha", "zeta"]

    def test_removed_key_counts_and_equal_values_excluded(self):
        prev = wrap(slot(formData={"gone": 1, "kept": [1, 2]}))
        cur = wrap(slot(formData={"kept": [1, 2]}))
        assert changed_fields_between(cur, prev) == ["gone"]

    def test_identical_and_flat_data(self):
        prev = wrap(slot(formData={"a": 1}))
        assert changed_fields_between(prev, prev) == []
        assert changed_fields_between({"prompt": "p", "formData": {"a": 2}}, {"prompt": "p", "formData": {"a": 1}}) == ["a"]

    def test_source_names_no_preset_fields(self):
        source = Path(version_insight_module.__file__).read_text(encoding="utf-8")
        for literal in ("resolution", "width", "height", "loras", "steps", "seed", "model", "size"):
            assert f'"{literal}"' not in source
            assert f"'{literal}'" not in source


@pytest.fixture
def env():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    connection.execute("INSERT INTO users (id) VALUES ('user-1')")
    connection.commit()

    @contextmanager
    def _get_conn():
        yield connection

    with patch.object(session_repository_module, "get_database_connection", _get_conn), \
         patch.object(version_repository_module, "get_database_connection", _get_conn):
        session_repo, version_repo = SessionRepository(), SessionVersionRepository()
        session = session_repo.create(SessionDTO(id="s1", user_id="user-1", preset_id="p", name="n", data={}))
        for i in range(1, 6):
            version_repo.create_if_changed(session.id, wrap(slot(f"prompt {i}")), "P")
        controller = SessionController(
            session_repository=session_repo,
            plugin_registry=Mock(),
            session_version_repository=version_repo,
        )
        yield controller, version_repo, connection
    connection.close()


class TestListEndpoint:
    @pytest.mark.asyncio
    async def test_shape_newest_first_and_first_version_has_no_changes(self, env):
        controller, _, _ = env
        data = (await controller.list_session_versions("user-1", "s1")).data
        assert [v["version_number"] for v in data] == [5, 4, 3, 2, 1]
        assert data[0]["prompt_preview"] == "prompt 5"
        assert data[0]["changes"] == ["prompt"]
        assert data[0]["changed_fields"] == []
        assert data[-1]["changes"] == []
        assert data[-1]["changed_fields"] == []
        assert data[0]["summary"] == "P"

    @pytest.mark.asyncio
    async def test_paging_uses_predecessor_for_diff(self, env):
        controller, _, _ = env
        page = (await controller.list_session_versions("user-1", "s1", limit=2)).data
        assert [v["version_number"] for v in page] == [5, 4]
        assert page[1]["changes"] == ["prompt"]
        nxt = (await controller.list_session_versions("user-1", "s1", limit=2, before=4)).data
        assert [v["version_number"] for v in nxt] == [3, 2]
        last = (await controller.list_session_versions("user-1", "s1", limit=5, before=2)).data
        assert [v["version_number"] for v in last] == [1]
        assert last[0]["changes"] == []

    @pytest.mark.asyncio
    async def test_single_payload_query(self, env):
        controller, _, connection = env
        statements = []
        connection.set_trace_callback(statements.append)
        await controller.list_session_versions("user-1", "s1")
        connection.set_trace_callback(None)
        version_queries = [s for s in statements if "session_versions" in s]
        assert len(version_queries) == 1
        assert "payload" in version_queries[0]

    @pytest.mark.asyncio
    async def test_page_fetches_exactly_one_extra_row(self, env):
        controller, _, connection = env
        statements = []
        connection.set_trace_callback(statements.append)
        await controller.list_session_versions("user-1", "s1", limit=2)
        connection.set_trace_callback(None)
        assert any("LIMIT 3" in s for s in statements)
