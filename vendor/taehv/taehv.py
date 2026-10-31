# Derived from: https://github.com/madebyollin/taehv/blob/main/taehv.py
from collections import namedtuple

import torch
import torch.nn as nn
import torch.nn.functional as F

TWorkItem = namedtuple("TWorkItem", ("input_tensor", "block_index"))


def conv(n_in, n_out, **kwargs):
    return nn.Conv2d(n_in, n_out, 3, padding=1, **kwargs)


class Clamp(nn.Module):
    def forward(self, x):
        return torch.tanh(x / 3) * 3


class MemBlock(nn.Module):
    def __init__(self, n_in, n_out):
        super().__init__()
        self.conv = nn.Sequential(conv(n_in * 2, n_out), nn.ReLU(inplace=True), conv(n_out, n_out), nn.ReLU(inplace=True), conv(n_out, n_out))
        self.skip = nn.Conv2d(n_in, n_out, 1, bias=False) if n_in != n_out else nn.Identity()
        self.act = nn.ReLU(inplace=True)

    def forward(self, x, past):
        return self.act(self.conv(torch.cat([x, past], 1)) + self.skip(x))


class SuperMemBlock(nn.Module):
    def __init__(self, n_f):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(n_f * 2, n_f * 2, 7, padding=3, groups=n_f * 2, bias=False),
            nn.Conv2d(n_f * 2, n_f * 4, 1), nn.ReLU(inplace=True),
            nn.Conv2d(n_f * 4, n_f, 1, bias=False),
        )

    def forward(self, x, past):
        return self.conv(torch.cat([x, past], 1)) + x


class TPool(nn.Module):
    def __init__(self, n_f, stride):
        super().__init__()
        self.stride = stride
        self.conv = nn.Conv2d(n_f * stride, n_f, 1, bias=False)

    def forward(self, x):
        _NT, C, H, W = x.shape
        return self.conv(x.reshape(-1, self.stride * C, H, W))


class TGrow(nn.Module):
    def __init__(self, n_f, stride):
        super().__init__()
        self.stride = stride
        self.conv = nn.Conv2d(n_f, n_f * stride, 1, bias=False)

    def forward(self, x):
        _NT, C, H, W = x.shape
        x = self.conv(x)
        return x.reshape(-1, C, H, W)


def apply_model_with_memblocks_parallel(model, x):
    assert x.ndim == 5, f"TAEHV operates on NTCHW tensors, but got {x.ndim}-dim tensor"
    N, T, C, H, W = x.shape
    x = x.reshape(N * T, C, H, W)
    for b in model:
        if isinstance(b, (MemBlock, SuperMemBlock)):
            NT, C, H, W = x.shape
            T = NT // N
            _x = x.reshape(N, T, C, H, W)
            block_memory = F.pad(_x, (0, 0, 0, 0, 0, 0, 1, 0), value=0)[:, :T].reshape(x.shape)
            x = b(x, block_memory)
        else:
            x = b(x)
    NT, C, H, W = x.shape
    T = NT // N
    return x.view(N, T, C, H, W)


def apply_model_with_memblocks_sequential_single_step(model, memory, work_queue):
    while work_queue:
        xt, i = work_queue.pop(0)
        if i == len(model):
            return xt.unsqueeze(1)
        b = model[i]
        if isinstance(b, (MemBlock, SuperMemBlock)):
            if memory[i] is None:
                xt_new = b(xt, xt * 0)
            else:
                xt_new = b(xt, memory[i])
            memory[i] = xt
            work_queue.insert(0, TWorkItem(xt_new, i + 1))
        elif isinstance(b, TPool):
            if memory[i] is None:
                memory[i] = []
            memory[i].append(xt)
            if len(memory[i]) > b.stride:
                raise ValueError(f"TPool memory overflow: {len(memory[i])} items for stride {b.stride}")
            elif len(memory[i]) == b.stride:
                N, C, H, W = xt.shape
                xt = b(torch.cat(memory[i], 1).view(N * b.stride, C, H, W))
                memory[i] = []
                work_queue.insert(0, TWorkItem(xt, i + 1))
        elif isinstance(b, TGrow):
            xt = b(xt)
            NT, C, H, W = xt.shape
            for xt_next in reversed(xt.view(NT // b.stride, b.stride * C, H, W).chunk(b.stride, 1)):
                work_queue.insert(0, TWorkItem(xt_next, i + 1))
        else:
            xt = b(xt)
            work_queue.insert(0, TWorkItem(xt, i + 1))
    return None


def apply_model_with_memblocks_sequential(model, x):
    assert x.ndim == 5, f"TAEHV operates on NTCHW tensors, but got {x.ndim}-dim tensor"
    work_queue = [TWorkItem(xt, 0) for xt in x.unbind(1)]
    memory = [None] * len(model)
    out = []
    while work_queue:
        xt = apply_model_with_memblocks_sequential_single_step(model, memory, work_queue)
        if xt is not None:
            out.append(xt)
    return torch.cat(out, 1)


def apply_model_with_memblocks(model, x, parallel):
    if parallel:
        return apply_model_with_memblocks_parallel(model, x)
    return apply_model_with_memblocks_sequential(model, x)


class TAEHV(nn.Module):
    def __init__(self, arch_name, encoder_time_downscale=None, decoder_time_upscale=None, decoder_space_upscale=None, patch_size=None, latent_channels=None):
        super().__init__()
        self.arch_name = str(arch_name)
        if "taehv1_5" in self.arch_name: patch_size, latent_channels = patch_size or 2, latent_channels or 32
        if "taew2_2" in self.arch_name: patch_size, latent_channels = patch_size or 2, latent_channels or 48
        if "taeh3" in self.arch_name: patch_size, latent_channels = patch_size or 2, latent_channels or 24
        if "taeltx" in self.arch_name:
            patch_size, latent_channels = patch_size or 4, latent_channels or 128
            encoder_time_downscale, decoder_time_upscale = encoder_time_downscale or (True, True, True), decoder_time_upscale or (True, True, True)
        self.patch_size = patch_size or 1
        self.latent_channels = latent_channels or 16
        self.image_channels = 3
        encoder_time_downscale = encoder_time_downscale or (True, True, False)
        decoder_time_upscale = decoder_time_upscale or (False, True, True)
        decoder_space_upscale = decoder_space_upscale or (True, True, True)
        if len(decoder_time_upscale) == 2:
            decoder_time_upscale = (False, *decoder_time_upscale)
        self.encoder = nn.Sequential(
            conv(self.image_channels * self.patch_size ** 2, 64), nn.ReLU(inplace=True),
            TPool(64, 2 if encoder_time_downscale[0] else 1), conv(64, 64, stride=2, bias=False), MemBlock(64, 64), MemBlock(64, 64), MemBlock(64, 64),
            TPool(64, 2 if encoder_time_downscale[1] else 1), conv(64, 64, stride=2, bias=False), MemBlock(64, 64), MemBlock(64, 64), MemBlock(64, 64),
            TPool(64, 2 if encoder_time_downscale[2] else 1), conv(64, 64, stride=2, bias=False), MemBlock(64, 64), MemBlock(64, 64), MemBlock(64, 64),
            conv(64, self.latent_channels),
        )
        if "_super" in self.arch_name:
            n_f = [512, 256, 128, 64]
            self.decoder = nn.Sequential(
                nn.Conv2d(self.latent_channels, n_f[0], 1, bias=False),
                SuperMemBlock(n_f[0]), SuperMemBlock(n_f[0]), SuperMemBlock(n_f[0]), conv(n_f[0], n_f[1] * (2 if decoder_space_upscale[0] else 1) ** 2), nn.ReLU(inplace=True), nn.PixelShuffle(2 if decoder_space_upscale[0] else 1), TGrow(n_f[1], 2 if decoder_time_upscale[0] else 1),
                SuperMemBlock(n_f[1]), SuperMemBlock(n_f[1]), SuperMemBlock(n_f[1]), conv(n_f[1], n_f[2] * (2 if decoder_space_upscale[1] else 1) ** 2), nn.ReLU(inplace=True), nn.PixelShuffle(2 if decoder_space_upscale[1] else 1), TGrow(n_f[2], 2 if decoder_time_upscale[1] else 1),
                SuperMemBlock(n_f[2]), SuperMemBlock(n_f[2]), SuperMemBlock(n_f[2]), conv(n_f[2], n_f[3] * (2 if decoder_space_upscale[2] else 1) ** 2), nn.ReLU(inplace=True), nn.PixelShuffle(2 if decoder_space_upscale[2] else 1), TGrow(n_f[3], 2 if decoder_time_upscale[2] else 1),
                conv(n_f[3], self.image_channels * self.patch_size ** 2),
            )
        else:
            n_f = [256, 128, 64, 64]
            self.decoder = nn.Sequential(
                Clamp(), conv(self.latent_channels, n_f[0]), nn.ReLU(inplace=True),
                MemBlock(n_f[0], n_f[0]), MemBlock(n_f[0], n_f[0]), MemBlock(n_f[0], n_f[0]), nn.Upsample(scale_factor=2 if decoder_space_upscale[0] else 1), TGrow(n_f[0], 2 if decoder_time_upscale[0] else 1), conv(n_f[0], n_f[1], bias=False),
                MemBlock(n_f[1], n_f[1]), MemBlock(n_f[1], n_f[1]), MemBlock(n_f[1], n_f[1]), nn.Upsample(scale_factor=2 if decoder_space_upscale[1] else 1), TGrow(n_f[1], 2 if decoder_time_upscale[1] else 1), conv(n_f[1], n_f[2], bias=False),
                MemBlock(n_f[2], n_f[2]), MemBlock(n_f[2], n_f[2]), MemBlock(n_f[2], n_f[2]), nn.Upsample(scale_factor=2 if decoder_space_upscale[2] else 1), TGrow(n_f[2], 2 if decoder_time_upscale[2] else 1), conv(n_f[2], n_f[3], bias=False),
                nn.ReLU(inplace=True), conv(n_f[3], self.image_channels * self.patch_size ** 2),
            )
        self.t_downscale = 2 ** sum(t.stride == 2 for t in self.encoder if isinstance(t, TPool))
        self.t_upscale = 2 ** sum(t.stride == 2 for t in self.decoder if isinstance(t, TGrow))
        self.frames_to_trim = self.t_upscale - 1

    def load_weights(self, sd):
        self.load_state_dict(self.patch_tgrow_layers(dict(sd)))

    def patch_tgrow_layers(self, sd):
        new_sd = self.state_dict()
        for i, layer in enumerate(self.decoder):
            if isinstance(layer, TGrow):
                key = f"decoder.{i}.conv.weight"
                if sd[key].shape[0] > new_sd[key].shape[0]:
                    sd[key] = sd[key][-new_sd[key].shape[0]:]
        return sd

    def preprocess_input_frames(self, x):
        if self.patch_size > 1: x = F.pixel_unshuffle(x, self.patch_size)
        return x

    def _encode_h3_video(self, x, parallel):
        batch = x.shape[0]
        x = torch.cat([x, x[:, -1:].expand(-1, -x.shape[1] % 17, -1, -1, -1)], dim=1)
        x = F.pad(x.reshape(batch, -1, 17, *x.shape[2:]), (0, 0, 0, 0, 0, 0, 3, 0))
        x = self.preprocess_input_frames(x)
        if parallel:
            x = apply_model_with_memblocks(self.encoder, x.flatten(0, 1), True)
            x = x.reshape(batch, -1, *x.shape[2:])
        else:
            x = torch.cat([apply_model_with_memblocks(self.encoder, chunk, False) for chunk in x.unbind(1)], dim=1)
        return x[:, :-3]

    def _decode_h3_video(self, x, parallel):
        x = apply_model_with_memblocks(self.decoder, x, parallel)
        chunk_frames = 5 * self.t_upscale
        x = F.pad(x, (0, 0, 0, 0, 0, 0, 0, -x.shape[1] % chunk_frames))
        x = x.unflatten(1, (-1, chunk_frames))[:, :, self.frames_to_trim:].flatten(1, 2)
        return self.postprocess_output_frames(x[:, :-3 * self.t_upscale])

    def encode_video(self, x, parallel=True):
        if "taeh3" in self.arch_name:
            return self._encode_h3_video(x, parallel)
        x = self.preprocess_input_frames(x)
        if x.shape[1] % self.t_downscale != 0:
            n_pad = self.t_downscale - x.shape[1] % self.t_downscale
            padding = x[:, -1:].repeat_interleave(n_pad, dim=1)
            x = torch.cat([x, padding], 1)
        return apply_model_with_memblocks(self.encoder, x, parallel)

    def postprocess_output_frames(self, x):
        if self.patch_size > 1: x = F.pixel_shuffle(x, self.patch_size)
        return x.clamp_(0, 1)

    def decode_video(self, x, parallel=True):
        if "taeh3" in self.arch_name:
            return self._decode_h3_video(x, parallel)
        x = apply_model_with_memblocks(self.decoder, x, parallel)
        x = self.postprocess_output_frames(x)
        return x[:, self.frames_to_trim:]
