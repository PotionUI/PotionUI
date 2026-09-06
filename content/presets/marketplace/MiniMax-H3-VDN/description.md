**Experimental.** This is MiniMax-H3 with a second attention branch bolted on, published by OpenVDN as *Video DeltaNet*. In the ordinary model every part of the clip attends to every other part, which is what makes a long clip expensive. VDN splits that work in two: the original attention is narrowed to a handful of neighbouring frames, and a new branch summarises everything outside that window in a single pass along the timeline. The two are added together. The point of it is **length** — a clip whose frames no longer all have to see each other — not a better picture. Image and sound quality are the base model's, and the same prompt format applies.

What you get is the same one-pass video plus soundtrack, at the same 24 fps, from the same DiT you already use. What is new is a 4.28 GB branch file and two adapters, all three from the same place.

**Files.** Everything below lives in the Hugging Face repo `OpenVDN/vdn-minimax-h3`, folder `stage-dmd-step-250/`. Upstream names them all `model.safetensors` or `adapter_model.safetensors`, so rename each one as you download it.

- `linear_branch/model.safetensors` — 4.28 GB, the attention branch. Goes with your diffusion models; pick it under **VDN branch**. The identical file also ships under `stage-b-step-2000/`.
- `adapters/default/adapter_model.safetensors` — 334 MB. Required on every render, at strength 1.0, first in the LoRA list.
- `adapters/turbo/adapter_model.safetensors` — 851 MB. Only for the 8-step Turbo tier, at strength 1.0, second in the LoRA list.

The DiT, text encoder and both VAEs are the ordinary MiniMax-H3 files. Nothing here replaces them.

**Two speed tiers, and only two.** Turbo is 8 steps with both adapters loaded; Quality is 50 steps with the default adapter alone. These are the tiers OpenVDN trained, and the base preset's 24-step and 4-step recipes do not apply — the 8-step recipe lives inside the turbo adapter, so running 8 steps without it just gives you an under-stepped render.

**Pruned checkpoints need one extra file for Turbo.** Every Comfy-Org H3 repack is pruned: it replaces the model's timestep stage with a compact lookup table. The turbo adapter retrains that stage in its original form and cannot be applied over the table. The **AdaLN sidecar** field takes a ~63 MB file carrying that stage from a full 33B checkpoint, extracted with `python scripts/h3_extract_time_embedder.py`. Leave it empty for Quality, and empty if you already run a full checkpoint.

**Memory behaves differently from every other video preset here.** The branch's working memory scales with the **number of frames**, not with the canvas — halving the picture size does not help, halving the clip length does. At 1344×768 and 102 latent frames it peaks around 4.3 GB while a block runs, released before the next one, on top of the 4.28 GB the branch weights occupy for the whole render and everything the DiT already costs.

**Speed is unmeasured.** OpenVDN publishes roughly 2.9× on an H200 with FlashAttention-4 and 8-bit weights. That is not this engine, and no VDN run of any kind has been made here. Treat the honest claim as "a long clip may fit where it previously did not", not as a speedup.

**What this preset cannot do.**

- **No sparse attention.** Sol-Attn and SLA choose which parts of the clip attention may look at, which is exactly what the VDN window already chooses. They are not offered here.
- **No low-memory sequence chunking.** The new branch reads the whole clip at once and cannot be split into pieces.
- **Keyframes are untrained.** OpenVDN's released renders are prompt-only. A first or last frame from the Video Director still reaches the model in a structurally correct place, and continuation between shots works the same way, but no published VDN checkpoint has ever been trained with one. Expect it to be weaker than on the base preset, and prefer prompt-only clips for anything you care about.
- **One canvas has evidence.** 1344×768 is the only geometry OpenVDN renders. The other sizes are inherited from the base preset and untested here.

**Licensing of the model weights.** Unchanged from MiniMax-H3, and it covers this variant too. OpenVDN's *code* is Apache-2.0, but the weights — the base model it redistributes and the branch and adapters trained on top of it — are under the MiniMax H3 Community License, not an open-source license. Its Applicable Territory excludes the European Union, the United Kingdom, the United States and the Republic of Korea; the license does not authorize use of the weights *or of anything generated with them* in those territories without an individual authorization from MiniMax, which is applied for at https://platform.minimax.io/h3-license. The license also requires separate written authorization above US$20M annual revenue, and requires a commercial product's interface to display "MiniMax H3" prominently. PotionUI ships this preset; it does not ship or download the weights, and the license is between you and MiniMax.
