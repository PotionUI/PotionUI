from unittest.mock import Mock, patch

import pytest

from src.features.forms.binding import BoundForm
from tests.features.generation.test_orchestrator_fixed_prompt import FILM, PREFIX, SUFFIX, make_orchestrator, pipeline_prompts
from tests.features.kits.test_orchestrator import Harness
from src.features.sessions.generate import Overrides
from tests.features.sessions.support import build_service, entry, field, memory_sessions, save, segment, template


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None, **_):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough):
        yield


@pytest.fixture
def sessions(tmp_path):
    with memory_sessions() as (repository, _):
        yield repository, str(tmp_path)


@pytest.fixture
def generation_repo():
    with patch("src.features.generation.orchestrator.generation_repo") as repo, \
         patch("src.features.generation.status_tracker.generation_repo", repo):
        repo.get_by_id = Mock(return_value=Mock(user_id="u1"))
        yield repo


@pytest.mark.asyncio
async def test_a_mirrors_fixed_prompt_text_applies_to_a_session_run_and_is_never_returned(sessions, generation_repo):
    repository, storage = sessions
    save(repository)
    service = build_service(repository, storage)
    prepared = service.prepare("u1", False, "s1", None, Overrides(prompt="a red fox", count=1))
    orchestrator, applied, _ = make_orchestrator(FILM)

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g1"):
        result = await orchestrator.start_generation(prepared.request, "u1")

    assert [pair["positive"] for pair in pipeline_prompts(orchestrator)] == [f"{PREFIX} a red fox {SUFFIX}"]
    assert applied.saved["g1"][0]["positive"] == f"{PREFIX} a red fox {SUFFIX}"
    for secret in (PREFIX, SUFFIX):
        assert secret not in repr(prepared.resolved_request)
        assert secret not in repr(result)


@pytest.mark.asyncio
async def test_a_kit_citation_in_a_sessions_prompt_expands_like_the_ui_because_it_goes_through_the_orchestrator(mock_db, sessions):
    repository, storage = sessions
    harness = Harness(mock_db)
    face, _ = harness.add("face")
    cite = f"@[references:kit:{harness.kit.id}/{face.id}]"
    fields = [field("references", "image", configuration={"multi": True})]
    save(repository, data={"refs": entry(
        promptSegments=[segment("a", f"{cite} smiles")], prompt="", formData={"references": [{"kit": harness.kit.id}]},
    )})
    service = build_service(repository, storage, template(fields=fields, modes=("refs",)))

    prepared = service.prepare("u1", False, "s1", None, Overrides())
    result = await harness.orchestrator.start_generation(prepared.request, "u1")

    assert prepared.resolved_request["prompts"][0]["positive"] == f"{cite} smiles"
    assert prepared.request.prompts[0].positive == "<Picture 1> smiles"
    assert result["kits"]["used"][0]["name"] == "face"
