# Changelog

The three most recent releases; older history lives in the
[commit log](https://github.com/PotionUI/PotionUI/commits/master).

### 0.2.0 — 2026-11-13

- Kits: a kit is a named set of pictures, clips, audio and text in the Library; cite an item in a
  prompt as @kits.name.item and only the cited items reach the model; kits load into the reference
  fields and the Video Director form, the prompt picker opens a kit instead of inserting it whole,
  items can be added from History, a whole kit never exceeds the field's own maximum, a load names
  any kit it could not use, and creating a kit resumes after a partial failure.
- Media picker: History, Library and Kits are tabs of one picker used by every media field, with
  their filters (the Library picker gains collection, kind and favourite filters), Small, Medium and
  Large tile sizes and one footer; Replace on a loaded item opens the source menu and swaps the
  chosen item in place; the sources read Upload file, History and Library everywhere; History and
  picker Model filters show model names instead of file names.
- Video modes: MiniMax-H3 Video becomes Video Director, with Text to Video, Image to Video and First
  to Last Frame as single-clip modes whose pictures sit on a References tab like the other modes;
  Long video is gone, and sessions and entries that used it open in Video Director; single-clip modes
  take a mirror's fixed prompt text and expand prompt variations per clip; Perfect loop turns a
  picture and a description of what moves into a clip whose end flows into its start, with a length
  picked from fixed values (4 seconds by default), the family's resolution picker, a start picture
  fitted once for both ends, no speed profiles that cannot loop, a check that flags jumps, stops
  and zoom, and a note on which scenes loop best; LTX-2.5 gets Ingredients, which keeps characters
  and props from your pictures consistent in a new video, starting at the trained 5 seconds.
- MiniMax-H3-Image: a new family that loads the ComfyUI single-file weights, with text to image and
  edit presets that take LoRAs in both modes (ComfyUI, kohya and diffusers keys, picked by tag) and
  prompt guides for the language model; the VAE runs in fp16 with exact noise levels
  and the reference's tile blending; for cards with less memory, such as 12 GB, edits make room for
  the text encoder, encode on the CPU what the card cannot hold, and count reference pictures in
  placement (M4-S4).
- MOSS-Image regional prompting: a Text / Regional switch by the prompt title opens a canvas at the
  resolution's aspect where you draw regions with a description each; the compiled prompt shows in
  Resolved prompt, chat and MCP can set regions, and the prompt guide follows OpenMOSS's advice.
- Presets: admins can rename a preset and create mirrors that reuse its files under their own name,
  cover, defaults and modes; fixed prompt text added to every generation of a preset stays hidden
  from regular users, is checked against banned words when saved and follows each image's own prompt;
  a preset with mirrors cannot be removed; a preset can name its own recorded parameters, with a
  label, unit and type; the prompt syntax is a preset-wide default that reaches every mode with a
  prompt, with a lint warning for modes left out.
- Generation details: the modal has a two-row header with the status as a coloured media icon and
  the rating beside the title; parameters sit in bordered boxes with a copy icon, long text copies
  whole on click, short values fold into a list, sections carry count badges and remember their
  folds, and a failed or cancelled generation shows its notice at the top; recorded parameters keep
  their labels, units and order; Library items open in the same modal with previous and next, file
  facts, tags, source and source parameters.
- Generate: reference tokens in long prompts show as readable thumbnail chips that keep their
  trailing punctuation; the resolution picker has a button that swaps to the mirrored size when the
  model offers it; Delete removes the current generation from the Workbench, History and Library
  selections get Delete, and a collection can delete all its entries and stay; the selection bar
  stays on one line at every width; a model slot starts with a model you can use, and a submit
  naming a model you are not assigned is refused.
- API keys and MCP: API keys replace MCP tokens; create, edit and revoke keys in Settings, choose
  what each can do, see its activity and set its MCP tools, with three levels that only narrow (the
  admin ceiling, the user's default, the key); every core route declares what a key needs and a key
  acts with its own authority, so without the admin scope it cannot start an administrator-only
  preset; admins get switches, ceilings, per-person access and a list of every key with search,
  counts and revoke; sockets take a single-use ticket or an Authorization header, and revoking or
  narrowing a key closes its sockets; MCP tools can follow a generation to its results and reach
  elixirs, automation, downloads, sessions, notifications, sharing and deletes, and a tool that
  spends on the server's account is off until someone turns it on.
- Sessions API and guide: generate from a saved session over the API and MCP by naming it and
  sending only what changes, a saved session has a Use from a script dialog, and answers never show
  locked or hidden values; a new API guide covers authentication, generating, sessions, your content
  and MCP, with a reference generated from the real routes and examples that run in the test suite;
  the user guide and reference were corrected in 83 places and gained a Kits page.
- Queue routing: jobs spread across the backends of an engine, to the least loaded first, with a
  queue position per backend; a busy worker makes a job wait instead of failing it, a silent worker
  times out, health probes need two failures and run on their own, and a loading worker keeps the
  connection alive.
- Plugins: install a plugin from a catalog in Admin, with no failure path ending with unconfirmed
  code enabled (M4-S4); a plugin page can ship its own scoped stylesheet and gets more page-host
  helpers (M4-S4); the plugin API gains face detection and face vectors, image embeddings, frame
  grabs, Library storage, listing the presets a user may run, binding media into a Video Director
  document, a memory preview, enabled backends and cloud pricing, and scratch databases for plugin
  tests (M4-S4); the Plugins page points to plugin catalogs and lists a community one (M4-S4).
- Admin and Library: models get a Tags section on their page, tags on list rows and a Tags button in
  the toolbar; Auto-organize calls the Library area Library; Admin Recipes shows one card per
  model.
- Video and upscale tools: RTX Upscale gains an interpolate mode that multiplies a video's frame
  rate by 2 or 4 or converts it to a target rate, keeping the duration and the sound, an exact target
  short side and a strength; the RIFE interpolator takes a target frame rate and loads the 4.21 to
  4.26 checkpoints; SeedVR2 keeps the default 1080p video upscale within 12 GB cards; effects work
  again with ffmpeg 8; a cancelled RTX Video Super Resolution stops and cleans up its files (M4-S4).
- Onboarding: the wizard is six steps, a failed test generation no longer fails setup, setup resumes
  after a restart and stops resuming after repeated interruptions, an unsupported setup stops with a
  plain message, starters whose recipe has moved past its first version install, the start page waits
  for the server and an unclaimed instance's login opens the claim screen, and the launcher says why
  a backend that died while starting did; recipe test generations take each model from the preset's
  admin default; README and getting started say Windows is supported and which Python to use.
- Native engine: Wan VAE decodes make room first, then decode whole, in temporal chunks or in
  spatial tiles, so long clips fit; Wan 2.2 TI2V-5B gains image-to-video and Director continuations
  from a first frame, and the Wan 2.2 A14B start-image encode makes room when it does not fit; the
  idle Wan 2.2 expert stays page-locked between generations; a failed sampling run frees its
  activations before the model offloads; MiniMax-Music3 streams its text-encoder layers when the card
  cannot hold them; the Qwen-Image-2.1 reference encode logs when it falls back to tiles (M4-S4).
- Security: the first account needs the setup token unless the whole request is local, counting
  forwarded addresses, the forwarded host and the browser's origin; a user can no longer switch
  their own MCP access back on; a submit naming a preset or model file the user is not assigned is
  refused, and preset configuration is readable by admins only; fixed prompt text that is hidden from
  users no longer reaches them under any parameter name; MCP tools refuse undeclared arguments, check
  file sizes before reading and limit concurrent waits; an encoded javascript link in Markdown is no
  longer rendered as a link; RIFE downloads are pinned to their checksums.
- Fixes: the first generation after a start no longer fails on a half-loaded module until restart;
  Perfect loop opens on a moving frame so motion no longer slows just after the seam; the MiniMax-H3
  latent upscaler recommendations download again; the Library scrolls only its content again.
- Upgrading: migrations run on the first start and cannot be undone, so back up your data first; MCP
  tokens carry over as keys that work for MCP only, so create a new key for the HTTP API, and the
  old tokens table is dropped after copying; kits, preset mirrors and fixed prompt text, and
  recorded parameter labels add their own tables and columns; the old MCP token routes and the token
  in a socket URL still work this release but are deprecated, so move scripts to API keys and
  tickets; MiniMax-H3 Video is now Video Director and Long video is removed, a saved session on the
  retired Long video mode opens with an offer to convert it into Video Director, and API and MCP
  callers naming the retired mode get a refusal that names the replacement; a model slot that names
  a model its user is not assigned is now refused; startup may log "plugin not found" for two
  plugins that no longer exist, which is harmless.

### 0.1.0 — 2026-11-06

- Editors: the Image editor and the Video editor are now tabs on Generate, opened from the + menu,
  the phone Tabs sheet, Open in editor on a media field, the Edit shortcuts and the Edit rows and
  card menus of History and Library, which no longer open modal editors; every edit is a step you
  can undo, redo, hide or reorder, kept with the tab through a reload, and either editor can swap in
  another picture or clip from Library, History, a file, recent pictures or the clipboard; the Image
  editor crops, resizes, paints, masks, applies looks and effects and has an Adjust tool whose
  sliders edit live, shows transparency over a checkerboard, and saves, replaces, sends to a
  Generate field, downloads or copies the result; the Video editor scrubs, trims, splits, crops,
  resizes, changes speed, mutes, sets the volume, replaces the audio and applies effects with live
  previews in the browser, a frame preview and Preview 3 s, and its save runs in the background and
  is picked up again after a reload.
- Effects and media tools: video gets Fix colours, Film grain, Vignette, Fade, Ken Burns, LUT (a
  .cube from the LUT model folder) and Look, which brings the Image editor's looks to video, and the
  Image editor's Effects tool offers the image effects; effects preview live through WebGL and the
  saved file matches the preview; Find cuts splits a clip into its shots, a video can be split at a
  time point, and Mute, Volume, Replace audio and a paint layer join the tools; Slice cuts a sheet
  into cells, detecting panels and snapping to gutters, and saves them as Library images or as
  references, whose Library details name the sheet and cell they came from; videos in generation
  details and the Library loop by default, with a loop toggle remembered per viewer.
- Elixirs: chain presets, files and prompt writers as cards that run left to right in a brew; a
  preset step runs a saved session of any preset mode whose inputs and outputs can be worked out
  from the preset itself, a files step holds picks from History or the Library (up to a thousand, or
  a seeded random draw per brew), and a prompt writer has a language model write one or several
  prompts per input from an instruction with its own variables and phrasebook chips, the prompts for
  one input running as one batch where the preset supports it; each elixir opens as its own tab with
  live brew status, a step opens in the real Generate view in the same tab and follows its latest
  brew, a single step can run again and wait for you to choose its new outputs, and Brew history
  numbers, filters and pages brews and stops them one at a time, in a selection or all at once; an
  Elixirs chat assistant reads the open elixir, edits its steps after approval and picks outputs at
  a waiting step; admins set the Elixirs limits in Settings.
- Motion transfer: LTX-2.5, LTX-2 and MiniMax-H3 get a Motion transfer mode that makes the person in
  a picture perform the moves of a video: a pose guide tracked from the video is retargeted onto
  that person's proportions and followed through the Union-Control LoRA on LTX or a Fun ControlNet
  Union patch on MiniMax-H3, offered as an optional download by its starter; both LTX presets also
  get Follow motion, which copies a video's camera and layout onto a new scene from a depth or edges
  guide; the Pose guide of the Structure modes now draws its skeleton with the Apache-2.0 DWPose
  estimator and draws every person in the picture.
- Video modes: LTX-2.5 gets Transition, the video between a first and a last picture with an
  optional Transition LoRA, and Transparent video, which turns a clip into a transparent WebM plus a
  checkerboard MP4 for players without alpha; MiniMax-H3 gets Long video, which chains Director
  parts into one seamless video with per-part prompts, a start picture, an optional soundtrack and a
  colour hold against drift, a Flex speed profile that runs the Pika 6-step adapter in the Video,
  Extend, References, Remix and Talking video modes, and a Beta57 scheduler with separate video and
  audio shift fields in the Custom profile; H3 reference pictures are never enlarged, a reference
  voice that is not a 32 kHz file, or is an .m4a, is decoded through ffmpeg, a reference video too
  short for two frames is accepted, and a checkpoint stored in fp16 computes in bf16.
- Image modes: SDXL gets Img2img / restyle, Structure (pose, sketch, depth or edges through a
  matching ControlNet), Fix faces & hands and Upscale & refine (two to four times, re-detailed tile
  by tile); Krea-2 gets Edit (instruction edits with source pictures, fidelity and placement dials
  and an optional region mask, keeping the source's shape by default) and Upscale; Qwen-Image gets
  Camera angle (turn, tilt and zoom with the Multiple-Angles LoRA) and Make it real, and its edit
  modes read the picture in the layout the loaded checkpoint was trained with;
  Qwen-Image 2.1 gets Inpaint, a Turbo profile (8 steps at CFG 1 with an optional LoRA) and a
  prompt-only Photo task in Restyle, and its Combine images, like Qwen-Image Edit, takes an Output
  size with a crop or pad fit; Z-Image gets Enhance, Structure (Fun ControlNet Union 2.1) and
  Inpaint, and MOSS-Image gets Inpaint, every Inpaint repainting a painted area with grow, feather
  and strength and blending it into the source; Z-Image, MOSS-Image and Qwen-Image 2.1 get the
  Krea-2 styles worded for each model, with typography styles for MOSS-Image and Qwen-Image 2.1.
- Live previews: MiniMax-H3, LTX, Wan and Qwen-Image modes take an optional Preview decoder, a tiny
  TAEHV decoder from the model catalog, that shows the live preview in real colour; while a video
  samples, the Workbench, the floating Workbench and the phone canvas loop a short low-resolution
  clip of the sampling so far with a pause control, the progress line names the step it shows, and
  the engine leaves room for the decoder so a preview never pushes sampling out of memory.
- Generate and sessions: a session stays in sync wherever it is open, a change made in another
  window, by chat or by an elixir merging field by field instead of being overwritten; a changed
  field flashes, a clash shows the other value with Use theirs or Keep mine, and a background tab
  shows a dot; phone Generate fills the defaults and picks installed models without opening
  Settings, and keeps prompt edits with the Preset & session sheet closed; global shortcuts win over
  a media field's hover keys, so C opens chat and E folds the Workbench; the batch counter names
  videos and audio; a prompt of only spaces uses the mode's default caption.
- Install modes: every native preset that recommends its files gets an Install modes recipe, where
  an admin ticks modes, picks a target and per-file variants and sees download, disk and time totals
  before starting, the files download in parallel, survive a restart and can be cancelled, and the
  finished run offers Try it; a mode that is not ready shows Install to admins and a not set up note
  to users in the mode picker, and the preset overview, requirements tab and model picker open the
  same sheet; Admin > Recipes groups them with a ready count and real sizes, previews a dry run and
  removes a mode's own files after confirming what is deleted and what is kept; the setup wizard
  offers Add more modes, starter cards show where their files come from, and a ComfyUI target lists
  the files to add by hand; gated models show an Open on Hugging Face link wherever they are
  recommended or fail to download.
- Admin and settings: Models > Folders is a master-detail view with problem badges, per-type status,
  row actions and a details panel, and a failed change is reported instead of passing silently;
  Auto-organize gets instance model rules that give a user group access, add model tags, set a model
  type or fetch CivitAI data for matching models, with preview, runs, undo and retry; admin tables
  page with numeric pagination and a per-page choice; Clear VRAM offloads a model to RAM only when
  it fits beside the RAM reserve and unloads it otherwise, with an offload-only or unload-only
  choice on the automation action; user Settings is split into sections (Account, Plan & usage,
  Appearance, Content, Notifications, MCP) with links straight to each.
- Chat: a Conversation mode talks to the model directly, with no tools, page context or memory; an
  Apply block the model writes slightly wrong is repaired, or shown as a can't-apply note; applying
  a Video Director shot version on a single-shot mode lands in that shot's own Prompt, and an edit
  aimed at a guessed shot still applies; streamed thinking follows only while you are at the bottom,
  and an opened tools-used list stays open.
- Native engine: with nvfp4 fast multiply on, nvfp4 checkpoints run much faster on RTX 50 cards,
  since the activation path is fixed: activations are quantised for the fp4 multiply, by one Triton
  kernel where available, instead of falling back to dequantised weights; fp8 and nvfp4 activations
  are quantised in chunks, so long LTX refines keep the DiT on the GPU, an LTX stage hands its DiT
  to the next one without offloading it, and long MiniMax-H3 clips split attention into chunks
  instead of running out of memory; a new Fused block steps switch (off by default) runs each
  diffusion block's small per-step operations as combined GPU operations on Linux with Triton, which
  can change the output slightly; cancelling a generation stops before the next DiT block or VAE
  tile in every family; a text encoder too large for VRAM streams from system RAM; an attention
  kernel that fails to load falls back to SDPA once; SDXL ControlNets load without a hub call,
  follow the UNet's precision and stop the run with an error naming a missing file.
- Plugins: plugins can add fields, pipes, prompt resources and Director reference fields to existing
  preset modes, register model types with their own folders and labels, add @-citation sources, ship
  video effects, add Generate tool tabs listed inline in the new-tab menu, ask the default language
  model to write a prompt for a preset from an instruction and images, end a media tool run as
  cancelled, failed or with a summary and links, and index a model file they wrote; a plugin pipe or
  mode that collides with a core one is skipped with a warning.
- Privacy and security: regular users no longer see a backend's host and port in live progress or
  recommended model downloads; editor uploads must be images, audio or video of at most 512 MB and
  count toward the plan's storage and file limits; editor frame and clip previews are served to
  their owner only; prompt writing refuses presets the user cannot see and media outside storage; a
  LUT is read only from inside the LUT folder.
- Reliability: the server warms presets and backends right after a start and reports itself as
  warming up meanwhile, so the first request no longer waits and `./potionui start` keeps waiting
  for it; deleting a generation also removes the files, notifications, animated thumbnails and
  folders only it used, keeping anything a model gallery or preview still shows; temporary video
  previews are cleaned up; an in-place restart no longer carries GPU memory into the new process; a
  driver-level out-of-memory error is reported as out of memory, and a remote worker whose GPU
  allocator is damaged refuses GPU work and asks for a restart; system RAM is read correctly under
  cgroup v2 limits; the LTX video VAE holds less memory; GPU readings come from CUDA when NVML is
  missing; the Docker image builds plugin styles and keeps a compiler, so Triton and sparse
  attention work there.
- Fixes: on Windows, GPU readings and the VRAM budget work on drivers that ship NVML only in
  System32; the face and hand detectors run at their trained size, so hands are found and faces are
  no longer invented, and the SDXL hand detailer keeps small hands; Z-Image Enhance redraws less at
  every setting; MiniMax-H3 LoRAs with bias weights apply to pruned checkpoints; MiniMax-H3
  References shows its prompt after a start frame was picked in Video; a refused History delete says
  why; the default LLM provider survives a restart; an unclaimed instance reaches the claim screen
  however slowly setup status answers; the Resource profile's memory census no longer hangs the
  server on Python 3.12; the X/Y compare time chip reads in the light theme.
- Upgrading: new database migrations for elixirs, session sync, upload provenance, gated downloads
  and Auto-organize instance rules run on first start and cannot be undone, so back up the database
  first; the installer replaces nvidia-ml-py3 with NVIDIA's nvidia-ml-py and adds onnxruntime; Pose
  in the Structure modes downloads its two DWPose files on first use and no longer loads the CMU
  OpenPose weights; MiniMax-H3 References no longer has a picker for pre-encoded reference bundles
  and their folder is no longer a core model type, as a separate plugin now adds them through preset
  extensions; the Qwen-Image 2.1 control recipe is now a mode of its starter; under WSL2 and Docker
  Desktop the first driver-level GPU out-of-memory error stops new GPU runs until Restart Backend
  (`POTIONUI_CUDA_DRIVER_OOM=retry` keeps the old retrying); plugins that imported the fixed model
  type and folder alias lists read them from the live registry instead.

### 0.0.18 — 2026-10-30

- Onboarding: the first run explains what PotionUI is and that the owner account stays in this
  install's own database, then a guide inside the app checks this computer's GPU, memory and free
  space, picks a starter model that fits, asks where models live (Start fresh or Use an existing
  folder), offers an optional Hugging Face token and installs the starter; the guide can be skipped
  and resumed from the sidebar or Settings; the Hugging Face provider is on by default and Guided
  setup enables the plugins it needs, so a fresh install no longer fails on the recipe download; a
  remote claim without the setup token names the file that holds it; the password reset script
  resets the owner when no username is given and can run from the Pinokio menu, and the Pinokio launcher now
  installs from master.
- Media tools: one catalog of operation tools (trim, crop, resize, rotate, flip, speed, fit canvas,
  color key, crop to subject, frame, split, concat) runs through one cancellable ffmpeg runner, with
  chained steps fused into a single run; the Tools menu and card menu on Library and History group
  them as Edit and Effects submenus, the details window has a tool row, cards show progress with
  Cancel, and Library edits offer Save as new or Replace original while an edit of a generation
  always saves a new Library copy; Concat takes per-clip trims and crossfades;
  images and videos get a Resize editor with sizes worked out from the original; canvas and resize
  are capped at 8192 px, a video's audio is re-encoded when the container changes, audio trims keep
  the source's sample format and color keying keeps transparency.
- Media tools elsewhere: PotionAI chat and MCP can run any media tool from the live catalog,
  saving new copies at once and asking approval only to replace; automations get a Media Tool action
  that hands the result items to later nodes; presets can run media tools as a step; plugins add
  their own tools; the old `/api/media/edit` and
  `/api/media/split` API, the AudioTools preset and the seven pipes the catalog replaced are removed.
- Details windows: History and Library details open with an action bar above the media (Reuse,
  Edit, Effects, Favorite, Download and a menu for the rest), with E, D and F working as the hints
  show and a sheet on phones; History and Library cards share one corner layout of checkbox,
  Favorite and menu; an edit started from History opens the saved copy in the Library item window
  over History without leaving the page, Back or Escape returns to the generation's details at the
  same file and scroll position, Cancel or a failure reopens them, a failure offers Try again with
  the settings kept, Library items made from a generation link back to it, and an edit that keeps
  running after its editor closes shows a progress chip with Cancel.
- Chat: a new LLM harness runs every turn as stored, resumable steps, so a turn paused for an
  approval continues where it stopped, an interrupted or cancelled turn keeps its partial steps, and
  a transient provider failure retries with backoff and a notice; Anthropic, OpenAI Responses,
  Gemini and OpenRouter get their own adapters beside Ollama and OpenAI-compatible servers; a
  reasoning model's thinking is kept across tool calls and approval pauses and shown as raw, summary
  or hidden blocks with how long the model thought; read-only tools run in parallel; the tool list
  only grows during a turn so the prompt cache survives, and Anthropic's one-hour cache is used when
  asked for.
- LLM profiles and cost: every model has a capability profile (context window, output limit, prices,
  vision, reasoning controls, recommended sampling) resolved from the profiles PotionUI ships, what
  the server reports and admin overrides, with a Profile tab in Admin showing where each value
  came from; each step is priced with cached input, cache writes and reasoning counted, and every
  provider step is traced; Temperature and Max tokens can be left empty for the model's own value;
  a DeepSeek plugin adds deepseek-flash and deepseek-v4-pro, and plugins can add LLM configuration
  types.
- LTX-2.5: Restore, Colorize, Day to night, Add water, Remove object, Layout to render and
  Cinemagraph modes join Extend and Refine faces, with captions in the format each Lightricks model
  card gives and one prompt per image when variables or several images are used; Upscale can use the
  Generative 2x or Refine details LoRA; edit modes keep the source audio and length, take the whole
  clip in one pass when it fits in VRAM and otherwise run the fewest equal crossfaded windows, and
  the DiT stays on the GPU through a decode that fits beside it; every mode follows the Krea-2 tab
  layout with a speed profile and defaults to the Distilled profile (8 steps, CFG 1, also for
  LTX-2), so runs are faster and no longer overbaked, while Balanced and Quality say they need the
  dev checkpoint; Colorize defaults to 544p, the model's training size, and its caption follows the
  model card (a grayscale reference half, the colours named in the COLORIZE half); Restore and
  Upscale show all their task LoRAs or method models at once and require only the file for the
  selected Fix or Method (preset authors get a form reaction that makes a field required or
  optional); the log warns when a LoRA matched none of the model's weights.
- MiniMax-H3: new Extend, Refine faces, Remix and Talking video modes; Extend continues a video or
  adds a lead-in and joins it to the source; reference pictures narrower than 1:4 or wider than 4:1
  are padded with white instead of rejected, transparent cutouts are flattened onto white, auto
  resolution follows a reference video, and a reference video is decoded only as far as the clip
  can reach; every H3 mode shares one layout with a References tab and a speed profile; Upscale no
  longer turns the clip into a grid, because the latent upscaler now gets latents normalized the way
  it was trained, and Video mode with latent upscale hands its first stage on in the right space; the
  Upscale encode moves a resident DiT off the GPU when it needs the room, tiles to the free VRAM and
  steps the tile down on out of memory, a decode-only VAE is refused for Upscale with a clear message,
  a silent source clip no longer fails the audio step, and Upscale's prompt is optional and works
  when left empty; Refine faces keeps the face clean; Talking video and other optional-prompt modes
  no longer send a stale prompt when the box is left empty.
- Qwen-Image 2.1: new Edit text, Restyle (style, background, lighting, colourise and restore tasks)
  and Combine images (one edited image plus up to nine references pointed at with @) modes; Edit
  image crops a painted region with scaled context and composites it back pixel-exact; the control
  mode is now called Structure; Restyle and Edit text read the standard prompt, so segments,
  phrasebooks and variables work there, and a preset can mark a prompt optional so Generate does not
  wait for text.
- Prompts and generation: the prompt section header shows batch prompts as tabs with rename,
  duplicate and delete and a list of all prompts; prompt editor text no longer renders below 12px;
  progress spans the whole run when a batch of prompts or a quantity above one renders several
  images, instead of sticking at 100% after the first, and the panel describes each stage in plain
  words; a mode can declare its own missing files, which flag and block only that mode, and a
  just-downloaded LoRA unblocks Generate without a refresh; a mode names what its prompt is for
  with a label and an example, so Edit text asks for placement and
  style, Talking video for the scene and every LTX-2.5 edit mode for what it changes, while exact
  text and spoken words stay in their fields; the Generate page no longer flashes a disconnected
  banner or the wrong empty state while it loads.
- PotionAI modes: History, Models and Phrasebook get their own welcome text and no longer follow
  the Generate page's prompt-writing reply format, while every mode with tools is still told to act
  rather than announce; the chat Stop button has an accessible name, and users see their LLM's
  Show thinking setting.
- Fixes: reopened History details show the real preset name; Library, History and Models titles
  say 1 item, 1 generation, 1 model; Library type counts follow Favorites, a collection or Unsorted;
  a new Library card shows a loading placeholder instead of black; every setup wizard step opens at
  the top, the models path wraps only at a separator and the download consent reads as a sentence;
  the paint editor footer matches the other editors; the generation details stage and the picker
  tiles show a loading placeholder instead of black until the image has loaded; leaving the Tools
  Edit submenu with Escape or ArrowLeft no longer logs a Svelte state error; a tab edit still
  waiting to be saved is saved when the page is hidden or closed, so a quick reload keeps it; on Windows a joined video no longer loses the
  last frame of every clip.
- Library and pickers: Library items can be favorited from the card menu and the details corner,
  with a Favorites view; the Uploads picker shows your library in the Library's justified mosaic,
  with checkboxes and a preview magnifier; a painted inpaint mask reaches the form data, the mask
  editor saves a clean black and white mask, and faint stray pixels no longer stretch the inpaint
  region; the mode and variant pickers use the preset picker's dropdown and grow to fit their options,
  and every new mode has its own icon, including a redrawn Upscale, Extend and Talking video, with
  Cinemagraph and Combine images drawn so they no longer resemble well-known logos, and every media
  tool (Rotate, Flip, Fit canvas, Color key, Crop to subject, Frame, Concat) has its own glyph
  instead of the generic puzzle icon; a signed-in hard load no longer renders the page twice.
- Privacy: the Artifacts panel is gone from the generation details for every viewer; artifacts
  live in Admin → Generations and their files are served to admins only; regular users no longer
  receive server file paths anywhere in generation progress, warnings or errors, only file names.
- Security: the frontend takes undici 7.30.1, vite 6.4.5 and vitest 4.1.12, and the plugin build
  toolchain takes zod 4.6.7.
- Models and downloads: a model file gets its type verdict as soon as its header is read, keyed by a
  hash of the header and shared by files with the same header; indexing reads files less, with a
  moved file keeping its digest, downloads hashed while they stream and progress pushed instead of
  polled; a download reports complete only after its model is indexed; a recommendation whose name
  ends in a model file extension downloads under that name; the BiRefNet-general recommendations are
  verified Hugging Face links, also offered by the matting pickers in Pixal3D and TRELLIS2.
- Native engine and Windows: Resource profile times every sampling step with its attention mode,
  step-cache skip and wait for streamed weights, and its report adds a step table; FLUX 3 Klein
  INT8 ConvRot checkpoints load; nvfp4 layers build their dequant tables only when needed and warn
  once per reason when they fall back; the frontend builds again when the shell starts in a
  lowercase Windows drive letter.
- Upgrading: existing chats are converted to stored steps on first start; OpenAI configurations that
  point at a server other than api.openai.com become the openai-compatible type and configurations
  on openrouter.ai become OpenRouter; a Temperature still at the old 0.7 default is cleared to Model
  default, and the context window and `num_ctx` settings move from the configuration form to the
  Profile tab; plugins that used the `history_tools` key move to `media_tools`, and anything that
  called `/api/media/edit` or `/api/media/split` must use the media tools API.
