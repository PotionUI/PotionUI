from __future__ import annotations

from typing import Any, Callable

import torch

QUANT_CHUNK_ELEMENTS = 1 << 26
QUANT_TILE_ROWS = 128


def activation_quant_chunk_rows(in_features: int) -> int:
    rows = QUANT_CHUNK_ELEMENTS // max(1, int(in_features))
    return max(QUANT_TILE_ROWS, rows // QUANT_TILE_ROWS * QUANT_TILE_ROWS)


def smaller_chunk_rows(rows: int, total_rows: int) -> int | None:
    half = min(rows, total_rows) // 2 // QUANT_TILE_ROWS * QUANT_TILE_ROWS
    return half if half >= QUANT_TILE_ROWS else None


class ChunkedGemmFailed(Exception):
    def __init__(self, reason: str, cause: BaseException, *, smaller_chunks_help: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause
        self.retry_smaller = smaller_chunks_help and isinstance(cause, torch.cuda.OutOfMemoryError)


def gemm_in_row_chunks(
    x2d: torch.Tensor,
    out_features: int,
    run_rows: Callable[[torch.Tensor, Any], torch.Tensor],
    whole_input_scale: Callable[[torch.Tensor], Any],
) -> torch.Tensor:
    total = x2d.shape[0]
    rows = activation_quant_chunk_rows(x2d.shape[1])
    scale: Any = None
    while True:
        try:
            if rows >= total:
                return run_rows(x2d, None)
            if scale is None:
                try:
                    scale = whole_input_scale(x2d)
                except Exception as exc:
                    raise ChunkedGemmFailed(
                        f"activation scale failed ({type(exc).__name__})", exc, smaller_chunks_help=False,
                    ) from exc
            try:
                out = torch.empty((total, out_features), dtype=x2d.dtype, device=x2d.device)
            except torch.cuda.OutOfMemoryError as exc:
                raise ChunkedGemmFailed(
                    "output allocation failed (OutOfMemoryError)", exc, smaller_chunks_help=False,
                ) from exc
            for start in range(0, total, rows):
                out[start:start + rows] = run_rows(x2d[start:start + rows], scale)
            return out
        except ChunkedGemmFailed as failed:
            smaller = smaller_chunk_rows(rows, total) if failed.retry_smaller else None
            if smaller is None:
                raise
            rows = smaller
