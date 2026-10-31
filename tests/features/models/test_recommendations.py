from types import SimpleNamespace

from src.features.models.recommendations import (
    any_installed,
    find_installed_model,
    format_size,
    lint_recommendations,
    parse_recommendation,
    parse_ref,
    serialize_recommendation,
    size_text_agrees,
)

SHA = "a" * 64


def test_parse_ref_conventions():
    assert parse_ref('{"repo": "org/x", "file": "d/f.safetensors"}') == ("org/x", "main@d/f.safetensors")
    assert parse_ref('{"model_id": "12", "version_id": "34"}') == ("12", "34")
    assert parse_ref('{"provider_model_id": "a", "provider_version_id": "b"}') == ("a", "b")
    assert parse_ref("plain-id") == ("plain-id", None)
    assert parse_ref(None) == (None, None)


def test_parse_recommendation_reads_new_keys_and_ignores_bad_values():
    rec = parse_recommendation(
        {"name": "x.safetensors", "provider": "huggingface", "ref": "r", "size_bytes": 10, "precision": "int8",
         "recommended_for": [{"generations": ["ada"]}, "junk"], "default": True, "gated": True, "license_url": "https://l", "sha256": SHA.upper()}
    )
    assert (rec.size_bytes, rec.precision, rec.default, rec.gated, rec.license_url, rec.sha256) == (10, "int8", True, True, "https://l", SHA)
    assert rec.recommended_for[0].generations == ("ada",)
    assert len(rec.recommended_for) == 1

    loose = parse_recommendation({"name": "y.safetensors", "link": "https://h/y.safetensors", "size_bytes": -1, "precision": "fp4", "default": "yes"})
    assert (loose.size_bytes, loose.precision, loose.default) == (None, None, False)
    assert parse_recommendation({"size": "1 GB"}) is None


def test_old_recommendations_without_new_keys_still_parse():
    rec = parse_recommendation({"name": "x.safetensors", "provider": "civitai", "ref": '{"model_id": "1", "version_id": "2"}', "size": "2 GB"})
    assert rec.downloadable is True
    assert rec.known_size_bytes == 2 * 1024 ** 3
    assert rec.provider_hint() == {"source": "civitai", "model_id": "1", "version_id": "2"}


def test_size_text_agreement_accepts_binary_or_decimal_units():
    assert size_text_agrees("20.03 GB", 21504034224) is True
    assert size_text_agrees("21.5 GB", 21504034224) is True
    assert size_text_agrees("19 GB", 21504034224) is False
    assert size_text_agrees("about twenty", 5) is True


def test_serialize_derives_size_text_from_size_bytes():
    assert serialize_recommendation({"name": "x", "size_bytes": 1536 * 1024 ** 2})["size"] == "1.50 GB"
    assert serialize_recommendation({"name": "x", "size": "keep", "size_bytes": 5})["size"] == "keep"
    assert format_size(500) == "500 B"


def test_lint_reports_each_rule():
    errors, warnings = lint_recommendations(
        [
            {"name": "A.safetensors", "provider": "p", "ref": "r", "size_bytes": "big", "precision": "int8", "default": True},
            {"name": "a.safetensors", "provider": "p", "ref": "r", "size": "1 GB", "size_bytes": 10 * 1024 ** 3, "default": True},
            {"name": "c.pth", "link": "https://x/c.pth", "precision": "fp4", "recommended_for": [{"generations": ["pascal"]}], "gated": "y"},
        ],
        "f",
    )
    joined = " | ".join(errors)
    assert "'size_bytes' must be a non-negative integer" in joined
    assert "does not match 'size_bytes'" in joined
    assert "'precision' must be one of" in joined
    assert "unknown GPU generation 'pascal'" in joined
    assert "'gated' must be true or false" in joined
    assert "at most one recommendation may be marked 'default: true'" in joined
    assert "case-insensitive" in joined
    assert any("should carry a 'sha256'" in w for w in warnings)
    assert any("give every recommendation a 'precision'" in w for w in warnings)


def test_lint_is_quiet_for_a_clean_list_and_skips_templated_values():
    errors, warnings = lint_recommendations(
        [
            {"name": "{{ vars.file }}", "provider": "p", "ref": "r", "size": "{{ vars.size }}", "size_bytes": 3, "precision": "bf16"},
            {"name": "b.safetensors", "link": "https://x/b", "sha256": SHA, "precision": "int8", "size": "1 KB", "size_bytes": 1024},
        ],
        "f",
    )
    assert errors == []
    assert warnings == []


class _Repo:
    def __init__(self, identity=None, nocase=None, by_sha=None):
        self.identity = identity or {}
        self.nocase = nocase or {}
        self.by_sha = by_sha or {}

    def get_by_identity(self, model_type, filename):
        return self.identity.get((model_type, filename))

    def get_by_identity_nocase(self, model_type, filename):
        return self.nocase.get((model_type, filename.lower()))

    def get_by_sha256(self, sha256):
        return self.by_sha.get(sha256)


def test_find_installed_model_falls_back_to_case_insensitive_name_then_hash():
    row = SimpleNamespace(model_type="lora", filename="A.safetensors", is_available=True)
    assert find_installed_model(_Repo(nocase={("lora", "a.safetensors"): row}), "lora", "a.safetensors") is row
    assert find_installed_model(_Repo(by_sha={SHA: row}), "lora", "z.safetensors", sha256=SHA) is row
    assert find_installed_model(_Repo(), "lora", "z.safetensors", sha256=SHA) is None


def test_case_insensitive_lookup_is_never_reached_on_a_mock_repository():
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.get_by_identity.return_value = None
    repo.get_by_sha256.return_value = None
    assert find_installed_model(repo, "lora", "x.safetensors", sha256=SHA) is None


def test_any_installed_skips_unavailable_rows():
    gone = SimpleNamespace(model_type="lora", filename="a.safetensors", is_available=False)
    here = SimpleNamespace(model_type="lora", filename="b.safetensors", is_available=True)
    repo = _Repo(identity={("lora", "a.safetensors"): gone, ("lora", "b.safetensors"): here})
    files = [{"model_type": "lora", "filename": "a.safetensors"}, {"model_type": "lora", "filename": "b.safetensors"}]
    assert any_installed(repo, files) is here
    assert any_installed(repo, files[:1]) is None
