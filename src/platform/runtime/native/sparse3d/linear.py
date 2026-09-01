# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/linear.py
"""nn.Linear over a sparse tensor's feats, coords passed through unchanged."""

import torch.nn as nn

from .basic import SparseTensor

__all__ = ["SparseLinear"]


class SparseLinear(nn.Linear):
    def __init__(self, in_features: int, out_features: int, bias: bool = True):
        super().__init__(in_features, out_features, bias)

    def forward(self, input: SparseTensor) -> SparseTensor:
        return input.replace(super().forward(input.feats))
