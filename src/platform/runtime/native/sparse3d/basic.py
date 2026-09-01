# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/basic.py
"""Pure-torch sparse-tensor container.

Ports only the ``CONV='none'`` code path of upstream's ``SparseTensor``
(``data`` is a plain ``{'feats', 'coords'}`` dict — no torchsparse/spconv
backend, no ``DEBUG`` assert block). Upstream's ``VarLenTensor`` base class is
not ported: nothing here needs a non-spatial variable-length container, so
``SparseTensor`` is flattened to a single concrete class instead of a subclass
of an unported base.
"""

from __future__ import annotations

from fractions import Fraction
from typing import List, Optional, Tuple, Union

import torch

__all__ = ["SparseTensor", "sparse_cat", "sparse_unbind"]

_DEFAULT_SCALE: Tuple[Fraction, Fraction, Fraction] = (Fraction(1, 1), Fraction(1, 1), Fraction(1, 1))


class SparseTensor:
    """Sparse voxel tensor: ``feats`` [N, ...], ``coords`` [N, 1+dim] (batch
    index in column 0). Rows belonging to the same batch element must be
    contiguous — every op here (layout, unbind, cat) assumes it."""

    def __init__(
        self,
        feats: torch.Tensor,
        coords: torch.Tensor,
        shape: Optional[torch.Size] = None,
        *,
        scale: Optional[Tuple[Fraction, Fraction, Fraction]] = None,
        spatial_cache: Optional[dict] = None,
    ):
        assert feats.shape[0] == coords.shape[0], (
            f"feats/coords row mismatch: {feats.shape[0]} vs {coords.shape[0]}"
        )
        self.feats = feats
        self.coords = coords
        self._shape = shape
        self._scale = scale if scale is not None else _DEFAULT_SCALE
        self._spatial_cache = spatial_cache if spatial_cache is not None else {}

    @staticmethod
    def from_tensor_list(feats_list: List[torch.Tensor], coords_list: List[torch.Tensor]) -> "SparseTensor":
        feats = torch.cat(feats_list, dim=0)
        coords = []
        for i, coord in enumerate(coords_list):
            coord = torch.cat([torch.full_like(coord[:, :1], i), coord[:, 1:]], dim=1)
            coords.append(coord)
        coords = torch.cat(coords, dim=0)
        return SparseTensor(feats, coords)

    def to_tensor_list(self) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        feats_list, coords_list = [], []
        for s in self.layout:
            feats_list.append(self.feats[s])
            coords_list.append(self.coords[s])
        return feats_list, coords_list

    def __len__(self) -> int:
        return len(self.layout)

    @staticmethod
    def _cal_shape(feats: torch.Tensor, coords: torch.Tensor) -> torch.Size:
        return torch.Size([coords[:, 0].max().item() + 1, *feats.shape[1:]])

    @staticmethod
    def _cal_layout(coords: torch.Tensor, batch_size: int) -> List[slice]:
        seq_len = torch.bincount(coords[:, 0], minlength=batch_size)
        offset = torch.cumsum(seq_len, dim=0)
        return [slice((offset[i] - seq_len[i]).item(), offset[i].item()) for i in range(batch_size)]

    @staticmethod
    def _cal_spatial_shape(coords: torch.Tensor) -> torch.Size:
        return torch.Size((coords[:, 1:].max(0)[0] + 1).tolist())

    @property
    def shape(self) -> torch.Size:
        if self._shape is None:
            self._shape = self._cal_shape(self.feats, self.coords)
        return self._shape

    @property
    def layout(self) -> List[slice]:
        layout = self.get_spatial_cache("layout")
        if layout is None:
            layout = self._cal_layout(self.coords, self.shape[0])
            self.register_spatial_cache("layout", layout)
        return layout

    @property
    def spatial_shape(self) -> torch.Size:
        spatial_shape = self.get_spatial_cache("shape")
        if spatial_shape is None:
            spatial_shape = self._cal_spatial_shape(self.coords)
            self.register_spatial_cache("shape", spatial_shape)
        return spatial_shape

    @property
    def dtype(self):
        return self.feats.dtype

    @property
    def device(self):
        return self.feats.device

    @property
    def seqlen(self) -> torch.LongTensor:
        seqlen = self.get_spatial_cache("seqlen")
        if seqlen is None:
            seqlen = torch.tensor([l.stop - l.start for l in self.layout], dtype=torch.long, device=self.device)
            self.register_spatial_cache("seqlen", seqlen)
        return seqlen

    @property
    def cum_seqlen(self) -> torch.LongTensor:
        cum_seqlen = self.get_spatial_cache("cum_seqlen")
        if cum_seqlen is None:
            cum_seqlen = torch.cat([
                torch.zeros(1, dtype=torch.long, device=self.device),
                self.seqlen.cumsum(dim=0),
            ], dim=0)
            self.register_spatial_cache("cum_seqlen", cum_seqlen)
        return cum_seqlen

    @property
    def _batch_broadcast_map(self) -> torch.LongTensor:
        m = self.get_spatial_cache("batch_broadcast_map")
        if m is None:
            m = torch.repeat_interleave(torch.arange(len(self.layout), device=self.device), self.seqlen)
            self.register_spatial_cache("batch_broadcast_map", m)
        return m

    def to(self, *args, **kwargs) -> "SparseTensor":
        device = None
        dtype = None
        if len(args) == 2:
            device, dtype = args
        elif len(args) == 1:
            if isinstance(args[0], torch.dtype):
                dtype = args[0]
            else:
                device = args[0]
        if "dtype" in kwargs:
            assert dtype is None, "to() received multiple values for argument 'dtype'"
            dtype = kwargs["dtype"]
        if "device" in kwargs:
            assert device is None, "to() received multiple values for argument 'device'"
            device = kwargs["device"]
        non_blocking = kwargs.get("non_blocking", False)
        copy = kwargs.get("copy", False)

        new_feats = self.feats.to(device=device, dtype=dtype, non_blocking=non_blocking, copy=copy)
        new_coords = self.coords.to(device=device, non_blocking=non_blocking, copy=copy)
        return self.replace(new_feats, new_coords)

    def type(self, dtype) -> "SparseTensor":
        return self.replace(self.feats.type(dtype))

    def cpu(self) -> "SparseTensor":
        return self.replace(self.feats.cpu(), self.coords.cpu())

    def cuda(self) -> "SparseTensor":
        return self.replace(self.feats.cuda(), self.coords.cuda())

    def half(self) -> "SparseTensor":
        return self.replace(self.feats.half())

    def float(self) -> "SparseTensor":
        return self.replace(self.feats.float())

    def detach(self) -> "SparseTensor":
        return self.replace(self.feats.detach(), self.coords.detach())

    def reshape(self, *shape) -> "SparseTensor":
        return self.replace(self.feats.reshape(self.feats.shape[0], *shape))

    def unbind(self, dim: int) -> List["SparseTensor"]:
        return sparse_unbind(self, dim)

    def replace(self, feats: torch.Tensor, coords: Optional[torch.Tensor] = None) -> "SparseTensor":
        new_coords = self.coords if coords is None else coords
        new_shape = torch.Size([self._shape[0]] + list(feats.shape[1:])) if self._shape is not None else None
        return SparseTensor(feats, new_coords, shape=new_shape, scale=self._scale, spatial_cache=self._spatial_cache)

    def to_dense(self) -> torch.Tensor:
        spatial_shape = self.spatial_shape
        ret = torch.zeros(*self.shape, *spatial_shape, dtype=self.dtype, device=self.device)
        idx = [self.coords[:, 0], slice(None)] + list(self.coords[:, 1:].unbind(1))
        ret[tuple(idx)] = self.feats
        return ret

    def _merge_sparse_cache(self, other: "SparseTensor") -> dict:
        new_cache = {}
        for k in set(list(self._spatial_cache.keys()) + list(other._spatial_cache.keys())):
            if k in self._spatial_cache:
                new_cache[k] = self._spatial_cache[k]
            if k in other._spatial_cache:
                if k not in new_cache:
                    new_cache[k] = other._spatial_cache[k]
                else:
                    new_cache[k].update(other._spatial_cache[k])
        return new_cache

    def _elemwise(self, other: Union[torch.Tensor, "SparseTensor", float], op) -> "SparseTensor":
        if isinstance(other, torch.Tensor):
            try:
                broadcast = torch.broadcast_to(other, self.shape)
                other = broadcast[self._batch_broadcast_map]
            except RuntimeError:
                pass
        if isinstance(other, SparseTensor):
            new_tensor = self.replace(op(self.feats, other.feats))
            new_tensor._spatial_cache = self._merge_sparse_cache(other)
            return new_tensor
        return self.replace(op(self.feats, other))

    def __neg__(self) -> "SparseTensor":
        return self.replace(-self.feats)

    def __add__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.add)

    def __radd__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.add)

    def __sub__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.sub)

    def __rsub__(self, other) -> "SparseTensor":
        return self._elemwise(other, lambda x, y: torch.sub(y, x))

    def __mul__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.mul)

    def __rmul__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.mul)

    def __truediv__(self, other) -> "SparseTensor":
        return self._elemwise(other, torch.div)

    def __rtruediv__(self, other) -> "SparseTensor":
        return self._elemwise(other, lambda x, y: torch.div(y, x))

    def __getitem__(self, idx) -> "SparseTensor":
        if isinstance(idx, int):
            idx = [idx]
        elif isinstance(idx, slice):
            idx = range(*idx.indices(self.shape[0]))
        elif isinstance(idx, list):
            assert all(isinstance(i, int) for i in idx), f"Only integer indices are supported: {idx}"
        elif isinstance(idx, torch.Tensor):
            if idx.dtype == torch.bool:
                assert idx.shape == (self.shape[0],), f"Invalid index shape: {idx.shape}"
                idx = idx.nonzero().squeeze(1)
            elif idx.dtype in (torch.int32, torch.int64):
                assert idx.dim() == 1, f"Invalid index shape: {idx.shape}"
            else:
                raise ValueError(f"Unknown index dtype: {idx.dtype}")
        else:
            raise ValueError(f"Unknown index type: {type(idx)}")

        new_coords, new_feats, new_layout = [], [], []
        start = 0
        for new_idx, old_idx in enumerate(idx):
            coord = self.coords[self.layout[old_idx]].clone()
            coord[:, 0] = new_idx
            new_coords.append(coord)
            new_feats.append(self.feats[self.layout[old_idx]])
            new_layout.append(slice(start, start + len(coord)))
            start += len(coord)
        new_coords = torch.cat(new_coords, dim=0).contiguous()
        new_feats = torch.cat(new_feats, dim=0).contiguous()
        new_shape = torch.Size([len(new_layout)] + list(self.shape[1:]))
        out = SparseTensor(new_feats, new_coords, shape=new_shape)
        out.register_spatial_cache("layout", new_layout)
        return out

    def clear_spatial_cache(self) -> None:
        self._spatial_cache = {}

    def register_spatial_cache(self, key, value) -> None:
        scale_key = str(self._scale)
        self._spatial_cache.setdefault(scale_key, {})[key] = value

    def get_spatial_cache(self, key=None):
        scale_key = str(self._scale)
        cur_scale_cache = self._spatial_cache.get(scale_key, {})
        if key is None:
            return cur_scale_cache
        return cur_scale_cache.get(key, None)

    def __repr__(self) -> str:
        return f"SparseTensor(shape={self.shape}, dtype={self.dtype}, device={self.device})"


def sparse_cat(inputs: List[SparseTensor], dim: int = 0) -> SparseTensor:
    if dim == 0:
        start = 0
        coords = []
        for t in inputs:
            c = t.coords.clone()
            c[:, 0] += start
            coords.append(c)
            start += t.shape[0]
        coords = torch.cat(coords, dim=0)
        feats = torch.cat([t.feats for t in inputs], dim=0)
        return SparseTensor(feats=feats, coords=coords)
    feats = torch.cat([t.feats for t in inputs], dim=dim)
    return inputs[0].replace(feats)


def sparse_unbind(input: SparseTensor, dim: int) -> List[SparseTensor]:
    if dim == 0:
        return [input[i] for i in range(input.shape[0])]
    feats = input.feats.unbind(dim)
    return [input.replace(f) for f in feats]
