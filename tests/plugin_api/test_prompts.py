"""Tests for the `src.plugin_api.prompts` prompt-importer surface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.plugin_api.prompts import (
    PromptImportOutcome,
    create_prompt_for_user,
    find_prompt_source_ids,
    import_prompts_for_user,
)


def _fake_prompt(**fields):
    return SimpleNamespace(to_dict=lambda: dict(fields))


@pytest.mark.asyncio
async def test_create_prompt_for_user_delegates_through_add_prompt_and_sets_source_provider():
    collaborators = SimpleNamespace()
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: {"id": "prompt-1", "source_provider": "fixture-plugin"})
    ))

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await create_prompt_for_user(
            "user-1", "a red fox", source_provider="fixture-plugin", model_name="SDXL",
        )

    assert result == {"id": "prompt-1", "source_provider": "fixture-plugin"}
    mock_operations.add_prompt.assert_awaited_once_with(
        collaborators, "user-1", "a red fox",
        model_id=None, name=None, usage_hint=None, source_provider="fixture-plugin",
        source_id=None, source_url=None, source_group_id=None,
        model_name="SDXL", base_model=None, cfg_scale=None, steps=None, sampler=None,
        width=None, height=None, nsfw=False, tags=[], metadata={},
    )


@pytest.mark.asyncio
async def test_create_prompt_for_user_forwards_model_id_to_add_prompt():
    collaborators = SimpleNamespace()
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: {"id": "prompt-1", "model_id": "model-9"})
    ))

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await create_prompt_for_user(
            "user-1", "a red fox", source_provider="fixture-plugin", model_id="model-9",
        )

    assert result["model_id"] == "model-9"
    assert mock_operations.add_prompt.await_args.kwargs["model_id"] == "model-9"


def test_find_prompt_source_ids_delegates_to_repository():
    repository = Mock(get_source_ids=Mock(return_value={"123", "456"}))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)

    with patch("src.plugin_api.prompts.get_container", return_value=container):
        result = find_prompt_source_ids("user-1", "civitai-provider", model_id="model-9")

    assert result == {"123", "456"}
    repository.get_source_ids.assert_called_once_with("user-1", "civitai-provider", model_id="model-9")


@pytest.mark.asyncio
async def test_import_prompts_for_user_skips_known_source_ids():
    repository = Mock(get_source_ids=Mock(return_value={"1"}))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [
        {"prompt": "a fox", "source_id": "1", "model_id": "model-9"},
        {"prompt": "a wolf", "source_id": "2", "model_id": "model-9"},
    ]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 1, "skipped_duplicates": 1}
    mock_operations.add_prompt.assert_awaited_once()
    assert mock_operations.add_prompt.await_args.kwargs["source_id"] == "2"


@pytest.mark.asyncio
async def test_import_prompts_for_user_pairs_negative_under_shared_group_id():
    repository = Mock(get_source_ids=Mock(return_value=set()))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [
        {"prompt": "a fox", "negative_prompt": "blurry", "source_id": "10", "model_id": "model-9"},
    ]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 2, "skipped_duplicates": 0}
    assert mock_operations.add_prompt.await_count == 2

    positive_call, negative_call = mock_operations.add_prompt.await_args_list
    assert positive_call.kwargs["usage_hint"] == "positive"
    assert negative_call.kwargs["usage_hint"] == "negative"
    assert positive_call.kwargs["source_group_id"] is not None
    assert positive_call.kwargs["source_group_id"] == negative_call.kwargs["source_group_id"]
    assert positive_call.args[2] == "a fox"
    assert negative_call.args[2] == "blurry"


@pytest.mark.asyncio
async def test_import_prompts_for_user_without_negative_has_no_group_id():
    repository = Mock(get_source_ids=Mock(return_value=set()))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [{"prompt": "a fox", "source_id": "10", "model_id": "model-9"}]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 1, "skipped_duplicates": 0}
    call = mock_operations.add_prompt.await_args
    assert call.kwargs["source_group_id"] is None
    assert call.kwargs["usage_hint"] is None


@pytest.mark.asyncio
async def test_import_prompts_for_user_empty_entries_is_a_no_op():
    with patch("src.plugin_api.prompts.get_container") as mock_get_container:
        result = await import_prompts_for_user("user-1", [], source_provider="civitai-provider")

    assert result == {"created": 0, "skipped_duplicates": 0}
    mock_get_container.assert_not_called()


def _writer_container(tmp_path, supports_vision=True):
    from PIL import Image

    (tmp_path / "uploads").mkdir(exist_ok=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "uploads" / "a.png")
    calls = []
    config = SimpleNamespace(id="llm-1", name="Writer", supports_vision=supports_vision, enabled=True)

    class Llm:
        repository = SimpleNamespace(
            get_configuration=lambda llm_id: config if llm_id == "llm-1" else None,
            get_default_configuration=lambda: config,
            get_user_llm_assignments=lambda user_id: [],
        )

        async def complete(self, llm_id, messages, *, system=None, settings=None, tools=()):
            calls.append(messages)
            return SimpleNamespace(text="written prompt")

    from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators

    from src.platform.security.user import AccountType

    preset = SimpleNamespace(id="p", name="Preset P", short_description=None, description=None, llm={})
    presets = SimpleNamespace(
        file_repo=SimpleNamespace(find_preset_by_id=lambda preset_id: preset if preset_id == "p" else None),
        db_repo=SimpleNamespace(get_available_preset_ids_for_user=lambda user_id: ["p"] if user_id == "user-1" else []),
    )
    users = {
        "user-1": SimpleNamespace(account_type=AccountType.USER),
        "user-2": SimpleNamespace(account_type=AccountType.USER),
        "admin-1": SimpleNamespace(account_type=AccountType.ADMIN),
    }
    container = SimpleNamespace(
        prompt_enhancement_manager=PromptEnhancementCollaborators(llm_service=Llm(), preset_collaborators=presets),
        settings=SimpleNamespace(get_file_storage_directory=lambda: str(tmp_path)),
        user_repository=SimpleNamespace(get_by_id=users.get),
    )
    return container, calls


@pytest.mark.asyncio
async def test_write_prompt_for_preset_sends_the_stored_image(tmp_path):
    from src.plugin_api.prompts import write_prompt_for_preset

    container, calls = _writer_container(tmp_path)
    with patch("src.plugin_api.prompts.get_container", return_value=container):
        result = await write_prompt_for_preset(
            "user-1", "Describe it", preset_id="p", mode="txt2img", media=[{"value": "uploads/a.png", "media_type": "image"}],
        )
    assert result["text"] == "written prompt"
    parts = calls[0][0].parts
    assert [type(part).__name__ for part in parts] == ["TextPart", "ImagePart"]


@pytest.mark.asyncio
async def test_write_prompt_for_preset_refuses_media_before_reading_it(tmp_path):
    from src.plugin_api.prompts import PromptWriteRefused, write_prompt_for_preset

    container, calls = _writer_container(tmp_path, supports_vision=False)
    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.encode_media", AsyncMock(side_effect=AssertionError("read media"))):
        with pytest.raises(PromptWriteRefused) as caught:
            await write_prompt_for_preset("user-1", "Describe it", media=[{"value": "uploads/a.png", "media_type": "image"}])
    assert caught.value.code == "vision_not_supported"
    assert calls == []


def test_prompt_writer_status_reads_the_default_llm(tmp_path):
    from src.plugin_api.prompts import prompt_writer_status

    container, _ = _writer_container(tmp_path, supports_vision=False)
    with patch("src.plugin_api.prompts.get_container", return_value=container):
        status = prompt_writer_status("user-1")
    assert status["available"] is True
    assert status["vision"] is False


@pytest.mark.asyncio
async def test_write_prompt_for_preset_refuses_a_preset_the_user_cannot_see(tmp_path):
    from src.plugin_api.prompts import PromptWriteRefused, write_prompt_for_preset

    container, calls = _writer_container(tmp_path)
    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.encode_media", AsyncMock(side_effect=AssertionError("read media"))):
        with pytest.raises(PromptWriteRefused) as caught:
            await write_prompt_for_preset(
                "user-2", "Describe it", preset_id="p", media=[{"value": "uploads/a.png", "media_type": "image"}],
            )
    assert caught.value.code == "preset_not_available"
    assert calls == []


@pytest.mark.asyncio
async def test_write_prompt_for_preset_lets_an_admin_use_any_preset(tmp_path):
    from src.plugin_api.prompts import write_prompt_for_preset

    container, calls = _writer_container(tmp_path)
    with patch("src.plugin_api.prompts.get_container", return_value=container):
        result = await write_prompt_for_preset("admin-1", "Describe it", preset_id="p")
    assert result["text"] == "written prompt"
    assert "Target preset: Preset P" in calls[0][0].parts[0].text
