---
title: Getting Started
order: 10
---

# Getting Started

PotionUI is a self-hosted studio for generating images (and, with the right presets, video and audio) from text prompts using AI diffusion models. Instead of forcing every model into one generic set of controls, PotionUI uses **presets**: each preset is tuned for a specific model and gives you a curated form with exactly the options that model understands. You pick a preset, describe what you want, and PotionUI runs the generation pipeline and shows you the results in real time.

This page walks you from opening PotionUI for the first time to your first finished image. Which path applies depends on how you got here:

- **I just installed PotionUI** — nobody has signed in yet, and you're about to become this instance's owner. See [First run: claiming the instance](#first-run-claiming-the-instance).
- **Someone set this up for me** — an account already exists for you on someone else's instance. See [Signing in to an existing instance](#signing-in-to-an-existing-instance).

Either way, once you're signed in the rest of the page — picking a preset, writing a prompt, generating — is the same for everyone.

## Which install?

If nobody has installed PotionUI yet, the `./potionui` CLI offers four install
presets: **local** (this box has the GPU), **hybrid** (this box has the GPU
today, with room to add remote workers later), **remote** (no GPU here — a
VPS or laptop that dispatches to a remote worker; local and hybrid pull the
full CUDA stack, remote installs CPU-only PyTorch instead), and **worker** (a
separate GPU box that only serves another PotionUI instance, via `./potionui
worker doctor`/`worker start`). See the README's [Install](../../README.md#install)
section for the full table and commands, and [Remote Native](../remote-native.md)
for how a worker is wired to the instance that dispatches to it.

## First run: claiming the instance

A freshly installed PotionUI has no accounts at all. The **first** account ever created on it automatically becomes its **owner** — an Administrator account, with no separate setup step. There is no default password to change and nothing to "unlock": you *are* the administrator your instance is waiting for.

1. **Open the printed URL.** Whatever started the server printed a local address (for example `http://localhost:3001`). Open it in your browser.
2. **Create the owner account.** Because no owner exists yet, PotionUI sends you straight to a screen titled **"Create the owner account"** instead of a login form. Fill in a username, email, and password.
   - If you're opening this from the same machine the server runs on, that's all you need.
   - If you're reaching it from another machine on your network, the form also asks for a **Claim code** — a one-time token proving you have console access to the server. The startup CLI prints it after the server comes up (`First-time setup: open http://localhost:<port> and use this claim code if asked: <token>`), or you can read it directly from `storage/setup_claim_token` on the server. The code is deleted once the owner account is created, so you only need it this one time.
3. **Follow guided setup.** After the owner account is created you land on the **Setup** page, which answers "Is this instance ready to generate?". If a backend or model still needs attention, guided setup walks you through it recipe by recipe — each step shows its status, and any step that needs to download something first asks you to **approve and download** (it lists exactly what it wants to fetch and the total size before it starts). When everything finishes, the page shows **"You're all set"** along with the test image your setup produced, and a **Create your first image** button.
4. **Create your first image.** Click it (or, at any time, click **Generate** in the sidebar) and continue with [Your first generation](#your-first-generation) below.

Once the owner account exists, this instance is claimed: nobody else can repeat this flow, and everyone after you signs in the ordinary way, either by an account someone with admin rights creates for them, or by registering directly if the instance owner has turned on open registration.

## Signing in to an existing instance

If someone else administers the PotionUI instance you're using, they create your account for you in **Administration → Users** — there's no self-service signup unless they've explicitly turned on open registration for the instance.

Open PotionUI in your browser and sign in with the username and password your administrator gave you. If open registration is enabled on your server, you may instead see a **Register** link to create your own account. Once signed in, you land in the app with a narrow icon sidebar on the left.

The sidebar is how you move around:

- **Generate** — create new images and video.
- **History** — browse everything you have generated.
- **Models** — see the models installed on the server.
- **LLM** — chat-assisted prompt help (when configured).
- **Phrasebook** — manage word suggestions that appear as you type prompts.
- **Prompts** — one workspace for complete Prompts, saved Segments, Segment Templates, and Segment Categories.

Some installations also show extra entries added by plugins, and an **Administration** area if your account has admin rights.

## Your first generation

1. **Open the Generate page.** Click **Generate** in the sidebar. You start on an empty workspace tab.
2. **Pick a preset.** Use the preset selector in the bar at the top of the page. Presets are named for the model and style they drive (for example, a photorealistic SDXL preset or a QwenImage text preset).
3. **Pick a mode.** After choosing a preset, a mode selector appears next to it. Modes are the kinds of generation the preset supports, such as **txt2img** (make an image from text), **img2img**, or **inpaint**. Most presets start with txt2img.
4. **Write a prompt.** The positive and negative editors each begin with a segment card. Describe what you want in the positive list and what to avoid in the negative list. Add cards to separate ideas, insert a `BREAK`, or accept phrasebook suggestions as chips.
5. **Adjust the form (optional).** The panel beside the prompt holds the preset's controls — things like resolution, steps, guidance strength, seed, and how many images to make. The defaults are usually sensible, so you can skip this the first time.
6. **Generate.** Click the **Generate** button in the panel at the bottom of the page. On a phone, use the floating sparkle button on the Generate panel.

## Watching it work

While a generation runs you get live feedback:

- A **progress bar** and status text show which pipeline step is running and how far along it is.
- The **workbench** in the center shows in-progress previews as the image takes shape.
- When a step produces extra information — the random **seed** that was used, the list of **models** applied, or before/after **comparison** images — it appears alongside the run so you can inspect it.

You can cancel at any time with the cancel button. Generations keep running on the server even if you switch tabs or briefly lose connection, and PotionUI reconnects and catches up automatically.

## Where your results land

When a generation finishes, the final images appear in the gallery on the Generate page for immediate viewing. Everything you create is also saved permanently to your **History**, where you can browse, filter, tag, download, and reuse it later. Nothing you generate is thrown away unless you delete it yourself.

## Where to go next

- To learn the Generate page in depth — multiple workspace tabs, prompt styles, live previews, and the gallery — see **Generating Images**.
- To understand why each preset shows different controls, see **Presets & Forms**.
- To organize results, see **History & Tags**. To reuse complete compositions, single chunks, or slot layouts, see **Prompts Workspace** and **Segments**.
- If you administer this instance, **Administration** covers the Users, Backends, and Models tabs referenced above.
