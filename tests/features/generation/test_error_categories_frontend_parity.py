import re
from pathlib import Path

from src.features.generation.error_classification import ERROR_CATEGORIES

_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "routes" / "admin" / "components" / "settings" / "failureAlerts.ts"


def test_admin_alert_category_chips_match_backend_categories():
    values = re.findall(r"value:\s*'([a-z_]+)'", _TS.read_text(encoding="utf-8"))
    assert sorted(values) == sorted(ERROR_CATEGORIES)
