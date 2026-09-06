# Derived from: OpenVDN/vdn-minimax-h3 (Apache-2.0) `src/models/sequence_layout.py`
# — the packed-sequence geometry the linear branch reads (video rows contiguous and
# t-major, text rows separate from the rest of the globals), re-expressed here with the
# window bounds and the anchor-frame flag carried alongside so the branch takes one
# argument instead of six.

"""Packed-sequence geometry for the MiniMax-H3 VDN linear branch.

The branch cares about exactly two distinctions in the packed sequence: video vs
non-video (which rows it runs on and writes to), and text vs everything-else-non-video
(the prompt seeds both directional scans; the soundtrack must not). Conditioning and
audio rows are never special-cased.

``window_bounds`` is the softmax side's per-frame window, one ``(lo, hi)`` pair per
latent frame, inclusive and unclamped. The branch summarises what those bounds exclude,
so the two are one description read from opposite sides.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class VdnLayout:
    """Where the video and prompt rows live, and what the softmax window covers.

    ``skip_ends`` is the partner of the softmax side's ``anchor_frames == "both"``:
    frames 0 and F-1 are exact softmax in both directions, so the branch drops them
    from its input entirely and their readout rows are exactly zero. With the two
    frames gone the remaining bounds rebase by one — see :attr:`branch_bounds`, which
    is the only place that arithmetic exists.
    """

    seq_len: int
    video_start: int
    num_frames: int
    tokens_per_frame: int
    frame_height: int
    frame_width: int
    window_bounds: tuple[tuple[int, int], ...]
    text_start: int = 0
    text_len: int = 0
    skip_ends: bool = False

    def __post_init__(self) -> None:
        if self.frame_height * self.frame_width != self.tokens_per_frame:
            raise ValueError(
                f"frame grid {self.frame_height}x{self.frame_width} != "
                f"{self.tokens_per_frame} tokens/frame (S alone cannot be factored back "
                "into a grid, so the branch's short conv needs it given explicitly)"
            )
        if len(self.window_bounds) != self.num_frames:
            raise ValueError(
                f"{len(self.window_bounds)} window bounds != {self.num_frames} frames"
            )
        if self.video_end > self.seq_len:
            raise ValueError(
                f"video rows end at {self.video_end}, past the {self.seq_len}-row sequence"
            )
        if self.text_len and self.text_start + self.text_len > self.video_start:
            raise ValueError("text rows overlap the video block")

    def with_anchor_mode(self, anchor_frames: str) -> "VdnLayout":
        """This layout with ``skip_ends`` derived from the softmax side's anchor mode.

        The two are one decision, not two: only ``"both"`` makes frames 0 and F-1 exact
        softmax in both directions, and only then may the branch drop them. Setting one
        without the other leaves the branches overlapping or leaves a gap, and neither
        shows up as an error. Derive it here rather than setting ``skip_ends`` by hand.
        """
        return replace(self, skip_ends=anchor_frames == "both")

    @property
    def video_end(self) -> int:
        return self.video_start + self.num_frames * self.tokens_per_frame

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.frame_height, self.frame_width

    @property
    def text_range(self) -> tuple[int, int]:
        return self.text_start, self.text_start + self.text_len

    @property
    def branch_frames(self) -> int:
        """Frames the branch actually scans."""
        return self.num_frames - 2 if self.skip_ends else self.num_frames

    @property
    def branch_bounds(self) -> tuple[tuple[int, int], ...]:
        """The window bounds in the branch's own frame numbering.

        Under ``skip_ends`` the complement of ``[lo, hi]`` inside frames 1..F-2 is
        ``[1..lo-1] u [hi+1..F-2]``, which is the gather's own arithmetic on
        ``(lo - 1, hi - 1)``. Forgetting the rebase leaves a silently wrong far state,
        not an error.
        """
        if not self.skip_ends:
            return self.window_bounds
        return tuple((lo - 1, hi - 1) for lo, hi in self.window_bounds[1:-1])
