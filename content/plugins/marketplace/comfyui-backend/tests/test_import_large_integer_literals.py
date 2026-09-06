"""Large-integer literals through the import transport: `workflow_text`
(`AnalyzeWorkflowRequest`/`ImportWorkflowRequest`) as the authoritative,
never-round-tripped-through-JS source, and `_make_json_safe` tagging any
oversized int in a response so a browser's own `JSON.parse` never rounds it.
See `backend/api.py`'s `_resolve_workflow_json`/`_make_json_safe` and
`docs/presets.md`'s "Exact large integers" note.
"""

import copy
import json
from pathlib import Path

import pytest

from backend import api
from backend.preset_import.emit import emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import PresetEmitError
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"

# JS's Number.MAX_SAFE_INTEGER (2**53 - 1) - the last integer a JSON number
# survives a browser's own JSON.parse/JSON.stringify exactly.
MAX_SAFE = 9007199254740991
JUST_OVER_SAFE = 9007199254740993  # 2**53 + 1 - the lead's own repro value
UINT64_MAX = 18446744073709551615


def _load_fixture() -> dict:
    with open(FIXTURES / "sdxl_basic_api.json") as f:
        return json.load(f)


def _with_literal(node_id: str, input_name: str, value) -> dict:
    """A copy of the fixture workflow with one node input's literal value
    replaced."""
    workflow = _load_fixture()
    workflow[node_id]["inputs"][input_name] = value
    return workflow


def _exact_text(workflow_dict: dict) -> str:
    """`json.dumps` is exact for a native Python int at any size - this
    stands in for "the file/paste bytes as authored", never having passed
    through a JS JSON.parse/JSON.stringify round trip."""
    return json.dumps(workflow_dict)


def _find_field_or_none(items: list, field_name: str):
    for item in items:
        if item.get("kind") == "field" and item.get("field_name") == field_name:
            return item
        if "items" in item:
            found = _find_field_or_none(item["items"], field_name)
            if found is not None:
                return found
    return None


def _find_field(items: list, field_name: str) -> dict:
    """The `{kind: "field", field_name, ...}` dict for `field_name`,
    searching recursively through row/group/section containers - a form's
    items nest (see `_build_form_files`/`_item_to_field_yaml`)."""
    found = _find_field_or_none(items, field_name)
    if found is None:
        raise AssertionError(f"no field named {field_name!r} found")
    return found


class TestResolveWorkflowJsonSeam:
    def test_workflow_text_is_authoritative_over_the_dict_field(self):
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        rounded_dict = _with_literal("3", "steps", JUST_OVER_SAFE + 1)  # what a JS round trip would have produced

        resolved = api._resolve_workflow_json(rounded_dict, _exact_text(exact))

        assert resolved["3"]["inputs"]["steps"] == JUST_OVER_SAFE
        assert resolved != rounded_dict

    def test_falls_back_to_the_dict_field_when_no_text_is_given(self):
        workflow = _load_fixture()
        assert api._resolve_workflow_json(workflow, None) is workflow

    def test_malformed_workflow_text_raises_a_clear_400(self):
        with pytest.raises(Exception) as exc_info:
            api._resolve_workflow_json({}, "{not valid json")
        assert exc_info.value.status_code == 400
        assert "workflow_text" in exc_info.value.detail

    def test_non_object_workflow_text_raises_a_clear_400(self):
        with pytest.raises(Exception) as exc_info:
            api._resolve_workflow_json({}, "[1, 2, 3]")
        assert exc_info.value.status_code == 400
        assert "JSON object" in exc_info.value.detail


class TestMakeJsonSafe:
    @pytest.mark.parametrize("value", [0, 1, -1, MAX_SAFE, -MAX_SAFE])
    def test_safe_integers_pass_through_unchanged(self, value):
        assert api._make_json_safe(value) == value

    @pytest.mark.parametrize("value", [MAX_SAFE + 1, -(MAX_SAFE + 1), JUST_OVER_SAFE, UINT64_MAX])
    def test_unsafe_integers_are_tagged_with_their_exact_digits(self, value):
        assert api._make_json_safe(value) == {"__exact_int__": str(value)}

    def test_bool_is_never_tagged(self):
        # bool is an int subclass in Python - must not fall through to the
        # int branch just because `isinstance(True, int)` is true.
        assert api._make_json_safe(True) is True
        assert api._make_json_safe(False) is False

    def test_float_is_left_alone_even_when_huge(self):
        # A Python float is already an IEEE-754 double, same as a JS Number -
        # no precision to lose in transport that wasn't already lost.
        huge_float = float(UINT64_MAX)
        assert api._make_json_safe(huge_float) == huge_float

    def test_numeric_looking_string_is_left_alone(self):
        # Proof this is a value-tree walk, not a digit-pattern rewrite - a
        # string never gets reinterpreted as a number.
        assert api._make_json_safe(str(UINT64_MAX)) == str(UINT64_MAX)

    def test_nested_dict_and_list_are_walked_and_otherwise_preserved(self):
        tree = {
            "a": [1, {"seed": JUST_OVER_SAFE, "label": "x"}, "text"],
            "b": True,
            "c": None,
        }
        safe = api._make_json_safe(tree)
        assert safe["a"][0] == 1
        assert safe["a"][1] == {"seed": {"__exact_int__": str(JUST_OVER_SAFE)}, "label": "x"}
        assert safe["a"][2] == "text"
        assert safe["b"] is True
        assert safe["c"] is None


class TestAnalyzeResponseNeverEchoesAnUnsafeRawInt:
    @pytest.mark.asyncio
    async def test_a_large_seed_literal_comes_back_tagged_not_as_a_raw_number(self):
        exact = _with_literal("3", "seed", UINT64_MAX)
        body = api.AnalyzeWorkflowRequest(workflow=exact, workflow_text=_exact_text(exact))

        response = await api.analyze_workflow(body, current_user=None)

        seed_candidate = next(c for c in response["candidates"] if c["role"] == "seed")
        assert seed_candidate["current_value"] == {"__exact_int__": str(UINT64_MAX)}
        # The raw response text (what a client's own JSON.parse actually
        # sees) must never contain the bare digit run as a JSON number - the
        # only acceptable shape is inside our own string-quoted tag.
        raw_text = json.dumps(response)
        assert f': {UINT64_MAX}' not in raw_text
        assert f', {UINT64_MAX}' not in raw_text

    @pytest.mark.asyncio
    async def test_default_form_field_default_is_tagged_for_an_oversized_mapped_literal(self):
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        body = api.AnalyzeWorkflowRequest(workflow=exact, workflow_text=_exact_text(exact))

        response = await api.analyze_workflow(body, current_user=None)

        steps_field = _find_field(response["default_form"]["tabs"][0]["items"], "steps")
        assert steps_field["default"] == {"__exact_int__": str(JUST_OVER_SAFE)}

    @pytest.mark.asyncio
    async def test_workflow_text_is_authoritative_through_analyze(self):
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        rounded_dict = _with_literal("3", "steps", JUST_OVER_SAFE + 1)
        body = api.AnalyzeWorkflowRequest(workflow=rounded_dict, workflow_text=_exact_text(exact))

        response = await api.analyze_workflow(body, current_user=None)

        steps_candidate = next(c for c in response["candidates"] if c["role"] == "steps")
        assert steps_candidate["current_value"] == {"__exact_int__": str(JUST_OVER_SAFE)}

    @pytest.mark.asyncio
    async def test_requirements_preview_accepts_workflow_text(self):
        exact = _with_literal("3", "seed", UINT64_MAX)
        body = api.AnalyzeWorkflowRequest(workflow=exact, workflow_text=_exact_text(exact))

        response = await api.preview_workflow_requirements(body, current_user=None)

        assert "results" in response


class TestImportPreservesExactLiteralsInTheStoredWorkflow:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _checkpoint_and_steps_form(self, workflow_dict):
        workflow = parse_api_workflow(workflow_dict)
        analysis = suggest_fields(workflow)
        return workflow, form_from_roles(analysis, {"checkpoint", "steps"})

    def test_an_unmapped_oversized_seed_literal_is_byte_exact_in_the_stored_workflow(self, dest_root):
        """`seed` is foundational (never a mapped `Item` - see emit.py's
        module docstring): its literal is carried through into workflow.json
        untouched, exactly as the source had it, regardless of `form`."""
        exact = _with_literal("3", "seed", UINT64_MAX)
        workflow, form = self._checkpoint_and_steps_form(exact)

        result = emit_preset(
            workflow, form, [], model_family="ExactSeed", variant="v1",
            display_name="Exact Seed", dest_root=dest_root,
        )

        workflow_path = result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json"
        stored_text = workflow_path.read_text()
        assert f'"seed": {UINT64_MAX}' in stored_text
        stored = json.loads(stored_text)
        assert stored["3"]["inputs"]["seed"] == UINT64_MAX

    def test_an_oversized_mapped_field_default_is_exact_in_the_pipeline_field_mapping(self, dest_root):
        """`steps` becomes a mapped field (form_from_roles maps it); its
        default (baked into the pipeline's field_mappings Jinja default via
        `json.dumps`) must carry the exact integer, not a rounded one."""
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        workflow, form = self._checkpoint_and_steps_form(exact)

        result = emit_preset(
            workflow, form, [], model_family="ExactSteps", variant="v1",
            display_name="Exact Steps", dest_root=dest_root,
        )

        pipeline_text = (result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text()
        assert str(JUST_OVER_SAFE) in pipeline_text
        assert str(JUST_OVER_SAFE + 1) not in pipeline_text

    @pytest.mark.asyncio
    async def test_source_endpoint_returns_the_exact_stored_text_and_tags_the_dict(self, dest_root, monkeypatch):
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", dest_root)
        exact = _with_literal("3", "seed", UINT64_MAX)
        workflow, form = self._checkpoint_and_steps_form(exact)
        result = emit_preset(
            workflow, form, [], model_family="ExactSource", variant="v1",
            display_name="Exact Source", dest_root=dest_root,
        )
        stored_path = result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json"
        stored_text = stored_path.read_text()

        response = await api.get_imported_preset_source(result.preset_id, current_user=None)

        assert response["workflow_text"] == stored_text
        assert response["workflow"]["3"]["inputs"]["seed"] == {"__exact_int__": str(UINT64_MAX)}
        assert json.loads(response["workflow_text"])["3"]["inputs"]["seed"] == UINT64_MAX

    @pytest.mark.asyncio
    async def test_reload_after_import_keeps_the_literal_exact_with_no_client_involved(self, dest_root, monkeypatch):
        """`reload_imported_preset` never receives a `workflow` from the
        client at all - it always re-reads its own stored file - so this
        proves COMFY-09's staged/swap publication and this card's exact
        parsing don't reintroduce rounding together on a reload."""
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", dest_root)
        monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container")))
        exact = _with_literal("3", "seed", UINT64_MAX)
        workflow, form = self._checkpoint_and_steps_form(exact)
        result = emit_preset(
            workflow, form, [], model_family="ExactReload", variant="v1",
            display_name="Exact Reload", dest_root=dest_root,
        )

        await api.reload_imported_preset(result.preset_id, current_user=None)

        workflow_path = result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json"
        stored = json.loads(workflow_path.read_text())
        assert stored["3"]["inputs"]["seed"] == UINT64_MAX

    @pytest.mark.asyncio
    async def test_ordinary_float_and_numeric_looking_string_are_unaffected(self, dest_root):
        """A control case alongside the big-int ones: an ordinary float
        (`denoise`) and a numeric-looking string (`ckpt_name`, unrelated to
        this card) must not be perturbed by any of this."""
        exact = _with_literal("3", "denoise", 0.75)
        workflow, form = self._checkpoint_and_steps_form(exact)

        result = emit_preset(
            workflow, form, [], model_family="ExactControl", variant="v1",
            display_name="Exact Control", dest_root=dest_root,
        )

        workflow_path = result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json"
        stored = json.loads(workflow_path.read_text())
        assert stored["3"]["inputs"]["denoise"] == 0.75
        assert stored["4"]["inputs"]["ckpt_name"] == "sdxlBase_v10.safetensors"

    def test_a_safe_numeric_edit_still_works_through_the_mapped_field(self, dest_root):
        """A representable (in-range) edit to the mapped `steps` field must
        still flow through normally - this card must not make ordinary
        values harder to edit."""
        exact = _with_literal("3", "steps", 20)
        workflow, form = self._checkpoint_and_steps_form(exact)
        for item in form.tabs[0].items:
            if item.field_name == "steps":
                item.default = 35

        result = emit_preset(
            workflow, form, [], model_family="SafeEdit", variant="v1",
            display_name="Safe Edit", dest_root=dest_root,
        )

        pipeline_text = (result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text()
        assert "35" in pipeline_text


class TestSchemaDriftUnaffectedByWorkflowText:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_fingerprint_round_trips_through_workflow_text(self, dest_root):
        """COMFY-04's schema-drift refusal must keep working when the
        workflow arrives via `workflow_text` instead of the plain dict -
        analyzing and then importing the same exact content must never be
        seen as drift."""
        exact = _with_literal("3", "seed", UINT64_MAX)
        workflow = parse_api_workflow(json.loads(_exact_text(exact)))
        analysis = suggest_fields(workflow)
        from backend.preset_import.suggest import classification_fingerprint

        fingerprint = classification_fingerprint(analysis)
        form = form_from_roles(analysis, {"checkpoint"})

        # Re-parsing the exact same text a second time (as import_workflow's
        # own _resolve_analysis would) must reproduce the same fingerprint -
        # no PresetEmitError from `_check_schema_drift`.
        workflow2 = parse_api_workflow(json.loads(_exact_text(exact)))
        try:
            emit_preset(
                workflow2, form, [], model_family="DriftCheck", variant="v1",
                display_name="Drift Check", dest_root=dest_root,
                expected_schema_fingerprint=fingerprint, expected_object_info_used=False,
            )
        except PresetEmitError as e:
            pytest.fail(f"unexpected schema-drift refusal: {e}")


def _wizard_form_from_default(default_form: dict) -> dict:
    """The `form` the wizard actually posts for `default_form`: its
    `hydrateItem` drops any item whose `default` arrived tagged, because
    such a field can neither be edited without rounding nor posted unmapped
    (`validate_against_workflow` refuses a field with no mappings). Mirrors
    ImportWorkflowTab.svelte's own hydration, so this test posts the shape
    the shipped dist produces."""

    def prune(items: list) -> list:
        kept = []
        for item in items:
            default = item.get("default")
            if item.get("kind") == "field" and isinstance(default, dict) and "__exact_int__" in default:
                continue
            if "items" in item:
                item["items"] = prune(item["items"])
            kept.append(item)
        return kept

    form = copy.deepcopy(default_form)
    for tab in form["tabs"]:
        tab["items"] = prune(tab["items"])
    return form


class TestImportingAnAutoMappedOversizedLiteral:
    """The end-to-end shape the wizard posts when the backend's own
    `default_form` auto-mapped an "obvious" input whose literal had to be
    tagged: the field is not in the form at all, and the import succeeds
    with the workflow's baked-in literal left exactly as authored."""

    @pytest.fixture()
    def dest_root(self, tmp_path, monkeypatch):
        root = tmp_path / "presets"
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
        return root

    async def _analyze(self, workflow: dict):
        return await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=workflow, workflow_text=_exact_text(workflow)),
            current_user=None,
        )

    @pytest.mark.asyncio
    async def test_the_wizards_payload_imports_and_keeps_the_literal_byte_exact(self, dest_root):
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        analysis = await self._analyze(exact)
        assert _find_field(analysis["default_form"]["tabs"][0]["items"], "steps")["default"] == {
            "__exact_int__": str(JUST_OVER_SAFE)
        }, "precondition: the backend auto-maps steps with a tagged default"

        form = _wizard_form_from_default(analysis["default_form"])
        assert _find_field_or_none(form["tabs"][0]["items"], "steps") is None

        body = api.ImportWorkflowRequest(
            workflow=exact,
            workflow_text=_exact_text(exact),
            form=form,
            history=[],
            model_family="AutoMappedExact",
            variant="imported",
            display_name="Auto-mapped Exact",
            schema_fingerprint=analysis["schema_fingerprint"],
            schema_object_info_used=analysis["object_info_used"],
        )

        response = await api.import_workflow(body, current_user=None)

        preset_dir = Path(response["path"])
        workflow_text = (
            preset_dir / "modes" / response["mode"] / "files" / "workflows" / f"{response['mode']}.json"
        ).read_text()
        assert f'"steps": {JUST_OVER_SAFE}' in workflow_text
        assert json.loads(workflow_text)["3"]["inputs"]["steps"] == JUST_OVER_SAFE

        sidecar = json.loads((preset_dir / "import.json").read_text())
        assert _find_field_or_none(sidecar["form"]["tabs"][0]["items"], "steps") is None
        assert (preset_dir / "modes" / response["mode"] / "tabs" / "generation.yml").read_text().count("steps") == 0

        # Reload re-reads the preset's own stored files - the literal must
        # survive that round trip too.
        await api.reload_imported_preset(response["preset_id"], current_user=None)
        assert json.loads(workflow_text)["3"]["inputs"]["steps"] == JUST_OVER_SAFE

    @pytest.mark.asyncio
    async def test_keeping_the_field_with_no_mappings_is_refused_by_name(self, dest_root):
        """The contract the payload above is shaped around: a form entry
        with an empty `mappings` is a 400, naming the field."""
        exact = _with_literal("3", "steps", JUST_OVER_SAFE)
        analysis = await self._analyze(exact)

        form = copy.deepcopy(analysis["default_form"])
        steps = _find_field(form["tabs"][0]["items"], "steps")
        steps["default"] = None
        steps["mappings"] = []

        body = api.ImportWorkflowRequest(
            workflow=exact,
            workflow_text=_exact_text(exact),
            form=form,
            history=[],
            model_family="UnmappedExact",
            variant="imported",
            display_name="Unmapped Exact",
            schema_fingerprint=analysis["schema_fingerprint"],
            schema_object_info_used=analysis["object_info_used"],
        )

        with pytest.raises(Exception) as exc_info:
            await api.import_workflow(body, current_user=None)

        assert exc_info.value.status_code == 400
        assert "field 'steps': has no mappings" in exc_info.value.detail
