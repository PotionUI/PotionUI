---
type: technique
title: Perfect Loop (Pinned Bridge)
category_group: Sampling
status: needs-gpu-validation
families: [minimax_h3]
authors: []
paper: null
reference_impl: null
knobs:
  - key: loop
    surface: preset
    default: false
    effect: "Render an anchor pass and a bridge pass pinned to the anchor at both ends, then decode the joined latent with wrap-around context"
  - key: audio_source
    surface: preset
    default: generate
    effect: "`none` writes the loop without a soundtrack; `generate` also wraps the sound, and the length then moves in 51-frame steps"
---

# Perfect Loop (Pinned Bridge)

A loop is perfect when the frame after the last one is the first one and nothing in between gives
the join away: not the composition, not the motion, not the fine texture. MiniMax-H3's `loop` mode
gets there in two passes and one decode, all in latent space.

## How it works

The video VAE turns 17 frames into 5 latents (`1, 4, 4, 4, 4` frames each), encodes each 17-frame
chunk on its own, and decodes chunk `i` from its own 5 latents plus the first 2 of the next chunk,
cross-fading its first 5 frames with the previous chunk's look-ahead. So if every pair of
neighbouring chunks in a sequence was denoised together, the decoded sequence has no seam anywhere.
The loop is built so that this holds around the wrap too.

1. **Anchor pass.** One ordinary window one chunk longer than the loop, `17 (C + 1) + 5` frames:
   text-to-video, or image-to-video from the Start picture, which is fitted to the picked resolution
   once, keeping its aspect ratio (cover crop). Only its first frame is held. Its latent chunks are
   `A0 … A(C)` plus a 2-latent tail. The prompt asks for the end to look like the start, and the anchor
   does that on its own: it travels out and makes its way back over its closing chunks. `A0` is where
   it starts from rest (image-to-video eases in from the still picture), so the loop leaves it out
   and opens on `A1`.
2. **Bridge pass.** A second window of `17b + 5` frames whose first chunk is pinned to `A(h)`,
   `h = C + 2 - b`, and whose last chunk and tail are pinned to `A1` and the first two latents of `A2`.
   Its `b - 2` free chunks re-render the anchor's `A(h+1) … A(C)`: the stretch where the anchor
   makes its way back, now with the true, moving opening as its target. Pinned spans are
   whole chunks at chunk boundaries, so every pinned latent sits at the phase it was made for and no
   pixels are re-encoded. Each pinned span enters twice:
   - as keyframe condition rows at the same latent positions (`latent_condition_rows`), the route
     the model was trained on, so it sees the clean content from the first step;
   - as pinned target rows (`pins.py`): on every step the velocity of a pinned row is replaced by
     the one whose clean estimate is the source latent, so the row walks the source's own noised
     path under any sampler, and after the last step the source is written back exactly.
3. **Join.** The loop is `A1 … A(h-1)` followed by the bridge up to its pinned tail, `C` chunks in
   all. Its last chunk
   was denoised next to the bridge's pinned copy of `A1`, which is bit for bit the loop's first
   chunk.
4. **Decode with wrap-around context.** The loop latent is decoded as
   `[last chunk] + loop + [first 2 latents]` and the first 17 and last 5 decoded frames are dropped.
   Every chunk, the first included, is decoded from its true predecessor and successor, so the
   delivered frames are exactly one period of the endlessly repeated loop's decode. Long loops are
   decoded in segments of up to 19 chunks with the same context, which gives the same frames.
5. **Sound.** With sound on, the bridge's audio is pinned the same way, the audio latents are joined
   at the same instants, and the track is decoded with up to 4 seconds of the loop's own end before it
   and start after it, then cut to exactly the video's length.
   Audio runs at 40 latents a second against 24 frames, which meet on a whole latent every 3 chunks,
   so with sound the loop length moves in 51-frame (2.125 s) steps and the track is exactly as long as
   the video. The joins inside the period sit on whole audio latents, at most two thirds of a latent
   (17 ms) from the chunk boundary.

## Frame arithmetic

`C` loop chunks deliver `17 C` frames. Without sound `C = round(frames / 17)`, at least 3; with sound
`C` is a multiple of 3. The anchor gets `C + 1` chunks, the bridge `b = max(3, (C + 2) // 2) + 1`,
and the bridge's head is the anchor's chunk `h = C + 2 - b`, at least 1. The anchor stays within
345 frames (14.4 s), so a loop is at most 19 chunks (13.5 s), or 18 (12.75 s) with sound. The mode
offers 2, 3, 4, 5, 6, 8 and 10 seconds. `OPENING_CHUNKS` in `loop.py` is the one chunk left out;
set to 0 it gives back the loop that opens on the picture (anchor `C` chunks, bridge one chunk shorter).

| Asked | Sound | Loop | Anchor | Bridge | Head `h` | Pinned in the bridge |
|---|---|---|---|---|---|---|
| 2 s | off | 51 frames (2.13 s) | 73 | 73 | 1 | latents 0-4 and 15-21 of 22 |
| 3 s | off | 68 frames (2.83 s) | 90 | 73 | 2 | latents 0-4 and 15-21 of 22 |
| 4 s | off | 102 frames (4.25 s) | 124 | 90 | 3 | latents 0-4 and 20-26 of 27 |
| 8 s | off | 187 frames (7.79 s) | 209 | 124 | 6 | latents 0-4 and 30-36 of 37 |
| 4 s | on | 102 frames (4.25 s) | 124 | 90 | 3 | latents 0-4 and 20-26; audio 0-27 and 113-149 |
| 8 s | on | 204 frames (8.50 s) | 226 | 141 | 6 | latents 0-4 and 35-41; audio 0-27 and 198-234 |

## Why the bridge replaces the anchor's return instead of following it

Until 2026-11-12 the loop was the anchor up to its last full chunk followed by `b - 2` new bridge
chunks (`a + b = C + 2`). The anchor already came back on its own: in a real 4 s Quality run at
544x960 (anchor 73 frames, bridge 73) the anchor's distance from its first frame peaked at frame 40
and was falling by a luma level a frame at frame 67, five frames before its own end. The bridge
then had 34 frames to cover those five: it got home by frame 73 and held the picture for the
remaining 29 frames (1.2 s, steps under 0.35x the median from frame 73 to the wrap). The hold is
`17 (b - 2) - 5` frames at any length (12 at 2-3 s, 29 at 4-5 s, 46 at 6 s). Re-rendering the
anchor's closing chunks instead gives the bridge the anchor's own time to come back, `17 (b - 2)`
frames for a way the anchor made in `17 (b - 2) + 5`.

That run (4 s, Quality) had no hold left, and one ease: the first 4 frames after the wrap moved at
about half the clip's median (optical flow 0.52x, one step at 0.20x) while the last half second
before the wrap ran at 1.24x. Those frames were the anchor's own start from the still picture, which
the loop opened on. Opening on `A1` instead puts moving frames on both sides of the wrap; the bridge
gets one more free chunk because it now also carries the motion from the picture into `A1`.

## Presets

- **MiniMax-H3 → Perfect loop** (`modes/loop`): Start picture (optional), What moves (the prompt),
  Length, Movement (Subtle by default), Resolution, Sound (off by default). No speed profile: Steps (24) is on the Advanced tab.

## Tradeoffs and limitations

- Cost is a window one chunk longer than the loop plus one a little over half as long, plus one extra
  chunk of decode. At 4 s and 544x960 with a Start picture that is 21,757 and 22,267 tokens.
- It needs the full step count. At 24 steps the bridge brings the scene back (a measured run: largest
  step 1.8x the median, the wrap 1.6x, no zoom). At 4 steps, shift 12, every model evaluation sees the
  pinned rows at sigma 0.8 or above, the pins are nearly invisible to the model, and the first real
  run drifted 52 luma levels and cut at the wrap.
- Holding the picture at the anchor's end as well does not help, and was tried. It puts a second
  visit to the picture in the middle of every period. When the picture is fitted differently for
  the two keyframes (the model's own fit stretches a first keyframe and crops a later one), the
  difference reads as a slow zoom. With identical fits, the bridge has to travel from the picture to
  the picture and solves that with a cut back to its own opening: a visible reset about four fifths
  of the way through.
- The pins make the seam exact in latent space and in the decoder. Whether the bridge's free middle
  moves naturally into and out of its pinned ends is what a GPU run has to
  show (`tests/manual/h3_perfect_loop_check.py`).
- The camera has to stay fixed for a loop to close; the mode's prompt asks for that.
- Not validated on real weights.
