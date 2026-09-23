import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "a1111-metadata-export"

for _key in list(sys.modules):
    if _key == "backend" or _key.startswith("backend."):
        del sys.modules[_key]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

import importlib
importlib.invalidate_caches()
import backend


@pytest.fixture
def png_bytes_factory():
    def make(size=(4, 4), mode="RGB"):
        import io

        from PIL import Image

        image = Image.new(mode, size, color=(10, 20, 30, 255) if mode == "RGBA" else (10, 20, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    return make
