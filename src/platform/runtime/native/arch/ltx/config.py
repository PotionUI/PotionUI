"""``LTXAVConfig`` — construction config for the LTX-2/2.3 audio-video DiT.

LTX-2 is an audio-video joint transformer (ComfyUI ``AVTransformer3DModel`` /
``LTXAVModel``): parallel video and audio token streams, per-block AV cross
attention, and a text embeddings connector — all inside the DiT ``model.*``.
The plain video-only ``LTXVModel`` (``image_model == "ltxv"``, older LTX-Video
0.9) is the same minus the ``audio_*`` / ``av_ca_*`` / connector modules; the
local checkpoints are all the AV variant.

Dims (verified against the real 19b header):
  video inner  = num_attention_heads(32) * attention_head_dim(128) = 4096
  audio inner  = audio_num_attention_heads(32) * audio_attention_head_dim(64) = 2048
  connector    = connector_num_attention_heads(30) * connector_head_dim(128) = 3840

Most fields are shape-derived by the detector; ``theta`` / ``max_pos`` /
``causal_temporal_positioning`` / ``timestep_scale_multiplier`` are arch
constants read from (or defaulted to) the checkpoint metadata JSON config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LTXV = "ltxv"
LTXAV = "ltxav"


@dataclass(frozen=True)
class LTXAVConfig:
    """Fully-resolved LTX-2 DiT hyper-parameters (AV variant; video-only sets audio=False)."""

    is_av: bool
    in_channels: int
    out_channels: int
    num_attention_heads: int
    attention_head_dim: int
    cross_attention_dim: int
    caption_channels: int
    num_layers: int
    # audio stream (AV only):
    audio_in_channels: int = 128
    audio_num_attention_heads: int = 32
    audio_attention_head_dim: int = 64
    audio_cross_attention_dim: int = 2048
    # caption projection (LTX-2 19b has it; LTX-2.3 22b drops it for connector-only):
    has_caption_projection: bool = True
    # embeddings connector (AV only). LTX-2 19b: shared 3840-dim, ungated, 2 blocks.
    # LTX-2.3 22b: per-stream (video inner / audio inner), gated (to_gate_logits),
    # 8 blocks. The two connector inner dims + gate/depth are detected per variant.
    use_embeddings_connector: bool = True
    connector_attention_head_dim: int = 128
    # 2.3's per-stream audio connector runs at the audio head dim (64 -> 32 heads at
    # inner 2048); the 19b shared 3840 connector uses 128 (verified against both
    # checkpoints' embedded ``__metadata__`` configs).
    audio_connector_attention_head_dim: int = 128
    video_connector_inner: int = 3840
    audio_connector_inner: int = 3840
    connector_gated: bool = False
    connector_gate_dim: int = 32
    connector_num_layers: int = 2
    connector_num_learnable_registers: int = 128
    # LTX-2.3 additions (absent in LTX-2 19b): gated block attentions
    # (to_gate_logits, out dim = num_attention_heads) + prompt-conditioning adaLN
    # (embedded-config key ``cross_attention_adaln``; widens the per-block
    # scale_shift_table 6->9 rows and the main adaln_single coeff 6->9).
    blocks_gated: bool = False
    block_gate_dim: int = 32
    has_prompt_adaln: bool = False
    # LTX-2.3 drives each stream's AV-cross-attention adaLN with the OTHER
    # modality's sigma (diffusers ``use_cross_timestep``: "True is the newer
    # (e.g. LTX-2.3) behavior; False is the legacy LTX-2.0 behavior").
    use_cross_timestep: bool = False
    # LTX-2.5: the video FFN can drop its bias (config keys ``ff_bias`` /
    # ``audio_ff_bias``, independently optional -- verified against the
    # diffusers convert script's 2.5 config, which sets only ``ff_bias=False``
    # for its reference checkpoint and leaves ``audio_ff_bias`` at its True
    # default). Both default True, matching every earlier checkpoint.
    ff_bias: bool = True
    audio_ff_bias: bool = True
    # LTX-2.5 KV-cacheable cross-attention: when False, the timestep-dependent
    # prompt-adaLN MLP (``prompt_adaln_single``/``audio_prompt_adaln_single``)
    # is dropped, and the per-block ``prompt_scale_shift_table`` (still present
    # whenever ``has_prompt_adaln`` is set) becomes a static, timestep-
    # independent modulation -- computable once per prompt and reused across
    # denoise steps (diffusers ``use_prompt_adaln_single``).
    use_prompt_adaln_single: bool = True
    # LTX-2.5.1+ generated-keyframe checkpoints: a learned ``(1, inner_dim)``
    # absolute-position embedding for keyframe tokens. Construction/load-parity
    # only -- the regular forward path does not consume it (diffusers doesn't
    # either; a dedicated keyframes pipeline applies it downstream).
    use_keyframes_abs_pos_embedding: bool = False
    # Raw checkpoint version from the safetensors metadata (``model_version``,
    # e.g. "2.5"), parsed to a tuple of ints when it looks like one. Not
    # consumed by construction or forward -- carried through so the sampling
    # layer can branch on it later.
    model_version: tuple[int, ...] | str | None = None
    # RoPE convention: LTX-2/2.3 (all-AV) checkpoints declare ``rope_type: split``
    # + ``frequencies_precision: float64`` in their embedded transformer config —
    # the main video/audio/AV-cross streams use the split-halves rotary, NOT the
    # legacy interleaved one (interleaved is only LTXV-0.9 video-only, which this
    # engine doesn't run). Verified against the 19b checkpoint metadata.
    rope_split: bool = True
    # arch constants:
    positional_embedding_theta: float = 10000.0
    positional_embedding_max_pos: list[int] = field(default_factory=lambda: [20, 2048, 2048])
    # audio stream is 1-D in time only (video is 3-D: frame/h/w).
    audio_positional_embedding_max_pos: list[int] = field(default_factory=lambda: [20])
    causal_temporal_positioning: bool = True
    timestep_scale_multiplier: float = 1000.0
    av_ca_timestep_scale_multiplier: float = 1000.0
    # RMSNorm eps: LTX 2.3 checkpoint config declares norm_eps: 1e-6 for q/k norms
    # (matches diffusers reference transformer_ltx2.py line 350). Other RMSNorms
    # (block norm1/norm2, norm_out) also use 1e-6 in the reference (line 181 default).
    norm_eps: float = 1e-6

    @property
    def inner_dim(self) -> int:
        return self.num_attention_heads * self.attention_head_dim

    @property
    def audio_inner_dim(self) -> int:
        return self.audio_num_attention_heads * self.audio_attention_head_dim

    @property
    def image_model(self) -> str:
        return LTXAV if self.is_av else LTXV

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "LTXAVConfig":
        image_model = config.get("image_model")
        if image_model not in (LTXV, LTXAV):
            raise ValueError(f"LTXAVConfig: unsupported image_model {image_model!r}")
        return cls(
            is_av=image_model == LTXAV,
            in_channels=int(config["in_channels"]),
            out_channels=int(config.get("out_channels", config["in_channels"])),
            num_attention_heads=int(config["num_attention_heads"]),
            attention_head_dim=int(config["attention_head_dim"]),
            cross_attention_dim=int(config["cross_attention_dim"]),
            caption_channels=int(config["caption_channels"]),
            num_layers=int(config["num_layers"]),
            audio_in_channels=int(config.get("audio_in_channels", 128)),
            audio_num_attention_heads=int(config.get("audio_num_attention_heads", 32)),
            audio_attention_head_dim=int(config.get("audio_attention_head_dim", 64)),
            audio_cross_attention_dim=int(config.get("audio_cross_attention_dim", 2048)),
            has_caption_projection=bool(config.get("has_caption_projection", True)),
            use_embeddings_connector=bool(config.get("use_embeddings_connector", True)),
            connector_attention_head_dim=int(config.get("connector_attention_head_dim", 128)),
            # Falls back to the shared connector head dim (19b: one shared 3840/128
            # connector config for both streams).
            audio_connector_attention_head_dim=int(config.get(
                "audio_connector_attention_head_dim", config.get("connector_attention_head_dim", 128))),
            video_connector_inner=int(config.get("video_connector_inner", 3840)),
            audio_connector_inner=int(config.get("audio_connector_inner", 3840)),
            connector_gated=bool(config.get("connector_gated", False)),
            connector_gate_dim=int(config.get("connector_gate_dim", 32)),
            connector_num_layers=int(config.get("connector_num_layers", 2)),
            blocks_gated=bool(config.get("blocks_gated", False)),
            block_gate_dim=int(config.get("block_gate_dim", 32)),
            has_prompt_adaln=bool(config.get("has_prompt_adaln", False)),
            use_cross_timestep=bool(config.get("use_cross_timestep", config.get("has_prompt_adaln", False))),
            rope_split=config.get("rope_type", "split") == "split",
            positional_embedding_theta=float(config.get("positional_embedding_theta", 10000.0)),
            positional_embedding_max_pos=list(config.get("positional_embedding_max_pos", [20, 2048, 2048])),
            audio_positional_embedding_max_pos=list(config.get("audio_positional_embedding_max_pos", [20])),
            causal_temporal_positioning=bool(config.get("causal_temporal_positioning", True)),
            timestep_scale_multiplier=float(config.get("timestep_scale_multiplier", 1000.0)),
            av_ca_timestep_scale_multiplier=float(config.get("av_ca_timestep_scale_multiplier", 1000.0)),
            norm_eps=float(config.get("norm_eps", 1e-6)),
            ff_bias=bool(config.get("ff_bias", True)),
            audio_ff_bias=bool(config.get("audio_ff_bias", True)),
            use_prompt_adaln_single=bool(config.get("use_prompt_adaln_single", True)),
            use_keyframes_abs_pos_embedding=bool(config.get("use_keyframes_abs_pos_embedding", False)),
            model_version=config.get("model_version"),
        )
