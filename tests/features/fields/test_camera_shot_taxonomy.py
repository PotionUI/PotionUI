"""Camera-shot taxonomy: phrase resolution and catalog shaping."""

from src.features.fields.camera_shot_taxonomy import (
    CATEGORY_KEYS,
    default_phrase,
    resolve_catalog,
    resolve_phrase,
    valid_shot_keys,
)


class TestResolvePhrase:
    def test_default_used_when_no_override(self):
        assert resolve_phrase("overhead", None) == "overhead shot, viewed from directly above"
        assert resolve_phrase("overhead", {}) == "overhead shot, viewed from directly above"

    def test_override_beats_default(self):
        assert resolve_phrase("overhead", {"overhead": "top-down bird's eye"}) == "top-down bird's eye"

    def test_blank_override_falls_back_to_default(self):
        assert resolve_phrase("overhead", {"overhead": "   "}) == default_phrase("overhead")

    def test_unknown_key_resolves_to_none(self):
        assert resolve_phrase("nope", None) is None
        assert resolve_phrase("nope", {"nope": "x"}) is None


class TestResolveCatalog:
    def test_full_catalog_has_all_categories(self):
        catalog = resolve_catalog()
        assert [c["key"] for c in catalog] == CATEGORY_KEYS

    def test_category_filter_and_order(self):
        catalog = resolve_catalog(categories=["orientation", "angle"])
        assert [c["key"] for c in catalog] == ["orientation", "angle"]

    def test_unknown_category_ignored(self):
        catalog = resolve_catalog(categories=["angle", "bogus"])
        assert [c["key"] for c in catalog] == ["angle"]

    def test_shot_carries_resolved_phrase_and_flag(self):
        catalog = resolve_catalog(vocabulary={"overhead": "top-down"}, categories=["angle"])
        overhead = next(s for s in catalog[0]["shots"] if s["key"] == "overhead")
        eye_level = next(s for s in catalog[0]["shots"] if s["key"] == "eye_level")
        assert overhead["phrase"] == "top-down"
        assert overhead["overridden"] is True
        assert eye_level["phrase"] == eye_level["default_phrase"]
        assert eye_level["overridden"] is False

    def test_override_equal_to_default_is_not_flagged(self):
        default = default_phrase("overhead")
        catalog = resolve_catalog(vocabulary={"overhead": default}, categories=["angle"])
        overhead = next(s for s in catalog[0]["shots"] if s["key"] == "overhead")
        assert overhead["overridden"] is False

    def test_shot_keys_are_unique_across_catalog(self):
        keys = [shot["key"] for cat in resolve_catalog() for shot in cat["shots"]]
        assert len(keys) == len(set(keys)) == len(valid_shot_keys())
