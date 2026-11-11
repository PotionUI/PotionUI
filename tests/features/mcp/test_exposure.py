from types import SimpleNamespace

from src.features.mcp import exposure


def test_counts_are_per_owner_and_the_total_is_the_catalogue_size(monkeypatch):
    monkeypatch.setattr(exposure, "_candidate_tools", lambda c: [1, 2, 3, 4])
    monkeypatch.setattr(exposure, "_default_llm_id", lambda c: "llm")
    allowed = {"a": ["x"], "b": ["x", "y", "z"]}
    lookups = []

    def resolve(c, user_id, llm):
        lookups.append(user_id)
        return SimpleNamespace(allowed_names=lambda: allowed[user_id])

    monkeypatch.setattr(exposure, "_resolve_scope", resolve)

    assert exposure.tool_counts_by_owner(object(), ["a", "b", "a"]) == {"a": (1, 4), "b": (3, 4)}
    assert lookups == ["a", "b"]


def test_no_owners_means_no_lookups(monkeypatch):
    monkeypatch.setattr(exposure, "_candidate_tools", lambda c: [1])
    monkeypatch.setattr(exposure, "_default_llm_id", lambda c: None)
    monkeypatch.setattr(exposure, "_resolve_scope", lambda *a: (_ for _ in ()).throw(AssertionError("looked up")))

    assert exposure.tool_counts_by_owner(object(), []) == {}
