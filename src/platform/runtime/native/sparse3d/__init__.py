"""Pure-torch sparse-tensor container + spatial ops (TRELLIS.2 port, Stage 1).

Every file here is a faithful port of a ``CONV='none'``/pure-torch slice of
microsoft/TRELLIS.2's ``trellis2/modules/sparse/`` (MIT) — see each file's
``# Derived from:`` header. No CUDA, no compiled deps.
"""

from .attention import sparse_scaled_dot_product_attention
from .basic import SparseTensor, sparse_cat, sparse_unbind
from .conv import SparseConv3d
from .linear import SparseLinear
from .rope import SparseRotaryPositionEmbedder
from .spatial import SparseChannel2Spatial, SparseDownsample, SparseSpatial2Channel, SparseUpsample

__all__ = [
    "SparseTensor",
    "sparse_cat",
    "sparse_unbind",
    "SparseDownsample",
    "SparseUpsample",
    "SparseChannel2Spatial",
    "SparseSpatial2Channel",
    "SparseRotaryPositionEmbedder",
    "SparseLinear",
    "SparseConv3d",
    "sparse_scaled_dot_product_attention",
]
