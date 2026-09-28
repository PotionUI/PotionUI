from backend.preset_import.emit import _history_param_entry
from backend.preset_import.schema import HistoryEntry


def test_lora_list_history_format_uses_the_strip_model_dir_filter():
    entry = HistoryEntry(field="loras", label="LoRAs", format="list")
    field, value = _history_param_entry(entry)

    assert field == "loras"
    assert "strip_model_dir" in value
    assert "models/loras/" not in value
    assert "'replace'" not in value
