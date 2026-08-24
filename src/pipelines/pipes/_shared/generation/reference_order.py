"""The ONE packed order a reference-conditioned request's references are
presented, conditioned and laid out in.

MiniMax-H3's `ref2va` path walks its references three times -- the text
encoder's presentation (`qwen3.MiniMaxH3TextEncoder.encode_reference_
request`), the generator's `ReferenceBlock` list, and the condition-latent
iterators `layout.build_ref2va_packed_sequence` consumes alongside it -- and
all three have to walk the same sequence. A form offers one picker per
modality, so there is no interleaved user order to preserve: the packed order
is every image in picker order, then every video, then every audio.

Both consumers derive their traversal from :func:`pack_references` rather
than concatenating for themselves, because a divergence between any two of
the three is SILENT: the request still runs, the shapes still line up, and
every reference conditions the generation from another reference's position.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple

# Packed order across the modality groups. Not alphabetical by accident:
# image before video before audio is the order the released checkpoint's own
# reference list arrives in, and the order the `<Picture i>`/`<Video k>`/
# `<Audio j>` counters are expected to advance in.
PACKED_KINDS = ("image", "video", "audio")


def pack_references(
    images: Sequence[Any] | None = None,
    videos: Sequence[Any] | None = None,
    audios: Sequence[Any] | None = None,
) -> List[Tuple[str, Any]]:
    """`[(kind, media), ...]` in packed order -- images, then videos, then
    audio, each group in its own array order."""
    return [
        (kind, media)
        for kind, group in zip(PACKED_KINDS, (images, videos, audios))
        for media in (group or [])
    ]
