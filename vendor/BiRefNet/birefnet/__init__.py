# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Inference-only subset; see each module's header for what upstream code it
# came from and what was removed. Upstream has no package `__init__` - it runs
# from its repository root with `config` and `models` as top-level modules -
# so this file is local, and exists to make the tree importable by path.

from .birefnet import BiRefNet
from .config import Config

__all__ = ["BiRefNet", "Config"]
