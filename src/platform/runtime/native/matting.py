"""BiRefNet background matting: lazy-load a checkpoint, run it, release it.

Vendored architecture: `vendor/BiRefNet/birefnet/` (MIT; see
`vendor/NOTICE.md`). Lifted out of `content/plugins/marketplace/trellis2`'s own copy
of this module (`generator_trellis2/main.py`'s `BiRefNetBackgroundRemover`/
`load_matting_model`) so a second plugin does not have to vendor the same
~1000-line architecture a second time - see `src/plugin_api/media.py` for the
sanctioned surface this backs (`BackgroundMattingModel`).

This module raises plain `ValueError` on a bad/missing checkpoint rather than
a pipe- or HTTP-specific exception type: it is a platform-layer module, and a
caller (a pipe, an API route) translates that into whatever error shape its
own layer uses (`GenerationExecutionError`, an HTTP 422, ...).
"""

from pathlib import Path
from typing import Any, Dict, Union

from PIL import Image

#: Prefixes a saved BiRefNet state dict can carry ahead of the model's own key
#: space: `module.` from `nn.DataParallel`/DDP, `_orig_mod.` from
#: `torch.compile`. BiRefNet trains under both, so its released checkpoints
#: carry them - in either order, and more than one deep. Upstream strips
#: exactly this set in `utils.check_state_dict`.
#:
#: Nothing else needs remapping: `ZhengPeng7/BiRefNet` and `briaai/RMBG-2.0`
#: publish the SAME 754 keys at the same shapes (verified against both
#: safetensors headers), differing only in dtype - fp16 and fp32 respectively.
#: None of those keys begins with a prefix below, `squeeze_module.` included.
MATTING_WRAPPER_PREFIXES = ("module.", "_orig_mod.")


def remap_matting_key(key: str) -> str:
    """One checkpoint key in the vendored BiRefNet's own key space."""
    stripped = True
    while stripped:
        stripped = False
        for prefix in MATTING_WRAPPER_PREFIXES:
            if key.startswith(prefix):
                key = key[len(prefix):]
                stripped = True
    return key


def remap_matting_state_dict(state: Dict[str, Any]) -> Dict[str, Any]:
    """A BiRefNet state dict keyed the way the vendored model names its weights."""
    return {remap_matting_key(key): value for key, value in state.items()}


def load_matting_model(path: Union[str, Path]):
    """The vendored BiRefNet, built from a checkpoint on disk.

    `strict=True` is not usable - these checkpoints legitimately omit
    non-persistent buffers - so missing *parameters* are checked explicitly
    instead. A BiRefNet built here has 754 tensors and both supported
    checkpoints (`ZhengPeng7/BiRefNet`, `briaai/RMBG-2.0`) carry all 754, so
    anything unfilled means the wrong file was picked, not a tolerable
    omission.
    """
    from safetensors.torch import load_file
    from vendor.BiRefNet.birefnet import BiRefNet

    path = str(path)
    if not path or not Path(path).exists():
        raise ValueError(f"Matting checkpoint not found: {path!r}")

    model = BiRefNet()
    result = model.load_state_dict(remap_matting_state_dict(load_file(path)), strict=False)

    expected = {name for name, _ in model.named_parameters()}
    unfilled = sorted(expected.intersection(result.missing_keys))
    if unfilled:
        raise ValueError(
            f"The matting model is missing {len(unfilled)} weights. "
            f"First few: {unfilled[:5]}. Expected a BiRefNet checkpoint over a "
            f"swin_v1_l backbone, 754 tensors - BiRefNet-general or RMBG-2.0."
        )

    return model.eval()


class BackgroundMattingModel:
    """BiRefNet-based background matting, with the load/use/release lifecycle
    every caller needs: `.to(device)`/`.cpu()`/`.cuda()` move the underlying
    model, `__call__` runs it. Never resident by default - a caller is
    expected to release it (`.cpu()`, then drop the reference) after use, not
    hold it across requests.

    `__init__` takes an already-built torch module directly (so tests can
    inject a fake with the same call shape, no real weights needed);
    `from_checkpoint` is the real-weights path.
    """

    #: BiRefNet is trained at 1024x1024 and its swin window size divides it.
    INPUT_SIZE = 1024

    def __init__(self, model):
        from torchvision import transforms

        self.model = model
        # Tracked rather than read back off a parameter: these three methods
        # are the only thing that moves the model, and callers invoke them
        # either side of every use.
        self.device = "cpu"
        self.transform = transforms.Compose([
            transforms.Resize((self.INPUT_SIZE, self.INPUT_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    @classmethod
    def from_checkpoint(cls, path: Union[str, Path]) -> "BackgroundMattingModel":
        """Load + wrap in one step. Raises `ValueError` naming the problem
        (missing file, incomplete/wrong checkpoint) rather than letting a
        safetensors/torch exception surface raw."""
        return cls(load_matting_model(path))

    def to(self, device):
        self.model.to(device)
        self.device = device
        return self

    def cpu(self):
        self.model.cpu()
        self.device = "cpu"
        return self

    def cuda(self):
        self.model.cuda()
        self.device = "cuda"
        return self

    def __call__(self, image: Image.Image) -> Image.Image:
        """`image` -> RGBA at the SAME size, alpha = the matted subject."""
        import torch

        rgb = image.convert("RGB")

        with torch.no_grad():
            batch = self.transform(rgb).unsqueeze(0).to(device=self.device, dtype=torch.float32)
            mask = self.model(batch)[-1].sigmoid().float().cpu()

        alpha = Image.fromarray(
            (mask[0, 0].numpy() * 255).round().astype("uint8"), mode="L"
        ).resize(rgb.size, Image.LANCZOS)

        output = rgb.copy()
        output.putalpha(alpha)
        return output
