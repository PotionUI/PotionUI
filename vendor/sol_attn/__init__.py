# Vendored from ComfyUI_sol-attn_Blackwell —
# https://github.com/KingGore/ComfyUI_sol-attn_Blackwell
# Source: the repository's runtime subset at commit
# a8a9584e1ed700f2ce3b7569048cab0071bbf58a (2026-08-05).
# License: Apache-2.0 (see LICENSE in this directory).
# Local modifications: this package deliberately imports NOTHING at import
# time. Upstream's `sol_attn/__init__.py` re-exported `interface.sol_attn`,
# which pulls in `preprocess` and therefore `triton` — a hard dependency this
# tree cannot take, because the feature is opt-in and every machine without a
# usable triton must still import the native engine. Callers import the
# backend module they want (`vendor.sol_attn.flex` or
# `vendor.sol_attn.interface`) inside a try/except; see
# `src/platform/runtime/native/sol_attn.py`, the only consumer here.

"""Sol-Attn — sparse attention (routing by KV-block summaries).

Two backends, neither imported here:

* ``flex`` — routing in plain torch, execution through
  ``torch.nn.attention.flex_attention``. Needs only torch, supports the
  exact-KV sink, and is upstream's own SM120 (RTX 5090) path.
* ``interface`` — the original kernel path: CuTe DSL on SM90/SM100 (needs
  ``cutlass`` + ``cuda.bindings``, imported lazily inside the functions that
  use them) and ``triton_ref`` on SM120. Both need ``triton`` at import.
"""
