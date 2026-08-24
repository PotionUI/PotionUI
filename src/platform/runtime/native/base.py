"""Base contract for native arch modules + the load-integrity path.

Every vendored/written architecture (Flux, Qwen-Image, Wan, ...) subclasses
``NativeArchModule`` so the loader can treat them uniformly:

  * ``from_config(config, operations)`` — build the module with empty weights,
    wiring every layer through the given ops namespace (the fp8/cast seam).
  * ``post_load()`` — MANDATORY. Recompute anything that empty-weight
    construction + ``assign``-load leaves as garbage: RoPE ``inv_freq``, causal
    masks, any non-persistent computed buffer. (Qwen lesson: meta/empty
    construction leaves computed buffers looking valid but wrong.)

``load_into_module`` is the single correctness gate: strict-ish state-dict load
with per-ModelSpec key allowlists, then ``post_load``, then buffer sanity.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import torch
import torch.nn as nn

from .errors import NativeEngineLoadIntegrityError

logger = logging.getLogger(__name__)

# How many tensors to spot-check for NaN after load (cheap sanity, not a scan).
_NAN_SAMPLE = 20


class NativeArchModule(nn.Module, ABC):
    """Base class every native architecture module must extend.

    Uses ``ABCMeta`` (compatible with ``nn.Module``'s plain ``type`` metaclass)
    so subclasses that forget ``from_config`` / ``post_load`` fail loudly at
    construction rather than silently at load time.
    """

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "NativeArchModule":
        """Construct the module (empty weights) from a detected config dict.

        ``operations`` is an ops namespace from ``vendor.gpl.comfyui.ops`` exposing
        ``Linear``/``Conv2d``/``Conv3d``/``GroupNorm``/``LayerNorm``/``RMSNorm``/
        ``Embedding``. All parameterised layers must be built from it.
        """
        raise NotImplementedError

    @abstractmethod
    def post_load(self) -> None:
        """Recompute derived buffers after weights are assign-loaded.

        Called exactly once by ``load_into_module`` after a successful load.
        Must leave no computed buffer on the meta device and no garbage
        ``inv_freq``/mask values.
        """
        raise NotImplementedError


def _unlisted(keys: list[str], predicate) -> list[str]:
    return [k for k in keys if not predicate(k)]


def load_into_module(module: nn.Module, sd: dict[str, torch.Tensor], spec) -> None:
    """Load ``sd`` into ``module`` and hard-assert the key allowlists.

    ``spec`` is a ``ModelSpec`` (duck-typed: needs ``key_is_expected_missing`` /
    ``key_is_expected_unexpected``). Raises ``NativeEngineLoadIntegrityError``
    when the missing/unexpected key sets escape the allowlists.
    """
    # ``assign=True`` rebinds each checkpoint tensor into a fresh ``nn.Parameter``
    # carrying the EXISTING parameter's ``requires_grad``, and an integer tensor
    # cannot require grad — so an integer-coded quantised weight (int8_tensorwise's
    # int8 codes) fails the assign outright unless the flag is cleared first. This
    # engine only ever runs inference, so clearing it wholesale is both correct and
    # what keeps int8 codes at their native width instead of being copied into the
    # pre-allocated float parameter.
    module.requires_grad_(False)
    result = module.load_state_dict(sd, strict=False, assign=True)
    missing = list(result.missing_keys)
    unexpected = list(result.unexpected_keys)

    bad_missing = _unlisted(missing, spec.key_is_expected_missing)
    bad_unexpected = _unlisted(unexpected, spec.key_is_expected_unexpected)
    if bad_missing or bad_unexpected:
        raise NativeEngineLoadIntegrityError(
            f"state-dict load for {spec.family}/{spec.variant} violated key allowlist",
            missing=bad_missing,
            unexpected=bad_unexpected,
        )

    if isinstance(module, NativeArchModule):
        module.post_load()
    elif hasattr(module, "post_load"):
        module.post_load()

    _assert_no_meta(module, spec)
    _assert_no_nan(module, spec)


def _assert_no_meta(module: nn.Module, spec) -> None:
    for name, tensor in _iter_tensors(module):
        if tensor.device.type == "meta":
            raise NativeEngineLoadIntegrityError(
                f"{spec.family}/{spec.variant}: '{name}' left on meta device after load"
            )


def _assert_no_nan(module: nn.Module, spec) -> None:
    checked = 0
    for name, tensor in _iter_tensors(module):
        if checked >= _NAN_SAMPLE:
            break
        if tensor.numel() == 0 or not tensor.is_floating_point():
            continue
        # cheap: look at a single element, upcast to fp32 (fp8 has no isnan path).
        sample = tensor.flatten()[0].to(torch.float32)
        if torch.isnan(sample) or torch.isinf(sample):
            raise NativeEngineLoadIntegrityError(
                f"{spec.family}/{spec.variant}: NaN/Inf in '{name}' after load"
            )
        checked += 1


def _iter_tensors(module: nn.Module):
    for name, p in module.named_parameters():
        yield name, p.data
    for name, b in module.named_buffers():
        if b is not None:
            yield name, b
