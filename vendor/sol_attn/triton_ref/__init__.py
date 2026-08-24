# Vendored from ComfyUI_sol-attn_Blackwell —
# https://github.com/KingGore/ComfyUI_sol-attn_Blackwell
# Source file: sol_attn/triton_ref/__init__.py at commit
# a8a9584e1ed700f2ce3b7569048cab0071bbf58a.
# License: Apache-2.0 (see ../LICENSE). Local modifications: the eager
# `from .fwd import sol_attn` re-export is dropped — importing `fwd` imports
# `triton`, which this tree treats as optional (see ../__init__.py).

"""Triton reference implementation of the Sol-Attn forward kernel."""
