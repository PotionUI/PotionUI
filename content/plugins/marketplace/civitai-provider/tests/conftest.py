import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))
