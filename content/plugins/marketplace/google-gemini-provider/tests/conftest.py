import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

for _key in list(sys.modules):
    if _key == "backend" or _key.startswith("backend."):
        del sys.modules[_key]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

import importlib

importlib.invalidate_caches()
import backend
from .fixtures import gemini
