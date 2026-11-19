import mimetypes

import pytest

from src.platform.http.mime_types import WEB_ASSET_MIME_TYPES, ensure_web_mime_types


@pytest.fixture(autouse=True)
def restore_mimetypes():
    mimetypes.init()
    before = dict(mimetypes.types_map)
    yield
    mimetypes.types_map.clear()
    mimetypes.types_map.update(before)


def test_registry_poisoned_js_is_overridden():
    mimetypes.init()
    mimetypes.add_type("text/plain", ".js")
    assert mimetypes.guess_type("x.js")[0] == "text/plain"

    ensure_web_mime_types()

    assert mimetypes.guess_type("x.js")[0] == "text/javascript"


@pytest.mark.parametrize("extension,expected", sorted(WEB_ASSET_MIME_TYPES.items()))
def test_every_web_type_wins_over_poisoned_mapping(extension, expected):
    mimetypes.init()
    mimetypes.add_type("text/plain", extension)

    ensure_web_mime_types()

    assert mimetypes.guess_type(f"x{extension}")[0] == expected
