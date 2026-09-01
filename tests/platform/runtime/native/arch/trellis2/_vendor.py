"""Import guard for the vendored microsoft/TRELLIS.2 checkout.

The checkout lives at ``content/plugins/local/trellis2/vendor/TRELLIS.2`` and is
gitignored (a local dev checkout, not part of the repo) — CI and any environment
without it must SKIP the tests that need it, never fail. Only tests that diff or
copy the vendored model's ``state_dict()`` need this; pure-native shape tests
must not call it, so they always run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

VENDOR_ROOT = Path(__file__).resolve().parents[6] / "content/plugins/local/trellis2/vendor/TRELLIS.2"


def import_vendor_models():
    """Return ``(SparseStructureFlowModel, SparseStructureDecoder)`` or skip."""
    if not VENDOR_ROOT.is_dir():
        pytest.skip(f"TRELLIS.2 vendor checkout not present at {VENDOR_ROOT}")

    # The dense (non-sparse) attention module defaults to flash_attn, which
    # this environment doesn't have installed. `config.BACKEND` is read fresh
    # on every call (not cached at import time), but a sibling test module
    # importing `trellis2.models.structured_latent_flow` first (it also pulls
    # in the dense cross-attn transformer) can beat $ATTN_BACKEND's read at
    # `trellis2.modules.attention.config` import time — so set the module
    # attribute directly, after import, rather than relying on env var
    # ordering.
    os.environ.setdefault("ATTN_BACKEND", "sdpa")

    vendor_str = str(VENDOR_ROOT)
    added = vendor_str not in sys.path
    if added:
        sys.path.insert(0, vendor_str)
    try:
        from trellis2.models.sparse_structure_flow import SparseStructureFlowModel
        from trellis2.models.sparse_structure_vae import SparseStructureDecoder
        import trellis2.modules.attention.config as _dense_attn_config
    finally:
        if added:
            sys.path.remove(vendor_str)

    _dense_attn_config.BACKEND = "sdpa"

    return SparseStructureFlowModel, SparseStructureDecoder
