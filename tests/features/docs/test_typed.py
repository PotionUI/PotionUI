"""Unit tests for typed doc frontmatter parsing + reverse index (docs/typed.py)."""

from __future__ import annotations

from src.features.docs.typed import parse_typed, refs_for


_TECH = {
    "type": "technique", "title": "FBCache", "category_group": "Performance",
    "status": "stable", "families": ["all-native"],
}
_MODEL = {
    "type": "model", "title": "Flux", "family_key": "flux", "modes": ["txt2img"],
    "spec": {"arch": "MMDiT", "latent": "16ch", "vae": "flux_ae", "te": "t5",
             "guidance": "embedded", "shift": 1.15, "engine": "native"},
}


def test_parse_valid_technique():
    dt, meta, errs = parse_typed(_TECH)
    assert dt == "technique" and errs == []
    assert meta["title"] == "FBCache" and meta["families"] == ["all-native"]


def test_parse_valid_model():
    dt, meta, errs = parse_typed(_MODEL)
    assert dt == "model" and errs == []
    assert meta["family_key"] == "flux" and meta["spec"]["engine"] == "native"


def test_parse_invalid_reports_errors_and_no_meta():
    dt, meta, errs = parse_typed({"type": "technique", "title": "x"})  # missing category_group/status
    assert dt == "technique" and meta is None
    assert any("category_group" in e for e in errs) and any("status" in e for e in errs)


def test_untyped_and_unknown_type_are_noops():
    assert parse_typed({"title": "plain"}) == (None, None, [])
    assert parse_typed({"type": "live"}) == ("live", None, [])


def test_extra_keys_tolerated():
    dt, meta, errs = parse_typed({**_TECH, "totally_unknown": 123})
    assert errs == [] and meta is not None and "totally_unknown" not in meta


# --- reverse index ------------------------------------------------------------


def _others(*items):
    # items: (doc_type, doc_id, slug, meta)
    return list(items)


def test_native_model_matches_family_and_all_native():
    tech_all = ("technique", "d/t/fb", "fb", {"title": "FB", "category_group": "Performance",
                                              "status": "stable", "families": ["all-native"]})
    tech_flux = ("technique", "d/t/fx", "fx", {"title": "FX", "category_group": "Quality",
                                               "status": "experimental", "families": ["flux"]})
    tech_wan = ("technique", "d/t/wan", "wan", {"title": "W", "category_group": "Memory",
                                                "status": "stable", "families": ["wan"]})
    refs = refs_for("model", _MODEL, _others(tech_all, tech_flux, tech_wan))
    slugs = {t["slug"] for t in refs["techniques"]}
    assert slugs == {"fb", "fx"}   # all-native + flux, NOT wan


def test_diffusers_model_excluded_from_all_native():
    sdxl = {"type": "model", "title": "SDXL", "family_key": "sdxl",
            "spec": {"arch": "unet", "latent": "4ch", "vae": "sdxl_vae", "te": "clip",
                     "guidance": "cfg", "shift": None, "engine": "diffusers"}}
    tech_all = ("technique", "d/t/fb", "fb", {"title": "FB", "category_group": "Performance",
                                              "status": "stable", "families": ["all-native"]})
    refs = refs_for("model", sdxl, _others(tech_all))
    assert refs["techniques"] == []   # diffusers engine doesn't match all-native


def test_technique_lists_matching_models():
    model_flux = ("model", "d/m/flux", "flux", {"family_key": "flux", "title": "Flux",
                                                "spec": {"engine": "native"}})
    model_sdxl = ("model", "d/m/sdxl", "sdxl", {"family_key": "sdxl", "title": "SDXL",
                                                "spec": {"engine": "diffusers"}})
    # all-native technique -> only native models
    refs = refs_for("technique", _TECH, _others(model_flux, model_sdxl))
    assert [m["family_key"] for m in refs["models"]] == ["flux"]
    # flux-specific technique -> flux only
    flux_tech = {"type": "technique", "title": "T", "category_group": "Quality",
                 "status": "stable", "families": ["flux"]}
    refs2 = refs_for("technique", flux_tech, _others(model_flux, model_sdxl))
    assert [m["family_key"] for m in refs2["models"]] == ["flux"]


def test_untyped_refs_empty():
    assert refs_for(None, None, []) == {}
