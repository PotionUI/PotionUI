# Changelog

The three most recent releases; older history lives in the
[commit log](https://github.com/PotionUI/PotionUI/commits/master).

### 0.3.0 — 2026-11-20

- Image editor: edits are saved on the server; every tab autosaves, a new tab opens on a list of recent edits to continue, a picture saved from the editor keeps its steps so Edit reopens it as you left it, closing a saved tab no longer asks, and Save as new and exports name the Library item from the tab name; Layers and Edits are two panels and edits apply to the whole layered picture; each paint layer is its own row with opacity, rename, reorder, Duplicate and Merge down, a paint or picture layer can be added at any time, and everything is one undo history; a generated step whose picture changed underneath is marked stale and can be regenerated; Generate has a floating prompt and a form beside the canvas; a lasso joins the brush as a region tool; the header is a menu bar, the canvas has an action bar, and Formulas and Settings sit in the dock with the applied count; the legacy modal editor is gone from media fields and media tool dialogs.
- Editor tasks: Edit a region runs any preset mode on a marked region and blends the result back as a patch; Remove an object and Replace the background run in one click with no prompt writing; Expand puts a picture on a larger canvas in several formats at once, with social and banner sizes, a custom size and a draggable position; Upscale takes a factor or a size and runs tiled with feathered blending; Fix faces and hands finds faces to tick, accepts hand marks and a strength, and lands as a layer; each task can use a setup of your choice, with your own default.
- Video editor: follows the Image editor with a rail of tools, options as form fields and the panel on the left; clips are saved as edits like pictures; extend, upscale, restore, refine faces, colorize, day to night and remove object run on the whole clip or a marked time range and splice back in.
- Editor extensions: tools, rail entries, overlays, step kinds and tasks are registries that core and plugins use the same way; tool options are declared as fields rendered by the dynamic form; presets declare editor modes and the tasks their modes can do; editor sessions hold a mode's values and settings.
- Kits: kit cards show every preview picture whole at its own aspect in a packed layout; the kits list has a Per page picker; the new manage_kits tool lets MCP and chat list, inspect, create, edit, fill and delete kits.
- History: a running generation opens in Generation Details with its parameters and live preview and turns into the finished view in place; the origin badge sits in the details header; History has an origin filter; Reuse settings opens the Generate tab from History, prompts, model pages and the grid viewer.
- Pages: Kits, Library, History, Inspirations, Models, Prompts, Phrasebook and Auto-organize share one list header with themed, searchable filters.
- Admin: the Activity tab shows the media tools pool queue, with its limits in System Settings.
- Native engine and models: LTX-2.5 gets Text to Video, Image to Video and First to Last Frame; MiniMax-Music3 compiles its depth decoder and replays the language model step as one CUDA graph, and lyrics are optional while Instrumental is on; sampling placement prices activations per token so a full-size Qwen-Image-2.1 edit evicts the co-resident models it needs to.
- MCP: a LoRA can be attached to a generation by id, file name or path.
- Fixes: RunPod provider moves to RunPod's REST API v2; Google Gemini lists the 22 October 2026 Veo 3.1 shutdown; Windows serves .js, .css, .wasm and .svg with the right content type, and the local storage driver retries renaming a file another handle briefly holds; MiniMax-H3 cover-crops reference pictures, clips and start pictures instead of squeezing them; the editor saves a mode-keyed session without nesting its values one level too deep; a paint stroke drawn while a layer is re-shown or right after adding one is no longer lost.
- Upgrading: saved edits live in a new server table and old editor configuration files (editor.yml) are no longer read; on first start your existing editor sessions and each user's default task setups move to the new mode keys, and any that cannot be matched to a mode are left as they were; paint saved by an earlier version opens as a single layer; install the update, start once, and open the Image editor to continue earlier work.

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
