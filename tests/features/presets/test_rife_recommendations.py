from pathlib import Path

import yaml

from src.features.models.recommendations import Recommendation
from src.features.models.routes import ModelController
from src.platform.filesystem.model_types import extensions_for

INTERPOLATE_TAB = Path("content/presets/marketplace/VideoTools/modes/interpolate/tabs/generation.yml")


def _model_field():
    tab = yaml.safe_load(INTERPOLATE_TAB.read_text(encoding="utf-8"))
    return next(field for field in tab["fields"] if field["name"] == "model")


def test_the_interpolate_picker_recommends_rife_425_then_426_then_49():
    names = [rec["name"] for rec in _model_field()["configuration"]["recommendations"]]

    assert names == ["flownet_v4.25.pth", "flownet_v4.26.pth", "rife49.pth"]


def test_every_rife_recommendation_lands_under_a_name_the_vfi_indexer_reads():
    field = _model_field()
    extensions = extensions_for(field["configuration"]["model_type"])

    for rec in field["configuration"]["recommendations"]:
        assert ModelController._recommendation_filename(rec["name"]) == rec["name"]
        assert Recommendation(name=rec["name"], link=rec["link"]).filename == rec["name"]
        assert Path(rec["name"]).suffix in extensions


def test_the_425_and_426_links_point_at_the_published_pkl_files():
    links = {rec["name"]: rec["link"] for rec in _model_field()["configuration"]["recommendations"]}

    assert links["flownet_v4.25.pth"].endswith("/flownet_v4.25.pkl")
    assert links["flownet_v4.26.pth"].endswith("/flownet_v4.26.pkl")


def test_every_rife_recommendation_pins_its_sha256():
    for rec in _model_field()["configuration"]["recommendations"]:
        assert Recommendation(name=rec["name"], link=rec["link"], sha256=rec.get("sha256")).sha256, rec["name"]
