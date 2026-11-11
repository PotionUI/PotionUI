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

1. **Anchor pass.** One ordinary window of `17a + 5` frames: text-to-video, or image-to-video from
   the Start picture. Its latent chunks are `A0 … A(a-1)` plus a 2-latent tail.
2. **Bridge pass.** A second window of `17b + 5` frames whose first chunk is pinned to `A(a-1)` and
   whose last chunk and tail are pinned to `A0` and the first two latents of `A1`. Pinned spans are
   whole chunks at chunk boundaries, so every pinned latent sits at the phase it was made for and no
   pixels are re-encoded. Each pinned span enters twice:
   - as keyframe condition rows at the same latent positions (`latent_condition_rows`), the route
     the model was trained on, so it sees the clean content from the first step;
   - as pinned target rows (`pins.py`): on every step the velocity of a pinned row is replaced by
     the one whose clean estimate is the source latent, so the row walks the source's own noised
     path under any sampler, and after the last step the source is written back exactly.
3. **Join.** The loop is `A0 … A(a-2)` followed by the bridge up to its pinned tail. Its last chunk
   was denoised next to the bridge's pinned copy of `A0`, which is bit for bit the loop's first
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
   so with sound the loop length moves in 51-frame (2.125 s) steps.

## Frame arithmetic

`C` loop chunks deliver `17 C` frames. Without sound `C = round(frames / 17)`, at least 2; the bridge
gets `max(3, (C + 2) // 2)` chunks and the anchor the rest, `a + b = C + 2`. With sound `C` is a
multiple of 3, and `a - 1` and `b - 1` are too. Each window stays within 345 frames (14.4 s), so a
loop is at most 38 chunks (26.9 s), or 36 (25.5 s) with sound.

| Asked | Sound | Loop | Anchor | Bridge | Pinned in the bridge |
|---|---|---|---|---|---|
| 4 s | off | 102 frames (4.25 s) | 73 | 73 | latents 0-4 and 15-21 of 22 |
| 8 s | off | 187 frames (7.79 s) | 124 | 107 | latents 0-4 and 25-31 of 32 |
| 4 s | on | 102 frames (4.25 s) | 73 | 73 | latents 0-4 and 15-21; audio 0-27 and 85-121 |
| 8 s | on | 204 frames (8.50 s) | 124 | 124 | latents 0-4 and 30-36; audio 0-27 and 170-206 |

## Presets

- **MiniMax-H3 → Perfect loop** (`modes/loop`): Start picture (optional), What moves (the prompt),
  Length, Movement (Subtle by default), Screen, Sound (off by default), speed profile.

## Tradeoffs and limitations

- Cost is two windows whose lengths add up to the loop plus two chunks, plus one extra chunk of
  decode.
- The pins make the seam exact in latent space and in the decoder. Whether the bridge's free middle
  moves naturally into and out of its pinned ends, at every speed profile, is what a GPU run has to
  show (`tests/manual/h3_perfect_loop_check.py`).
- The camera has to stay fixed for a loop to close; the mode's prompt asks for that.
- Not validated on real weights.
