from types import SimpleNamespace

from src.features.recipes.preset_links import RecipePresetLinks
from src.features.recipes.schema import Recipe, RecipeArtifact, RecipePresetRef, RecipeStep


class _Loader:
    def __init__(self, requires_by_id):
        self._requires = requires_by_id

    def load_preset_by_id(self, preset_id):
        if preset_id not in self._requires:
            return None
        return SimpleNamespace(name=preset_id, media=None, requires=self._requires[preset_id])


class _Runner:
    def get_latest_completed_run(self, recipe_id):
        return None


def _artifact(artifact_id, host=None):
    return RecipeArtifact(
        id=artifact_id, kind="checkpoint", model_type="checkpoint",
        filename=f"{artifact_id}.safetensors", source_host=host,
    )


def _recipe(preset_ids=("P1",), artifacts=(), **kwargs):
    return Recipe(
        id="r", schema_version=1, version=1, name="R", engine="native",
        artifacts=list(artifacts),
        presets=[RecipePresetRef(preset_id=p) for p in preset_ids],
        **kwargs,
    )


def _links(requires_by_id):
    return RecipePresetLinks(None, _Runner(), preset_loader=_Loader(requires_by_id))


def _summary(links, recipe):
    return links.summaries([recipe])[0]


def test_vram_taken_from_linked_preset():
    summary = _summary(_links({"P1": {"min_vram_gb": 12, "recommended_vram_gb": 16}}), _recipe())
    assert (summary.min_vram_gb, summary.recommended_vram_gb) == (12, 16)


def test_vram_is_max_across_presets_and_rounded_up():
    links = _links({"P1": {"min_vram_gb": 8, "recommended_vram_gb": 12.5}, "P2": {"min_vram_gb": 10}})
    summary = _summary(links, _recipe(preset_ids=("P1", "P2")))
    assert (summary.min_vram_gb, summary.recommended_vram_gb) == (10, 13)


def test_vram_partial_and_absent_are_none():
    only_recommended = _summary(_links({"P1": {"recommended_vram_gb": 16}}), _recipe())
    assert (only_recommended.min_vram_gb, only_recommended.recommended_vram_gb) == (None, 16)
    none_declared = _summary(_links({"P1": {}}), _recipe())
    assert (none_declared.min_vram_gb, none_declared.recommended_vram_gb) == (None, None)


def test_vram_none_without_preset_loader_or_unknown_preset():
    no_loader = _summary(RecipePresetLinks(None, _Runner()), _recipe())
    assert no_loader.min_vram_gb is None
    unknown = _summary(_links({}), _recipe())
    assert unknown.recommended_vram_gb is None


def test_sources_are_distinct_in_declaration_order():
    recipe = _recipe(artifacts=[
        _artifact("a", "huggingface.co"), _artifact("b", "example.org"),
        _artifact("c", "huggingface.co"), _artifact("d"),
    ])
    assert _summary(_links({}), recipe).sources == ["huggingface.co", "example.org"]


class _Planner:
    def __init__(self, sources):
        self.sources = sources

    def summary(self, preset_id, selected=(), locked=()):
        return {"total_bytes": 5, "sources": self.sources}


def _plan_recipe(artifacts=()):
    return _recipe(
        artifacts=artifacts,
        steps=[RecipeStep(key="plan", kind="modes.plan", title="Plan", params={"preset_id": "P1"})],
    )


def _planned_links(sources):
    return RecipePresetLinks(None, _Runner(), preset_loader=_Loader({}), mode_planner=_Planner(sources))


def test_sources_come_from_the_planned_files_when_recipe_has_no_artifacts():
    summary = _summary(_planned_links(["huggingface.co"]), _plan_recipe())
    assert summary.sources == ["huggingface.co"]
    assert summary.total_download_bytes == 5


def test_sources_merge_artifacts_and_plan_without_duplicates():
    recipe = _plan_recipe([_artifact("a", "example.org"), _artifact("b", "huggingface.co")])
    summary = _summary(_planned_links(["huggingface.co", "cdn.test"]), recipe)
    assert summary.sources == ["example.org", "huggingface.co", "cdn.test"]


def test_blurb_and_first_run_pass_through():
    summary = _summary(_links({}), _recipe(blurb="Fast.", first_run=True))
    assert summary.blurb == "Fast."
    assert summary.first_run is True
    default = _summary(_links({}), _recipe())
    assert default.blurb is None and default.first_run is False and default.sources == []
