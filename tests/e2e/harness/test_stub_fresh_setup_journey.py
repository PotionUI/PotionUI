import hashlib
import importlib.util
import sys
from pathlib import Path

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
JOURNEY = HARNESS_DIR.parent / "journeys" / "fresh_setup_stub_engine.py"


def _journey():
    if str(HARNESS_DIR) not in sys.path:
        sys.path.insert(0, str(HARNESS_DIR))
    spec = importlib.util.spec_from_file_location("journey_fresh_setup_stub_engine", JOURNEY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_journey_changes_only_the_size_and_checksum_of_the_shipped_recipe():
    journey = _journey()
    payload = b"fake-checkpoint"
    shipped = yaml.safe_load(journey.SOURCE_RECIPE.read_text(encoding="utf-8"))

    built = journey.build_recipe(payload)

    assert [s["key"] for s in built["steps"]] == [s["key"] for s in shipped["steps"]]
    assert built["steps"] == shipped["steps"]
    artifact = built["artifacts"][0]
    assert artifact["size_bytes"] == len(payload)
    assert artifact["checksum"]["value"] == hashlib.sha256(payload).hexdigest()
    untouched = {k: v for k, v in artifact.items() if k not in ("size_bytes", "checksum")}
    original = {k: v for k, v in shipped["artifacts"][0].items() if k not in ("size_bytes", "checksum")}
    assert untouched == original
    assert {k: v for k, v in built.items() if k != "artifacts"} == {k: v for k, v in shipped.items() if k != "artifacts"}


def test_the_download_path_matches_what_the_huggingface_source_asks_for():
    journey = _journey()
    artifact = journey.build_recipe(b"x")["artifacts"][0]

    assert journey.resolve_path(artifact) == (
        "RunDiffusion/Juggernaut-XL-v9/resolve/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
    )


def test_the_journey_is_skipped_unless_it_is_asked_for(monkeypatch):
    journey = _journey()
    monkeypatch.delenv(journey.OPT_IN, raising=False)

    result = journey.run(None)

    assert result.status == "skip"
    assert journey.OPT_IN in result.evidence[0]
