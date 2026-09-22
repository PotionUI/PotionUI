from __future__ import annotations

import torch

from ..errors import NativeEngineUnsupportedError
from .base import NativeTextEncoder, _module_to, _module_unload
from .qwen3 import Qwen3Model
from .qwen3_vl_vision import IMAGE_PAD_TOKEN, Qwen3VLVisionTower, preprocess_qwen3_vl_image
from .qwen_vl_vision import qwen25vl_mrope_position_ids


class QwenImage21TextEncoder(NativeTextEncoder):
    role = "qwen3vl_8b"

    def __init__(
        self, module: Qwen3Model, tokenizer, variant: str = "qwen3vl_8b",
        device: str | torch.device = "cpu",
    ) -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)
        self._has_vision = hasattr(module.model, "visual")
        self._layer = module.cfg.num_hidden_layers - 1

    def to(self, device: str | torch.device) -> "QwenImage21TextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(
        self, texts: list[str], images: list[torch.Tensor] | None = None,
        grounding_px: int = 768, keep_vision: bool = False,
    ) -> dict[str, torch.Tensor]:
        if images:
            return self._encode_with_images(texts, images, grounding_px, keep_vision)
        ids, mask, prefix_len = self.tokenizer(texts, device=self._device)
        return self._encode_ids(ids, mask, prefix_len)

    def _encode_ids(self, ids, mask, prefix_len) -> dict[str, torch.Tensor]:
        stacked = self.module(ids, attention_mask=mask, layers_to_extract=(self._layer,), capture="output")
        context = stacked.squeeze(1)
        return {"context": context[:, prefix_len:], "attention_mask": mask[:, prefix_len:]}

    def _encode_with_images(
        self, texts: list[str], images: list[torch.Tensor], grounding_px: int, keep_vision: bool,
    ) -> dict[str, torch.Tensor]:
        if not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this qwen-image-2.1 text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time (load_text_encoder(..., vision=True))"
            )
        if len(texts) != 1:
            raise ValueError("image-conditioned encode() supports exactly one prompt per call")

        ids, mask, prefix_len = self.tokenizer.tokenize_with_images(
            texts[0], num_images=len(images), device=self._device,
        )
        pad_positions = (ids[0] == IMAGE_PAD_TOKEN).nonzero(as_tuple=True)[0].tolist()
        if len(pad_positions) != len(images):
            raise ValueError(
                f"template has {len(pad_positions)} <|image_pad|> slot(s) but got {len(images)} image(s)"
            )

        text_embeds = self.module.model.embed_tokens(ids).to(torch.float32)
        visual: Qwen3VLVisionTower = self.module.model.visual

        embed_pieces: list[torch.Tensor] = []
        mask_pieces: list[torch.Tensor] = []
        image_spans: list[tuple[int, int, torch.Tensor]] = []
        deepstack_pieces: list[list[torch.Tensor]] = [[] for _ in visual.deepstack_indexes]
        cursor = 0
        for pad_pos, image in zip(pad_positions, images):
            embed_pieces.append(text_embeds[:, cursor:pad_pos])
            mask_pieces.append(mask[:, cursor:pad_pos])

            patches, grid_thw = preprocess_qwen3_vl_image(
                image.to(self._device), grounding_px=grounding_px,
                patch_size=visual.patch_size,
                temporal_patch_size=visual.patch_embed.temporal_patch_size,
                merge_size=visual.spatial_merge_size,
            )
            merged, deepstack_feats = visual(patches.to(self._device, dtype=torch.float32), grid_thw)
            start = sum(p.shape[1] for p in embed_pieces)
            embed_pieces.append(merged.unsqueeze(0).to(text_embeds.dtype))
            mask_pieces.append(torch.ones((mask.shape[0], merged.shape[0]), dtype=mask.dtype, device=mask.device))
            image_spans.append((start, merged.shape[0], grid_thw))
            for j, feat in enumerate(deepstack_feats):
                deepstack_pieces[j].append(feat)

            cursor = pad_pos + 1
        embed_pieces.append(text_embeds[:, cursor:])
        mask_pieces.append(mask[:, cursor:])

        embeds = torch.cat(embed_pieces, dim=1)
        new_mask = torch.cat(mask_pieces, dim=1)
        position_ids = qwen25vl_mrope_position_ids(image_spans, embeds.shape[1], embeds.device)

        visual_pos_mask = new_mask.new_zeros(new_mask.shape, dtype=torch.bool)
        for start, size, _grid in image_spans:
            visual_pos_mask[:, start:start + size] = True
        deepstack_embeds = [torch.cat(pieces, dim=0) for pieces in deepstack_pieces]

        stacked = self.module(
            None, attention_mask=new_mask, layers_to_extract=(self._layer,), capture="output",
            inputs_embeds=embeds, position_ids=position_ids,
            deepstack_embeds=deepstack_embeds, visual_pos_mask=visual_pos_mask,
        )
        context = stacked.squeeze(1)

        keep = torch.ones(context.shape[1], dtype=torch.bool, device=context.device)
        keep[:prefix_len] = False
        slots: list[int] = []
        if not keep_vision:
            for start, size, _grid in image_spans:
                keep[start:start + size] = False
                slots.append(int(keep[:start].sum().item()))

        context = context[:, keep]
        new_mask = new_mask[:, keep.to(new_mask.device)]
        result = {"context": context, "attention_mask": new_mask}
        if slots:
            result["image_slots"] = slots
        return result
