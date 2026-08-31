"""Availability-driven backend narrowing.

Selection is not circular: the picker shows the union across an engine's backends, then
the chosen models narrow the candidates. See docs/models.md.
"""

import pytest
from unittest.mock import Mock, patch

from src.features.models import availability as av


def registry_with(*backend_ids):
    """Backends are returned in priority order; that order must survive narrowing."""
    registry = Mock()
    backends = []
    for bid in backend_ids:
        backend = Mock(backend_id=bid)
        backend.name = bid
        backends.append(backend)
    registry.get_backends_for_engine.return_value = backends
    return registry


def test_no_models_selected_leaves_every_backend_of_the_engine():
    registry = registry_with("comfy_a", "comfy_b")
    assert av.candidate_backends("comfyui", [], registry) == ["comfy_a", "comfy_b"]


@patch.object(av, "model_availability_repo")
def test_narrows_to_backends_holding_every_selected_model(repo):
    repo.backends_holding.return_value = {"comfy_b"}
    registry = registry_with("comfy_a", "comfy_b")

    assert av.candidate_backends("comfyui", ["m1", "m2"], registry) == ["comfy_b"]
    repo.backends_holding.assert_called_once_with(["m1", "m2"])


@patch.object(av, "model_availability_repo")
def test_priority_order_is_preserved_after_narrowing(repo):
    repo.backends_holding.return_value = {"comfy_b", "comfy_a"}
    registry = registry_with("comfy_a", "comfy_b")

    assert av.candidate_backends("comfyui", ["m1"], registry) == ["comfy_a", "comfy_b"]


@patch.object(av, "model_availability_repo")
def test_backend_holding_only_some_models_is_excluded(repo):
    """A backend with the checkpoint but not the LoRA cannot run the generation."""
    repo.backends_holding.return_value = set()
    registry = registry_with("comfy_a")

    assert av.candidate_backends("comfyui", ["ckpt", "lora"], registry) == []


@patch.object(av, "model_repo", create=True)
@patch.object(av, "model_availability_repo")
def test_require_names_the_models_no_backend_holds(repo, _model_repo):
    repo.backends_holding.return_value = set()
    repo.backend_ids_by_model.return_value = {}
    registry = registry_with("comfy_a")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = Mock(filename="missing.safetensors")
        with pytest.raises(av.NoBackendHoldsAllModelsError) as exc:
            av.require_candidate_backends("comfyui", ["m1"], registry)

    assert "missing.safetensors" in str(exc.value)


@patch.object(av, "model_availability_repo")
def test_require_distinguishes_scattered_models_from_missing_ones(repo):
    """Each model exists somewhere, but no single backend holds them all - the error
    names which backend holds which model, or the operator can't act on it."""
    repo.backends_holding.return_value = set()
    repo.backend_ids_by_model.return_value = {"m1": ["comfy_a"], "m2": ["comfy_b"]}
    repo.conflicts_for.return_value = []
    registry = registry_with("comfy_a", "comfy_b")

    def _get_by_id(model_id, **kwargs):
        return Mock(filename=f"{model_id}.safetensors")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.side_effect = _get_by_id
        with pytest.raises(av.NoBackendHoldsAllModelsError) as exc:
            av.require_candidate_backends("comfyui", ["m1", "m2"], registry)

    message = str(exc.value)
    assert "no single backend holds all of them" in message
    assert "'m1.safetensors' is on comfy_a" in message
    assert "'m2.safetensors' is on comfy_b" in message


@patch.object(av, "model_repo", create=True)
@patch.object(av, "model_availability_repo")
def test_require_explains_digest_conflict_distinctly_from_missing(repo, _model_repo):
    """A conflicted claim is not "never indexed" - the operator needs to know which
    of those two very different problems they have."""
    repo.backends_holding.return_value = set()
    repo.backend_ids_by_model.return_value = {}
    repo.conflicts_for.return_value = [Mock(model_id="m1", backend_id="comfy_a", digest="b" * 64)]
    registry = registry_with("comfy_a")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = Mock(filename="flux.safetensors", sha256="a" * 64)
        with pytest.raises(av.NoBackendHoldsAllModelsError) as exc:
            av.require_candidate_backends("comfyui", ["m1"], registry)

    message = str(exc.value)
    assert "Digest conflict" in message
    assert "flux.safetensors" in message
    assert "comfy_a" in message
    assert "missing.safetensors" not in message


@patch.object(av, "model_repo", create=True)
@patch.object(av, "model_availability_repo")
def test_require_reports_both_missing_and_conflicted_models_together(repo, _model_repo):
    repo.backends_holding.return_value = set()
    repo.backend_ids_by_model.return_value = {}
    repo.conflicts_for.return_value = [Mock(model_id="m_conflict", backend_id="comfy_a", digest="b" * 64)]
    registry = registry_with("comfy_a")

    def _get_by_id(model_id, **kwargs):
        if model_id == "m_conflict":
            return Mock(filename="flux.safetensors", sha256="a" * 64)
        return Mock(filename="missing.safetensors")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.side_effect = _get_by_id
        with pytest.raises(av.NoBackendHoldsAllModelsError) as exc:
            av.require_candidate_backends("comfyui", ["m_missing", "m_conflict"], registry)

    message = str(exc.value)
    assert "Not available on any backend: missing.safetensors" in message
    assert "Digest conflict" in message and "flux.safetensors" in message


@patch.object(av, "model_availability_repo")
def test_models_for_engine_badges_each_model_with_its_backends(repo):
    repo.any_indexed.return_value = True
    repo.model_ids_for_backends.return_value = ["m1", "m2"]
    repo.backend_ids_by_model.return_value = {"m1": ["comfy_a", "comfy_b"], "m2": ["comfy_a"]}
    registry = registry_with("comfy_a", "comfy_b")

    model_repo = Mock()
    m1, m2 = Mock(id="m1"), Mock(id="m2")
    m1.to_dict.return_value = {"id": "m1"}
    m2.to_dict.return_value = {"id": "m2"}
    model_repo.get_all.return_value = [m1, m2]

    result = av.models_for_engine("comfyui", registry, model_repository=model_repo, admin=True)

    assert result[0]["backend_ids"] == ["comfy_a", "comfy_b"]
    assert result[1]["backend_ids"] == ["comfy_a"]


@patch.object(av, "model_availability_repo")
def test_engine_with_nothing_available_returns_early_without_querying_models(repo):
    """Indexed, but this engine's backends hold nothing."""
    repo.any_indexed.return_value = True
    repo.model_ids_for_backends.return_value = []
    registry = registry_with("comfy_a")

    model_repo = Mock()

    assert av.models_for_engine("comfyui", registry, model_repository=model_repo) == []
    model_repo.get_all.assert_not_called()


@patch.object(av, "model_availability_repo")
def test_availability_is_pushed_into_the_query_not_applied_to_its_results(repo):
    """Post-filtering would force the call to be unpaginated: `get_all` loads providers
    and tags per row, so the picker would fetch the whole library on every open."""
    repo.any_indexed.return_value = True
    repo.model_ids_for_backends.return_value = ["m1", "m2"]
    repo.backend_ids_by_model.return_value = {"m1": ["comfy_a"]}
    registry = registry_with("comfy_a")

    model_repo = Mock()
    m1 = Mock(id="m1")
    m1.to_dict.return_value = {"id": "m1"}
    model_repo.get_all.return_value = [m1]

    av.models_for_engine("comfyui", registry, model_repository=model_repo, limit=50)

    kwargs = model_repo.get_all.call_args.kwargs
    assert kwargs["allowed_model_ids"] == ["m1", "m2"]
    assert kwargs["limit"] == 50


@patch.object(av, "model_availability_repo")
def test_callers_may_override_provider_and_tag_loading(repo):
    """Readiness passes include_providers/include_tags=False for its existence
    probe; they must override the defaults, not collide with them (TypeError)."""
    repo.any_indexed.return_value = True
    repo.model_ids_for_backends.return_value = ["m1"]
    repo.backend_ids_by_model.return_value = {"m1": ["comfy_a"]}
    registry = registry_with("comfy_a")

    model_repo = Mock()
    model_repo.get_all.return_value = []

    av.models_for_engine(
        "comfyui",
        registry,
        model_repository=model_repo,
        limit=1,
        include_providers=False,
        include_tags=False,
    )

    kwargs = model_repo.get_all.call_args.kwargs
    assert kwargs["include_providers"] is False
    assert kwargs["include_tags"] is False


@patch.object(av, "model_availability_repo")
def test_unindexed_engine_does_not_constrain_the_query(repo):
    repo.any_indexed.return_value = False
    registry = registry_with("comfy_a")

    model_repo = Mock()
    model_repo.get_all.return_value = []

    av.models_for_engine("comfyui", registry, model_repository=model_repo)

    assert model_repo.get_all.call_args.kwargs["allowed_model_ids"] is None
    repo.model_ids_for_backends.assert_not_called()


def test_engine_with_no_enabled_backend_yields_nothing():
    registry = registry_with()
    assert av.models_for_engine("comfyui", registry, model_repository=Mock()) == []


@patch.object(av, "model_availability_repo")
def test_unindexed_engine_lists_everything_rather_than_showing_an_empty_picker(repo):
    """Nothing indexed means nobody asked, not that nothing is available."""
    repo.any_indexed.return_value = False
    registry = registry_with("comfy_a")

    model_repo = Mock()
    m1 = Mock(id="m1")
    m1.to_dict.return_value = {"id": "m1"}
    model_repo.get_all.return_value = [m1]

    result = av.models_for_engine("comfyui", registry, model_repository=model_repo, admin=True)

    assert len(result) == 1
    assert result[0]["backend_ids"] == [], "unbadged: availability is unknown, not empty"
    repo.backend_ids_by_model.assert_not_called()


@patch.object(av, "model_availability_repo")
def test_non_admin_picker_never_sees_backend_topology(repo):
    """Which backends hold a model is operational detail. The user picks a model; the
    system routes it. Badges are admin-only."""
    repo.any_indexed.return_value = True
    repo.model_ids_for_backends.return_value = ["m1"]
    repo.backend_ids_by_model.return_value = {"m1": ["comfy_a"]}
    registry = registry_with("comfy_a")

    model_repo = Mock()
    m1 = Mock(id="m1")
    m1.to_dict.return_value = {"id": "m1", "name": "detail"}
    model_repo.get_all.return_value = [m1]

    result = av.models_for_engine("comfyui", registry, model_repository=model_repo, admin=False)

    assert "backend_ids" not in result[0]
    assert m1.to_dict.call_args.kwargs["admin"] is False


class TestUserAllowedModelIds:
    """`user_allowed_model_ids`: a second, independent
    restriction on top of engine availability - strict (empty means nothing),
    None means unrestricted (admin)."""

    @patch.object(av, "model_availability_repo")
    def test_none_means_unrestricted(self, repo):
        """The admin case: no user filter, only availability applies."""
        repo.any_indexed.return_value = True
        repo.model_ids_for_backends.return_value = ["m1", "m2"]
        repo.backend_ids_by_model.return_value = {}
        registry = registry_with("comfy_a")

        model_repo = Mock()
        model_repo.get_all.return_value = []

        av.models_for_engine("comfyui", registry, model_repository=model_repo, user_allowed_model_ids=None)

        assert model_repo.get_all.call_args.kwargs["allowed_model_ids"] == ["m1", "m2"]

    @patch.object(av, "model_availability_repo")
    def test_empty_list_yields_nothing_without_querying(self, repo):
        """STRICT: no assignments = empty list, not unfiltered."""
        repo.any_indexed.return_value = True
        repo.model_ids_for_backends.return_value = ["m1", "m2"]
        registry = registry_with("comfy_a")

        model_repo = Mock()

        result = av.models_for_engine(
            "comfyui", registry, model_repository=model_repo, user_allowed_model_ids=[],
        )

        assert result == []
        model_repo.get_all.assert_not_called()

    @patch.object(av, "model_availability_repo")
    def test_intersects_with_availability_when_both_indexed_and_scoped(self, repo):
        repo.any_indexed.return_value = True
        repo.model_ids_for_backends.return_value = ["m1", "m2", "m3"]
        repo.backend_ids_by_model.return_value = {}
        registry = registry_with("comfy_a")

        model_repo = Mock()
        model_repo.get_all.return_value = []

        av.models_for_engine(
            "comfyui", registry, model_repository=model_repo, user_allowed_model_ids=["m2", "m4"],
        )

        # m4 isn't available on this engine at all; only the intersection survives.
        assert model_repo.get_all.call_args.kwargs["allowed_model_ids"] == ["m2"]

    @patch.object(av, "model_availability_repo")
    def test_intersection_empty_yields_nothing_without_querying(self, repo):
        repo.any_indexed.return_value = True
        repo.model_ids_for_backends.return_value = ["m1", "m2"]
        registry = registry_with("comfy_a")

        model_repo = Mock()

        result = av.models_for_engine(
            "comfyui", registry, model_repository=model_repo, user_allowed_model_ids=["m9"],
        )

        assert result == []
        model_repo.get_all.assert_not_called()

    @patch.object(av, "model_availability_repo")
    def test_unindexed_engine_still_applies_user_scope(self, repo):
        """Even when availability is unknown (nobody indexed), the user's own
        model-access scope must still apply - it is not conditioned on indexing."""
        repo.any_indexed.return_value = False
        registry = registry_with("comfy_a")

        model_repo = Mock()
        model_repo.get_all.return_value = []

        av.models_for_engine(
            "comfyui", registry, model_repository=model_repo, user_allowed_model_ids=["m1"],
        )

        assert model_repo.get_all.call_args.kwargs["allowed_model_ids"] == ["m1"]
