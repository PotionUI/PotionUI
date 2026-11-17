import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.api_keys.operations.resolver import ApiKeyResolver
from src.features.forms.binding import bind_form
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp import routes as mcp_routes
from src.features.mcp.protocol import McpToolCollaborators
from src.features.mcp.tool_policy import POLICY_ALL_ON
from src.features.mcp.tool_repository import McpToolRepository
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from src.features.tags.repository import TagRepository
from src.platform.security import current_user
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.features.api_keys.conftest import Stack
from tests.features.api_keys.support import World
from tests.features.generation.test_orchestrator_fixed_prompt import FILM, make_orchestrator

PRESET = "krea2"


def lora_schema(filter_tags=None):
    return {
        "form_schema": {
            "properties": {
                "tabs": {
                    "type": "tabs",
                    "children": [
                        {
                            "type": "tab",
                            "children": [
                                {"type": "model", "name": "diffusion_model", "configuration": {"model_type": "diffusion_model"}},
                                {
                                    "type": "lora_picker",
                                    "name": "loras",
                                    "configuration": {"model_type": "lora", "filter_tags": filter_tags, "max_items": 10},
                                },
                            ],
                        }
                    ],
                }
            }
        }
    }


class Library:
    def __init__(self):
        self.repo = ModelRepository()
        self.tags = TagRepository()
        self.relative = {}

    def add(self, filename, model_type="lora", tags=(), relative=None, available=True):
        model = self.repo.create(Model(filename=filename, model_type=model_type, file_size=1, sha256=filename))
        if tags:
            ids = [self.tags.create_tag(name).id for name in tags]
            self.tags.set_model_tags(model.id, ids)
        if relative:
            self.relative[relative] = model
        if not available:
            from src.platform.database import get_cursor

            with get_cursor() as cursor:
                cursor.execute("UPDATE models SET is_available = 0 WHERE id = ?", (model.id,))
        return model

    def tag_id(self, name):
        return self.tags.create_tag(name).id

    def manager(self):
        return SimpleNamespace(
            model_repo=self.repo,
            locator=SimpleNamespace(model_for_path=lambda path: self.relative.get(str(path).replace("\\", "/"))),
        )


@pytest.fixture
def rig(keys_db):
    stack = Stack()
    world = World(stack)
    stack.settings.set_setting("mcp_enabled", True)
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: world.sessions.get(token)
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(ApiKeyResolver(stack.keys, stack.access, stack.users, stack.settings))
    current_user._key_failures.reset("testclient")

    library = Library()
    bound = []

    def capture(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None, **_):
        from src.features.forms.binding import BoundForm

        bound.append(dict(raw_form_data or {}))
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    orchestrator, _, _ = make_orchestrator(FILM)
    orchestrator.preset_template_loader.load_preset_by_id = lambda preset_id: Mock(engine="native", vars={})
    preset_collaborators = SimpleNamespace(
        db_repo=Mock(),
        file_repo=SimpleNamespace(find_preset_by_id=lambda preset_id: SimpleNamespace(modes={"txt2img": {}}, vars={})),
    )
    registry = ToolRegistry()
    registry.register(StartGenerationTool())
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    collaborators = McpToolCollaborators(
        tool_registry=registry,
        tool_repository=McpToolRepository(),
        settings=Settings(SettingRepository()),
        services=ToolServices(
            llm_repository=llm_repository,
            generation_orchestrator=orchestrator,
            settings=Mock(get_setting=Mock(return_value="")),
            model_index_manager=library.manager(),
            preset_collaborators=preset_collaborators,
        ),
    )
    for user in (world.alice, world.bob):
        McpToolRepository().apply_default(user.id, POLICY_ALL_ON, {})
    container = SimpleNamespace(
        api_key_collaborators=ApiKeyCollaborators(
            keys=stack.keys, events=stack.events, access=stack.access, settings=stack.settings
        ),
        user_repository=stack.users,
        mcp_tool_collaborators=collaborators,
    )
    app = FastAPI()
    app.include_router(mcp_routes.build_router(container))
    schema = {"value": lora_schema()}
    with patch("src.features.generation.orchestrator.bind_form", side_effect=capture), \
            patch("src.features.llm.tools.builtin.start_generation_tool.preset_available_to_user", return_value=True), \
            patch("src.features.presets.operations.get_form_schema", side_effect=lambda *a, **k: schema["value"]), \
            TestClient(app, raise_server_exceptions=False) as client:
        _, secret = world.mint(world.alice, scopes=("mcp",), name="agent")
        yield SimpleNamespace(client=client, world=world, library=library, bound=bound, secret=secret, schema=schema)
    current_user._auth, current_user._key_resolver = previous
    current_user._key_failures.reset("testclient")


def start(rig, loras, preset_id=PRESET):
    response = rig.client.post(
        "/api/mcp",
        headers=rig.world.bearer(rig.secret),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "start_generation",
                "arguments": {"preset_id": preset_id, "prompt": "a fox", "form_overrides": {"loras": loras}},
            },
        },
    ).json()
    result = response["result"]
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


def sent_rows(rig):
    return rig.bound[-1]["loras"]


def test_attach_by_model_id_with_strength(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, body = start(rig, [{"model": lora.id, "strength": 0.7}])

    assert not failed and body["generation_id"]
    assert sent_rows(rig) == [{"model": f"model:{lora.id}", "strength": 0.7}]


def test_attach_by_model_ref(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, _ = start(rig, [{"model": f"model:{lora.id}", "strength": 1.0}])

    assert not failed
    assert sent_rows(rig) == [{"model": f"model:{lora.id}", "strength": 1.0}]


def test_attach_by_file_name(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, _ = start(rig, [{"model": "fox.safetensors", "strength": 1.0}])

    assert not failed
    assert sent_rows(rig) == [{"model": f"model:{lora.id}", "strength": 1.0}]


def test_attach_by_library_relative_path(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",), relative="krea2/fox.safetensors")

    failed, _ = start(rig, [{"model": "krea2/fox.safetensors"}])

    assert not failed
    assert sent_rows(rig) == [{"model": f"model:{lora.id}"}]


def test_bare_string_and_object_shapes_resolve(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, _ = start(rig, [lora.id, {"modelPath": "fox.safetensors", "weight": 0.5}])

    assert not failed
    assert sent_rows(rig) == [
        {"model": f"model:{lora.id}"},
        {"model": f"model:{lora.id}", "strength": 0.5},
    ]


def test_row_fields_pass_through(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    start(rig, [{"model": lora.id, "strength": 1, "step_start": 1, "step_end": 4}])

    assert sent_rows(rig) == [{"model": f"model:{lora.id}", "strength": 1, "step_start": 1, "step_end": 4}]


def test_unknown_reference_lists_the_accepted_forms(rig):
    failed, text = start(rig, [{"model": "nope.safetensors"}])

    assert failed
    assert "no installed LoRA matches 'nope.safetensors'" in text
    assert "model id" in text and "model:<id>" in text and "file name" in text and "library-relative path" in text


def test_ambiguous_file_name_names_the_candidates(rig):
    first = rig.library.add("dup.safetensors")
    second = rig.library.add("dup2.safetensors")

    with patch.object(rig.library.repo, "get_by_filename", return_value=[first, second]):
        failed, text = start(rig, [{"model": "dup.safetensors"}])

    assert failed
    assert first.id in text and second.id in text and "matches 2 installed LoRAs" in text


def test_same_file_name_of_another_type_is_not_a_lora(rig):
    rig.library.add("shared.safetensors", model_type="vae")

    failed, text = start(rig, [{"model": "shared.safetensors"}])

    assert failed and "no installed LoRA matches" in text


def test_a_model_id_of_another_type_is_refused_by_type(rig):
    vae = rig.library.add("vae.safetensors", model_type="vae")

    failed, text = start(rig, [{"model": vae.id}])

    assert failed and "is a vae model" in text


def test_unavailable_lora_is_refused(rig):
    lora = rig.library.add("gone.safetensors", available=False)

    failed, text = start(rig, [{"model": lora.id}])

    assert failed and "not available" in text


def test_non_numeric_strength_is_refused(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, text = start(rig, [{"model": lora.id, "strength": "strong"}])

    assert failed and "strength" in text and "must be a number" in text


def test_untagged_lora_attaches_with_a_warning_when_the_picker_has_no_base_model_filter(rig):
    lora = rig.library.add("untagged.safetensors")

    failed, body = start(rig, [{"model": lora.id}])

    assert not failed
    assert sent_rows(rig) == [{"model": f"model:{lora.id}"}]
    assert any("no base-model tag" in warning for warning in body["warnings"])


def test_tagged_lora_attaches_without_a_warning(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    _, body = start(rig, [{"model": lora.id}])

    assert "warnings" not in body


def test_untagged_lora_is_refused_when_the_picker_filters_by_base_model(rig):
    krea = rig.library.tag_id("KREA2")
    rig.schema["value"] = lora_schema(filter_tags=[krea])
    lora = rig.library.add("untagged.safetensors")

    failed, text = start(rig, [{"model": lora.id}])

    assert failed
    assert "has no base-model tag" in text and krea in text and "Models" in text


def test_lora_of_another_base_model_is_refused_when_the_picker_filters(rig):
    krea = rig.library.tag_id("KREA2")
    rig.schema["value"] = lora_schema(filter_tags=[krea])
    lora = rig.library.add("sdxl.safetensors", tags=("SDXL",))

    failed, text = start(rig, [{"model": lora.id}])

    assert failed and "tagged SDXL" in text and "another base model" in text


def test_lora_with_the_required_base_model_tag_attaches_when_the_picker_filters(rig):
    krea = rig.library.tag_id("KREA2")
    rig.schema["value"] = lora_schema(filter_tags=[krea])
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))

    failed, _ = start(rig, [{"model": lora.id}])

    assert not failed


def test_empty_loras_still_start(rig):
    failed, _ = start(rig, [])

    assert not failed


def preset_with_loras():
    field = FieldTemplate(
        type="lora_picker", name="loras", default=[], configuration={"model_type": "lora", "max_items": 10}
    )
    return PresetTemplate(
        id="p", name="P", version="1", path="/p",
        modes={"txt2img": ModeTemplate(forms=[FormTemplate(name="custom", fields=[field], default=True, order=0)], pipes=[])},
    )


def test_the_resolved_rows_bind(rig):
    lora = rig.library.add("fox.safetensors", tags=("KREA2",))
    start(rig, [{"model": "fox.safetensors", "strength": 0.6}])

    bound = bind_form(preset_with_loras(), "txt2img", None, {"loras": sent_rows(rig)}, "u")

    assert bound.values["loras"] == [{"model": f"model:{lora.id}", "strength": 0.6}]


def test_the_shape_the_web_form_submits_still_binds():
    rows = [{"model": "model:01ABC", "strength": 0.8}]

    bound = bind_form(preset_with_loras(), "txt2img", None, {"loras": rows}, "u")

    assert bound.values["loras"] == rows


def test_a_saved_sessions_stored_rows_still_bind():
    stored = [{"model": "krea2_style.safetensors", "strength": 1.0}, {"model": "model:01XYZ", "strength": -0.5}]

    bound = bind_form(preset_with_loras(), "txt2img", None, {"loras": stored}, "u")

    assert [row["model"] for row in bound.values["loras"]] == ["krea2_style.safetensors", "model:01XYZ"]
    assert bound.values["loras"][1]["strength"] == -0.5
