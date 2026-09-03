"""Unit test for GET /presets/families - the import UI's model-family
datalist source (see frontend/src/ImportWorkflowModal.svelte).
"""

import asyncio

import pytest

from backend import api


@pytest.mark.asyncio
async def test_lists_dirs_from_both_roots_deduped_and_sorted(tmp_path, monkeypatch):
    marketplace_root = tmp_path / "marketplace"
    local_root = tmp_path / "local"
    for name in ("SDXL", "Flux1"):
        (marketplace_root / name).mkdir(parents=True)
    for name in ("Flux1", "MyCustomFamily", ".hidden"):
        (local_root / name).mkdir(parents=True)
    # A stray file (not a directory) under a root must never surface as a family.
    local_root.mkdir(parents=True, exist_ok=True)
    (local_root / "not_a_family.txt").write_text("x")

    monkeypatch.setattr(api, "_MARKETPLACE_PRESETS_ROOT", marketplace_root)
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", local_root)

    result = await api.list_preset_families(current_user=None)

    assert result == {"families": ["Flux1", "MyCustomFamily", "SDXL"]}


@pytest.mark.asyncio
async def test_missing_roots_return_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_MARKETPLACE_PRESETS_ROOT", tmp_path / "does-not-exist-1")
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path / "does-not-exist-2")

    result = await api.list_preset_families(current_user=None)

    assert result == {"families": []}
