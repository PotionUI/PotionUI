import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';

// The `director` mode is capability-shaped: with segment_routing it is Wan's
// routed multi-shot chain, without it LTX's keyframe/audio timeline. The
// retired `chain` mode is folded into `director` -- there is no separate mode.
export type DirectorMode = 't2v' | 'i2v' | 'flf' | 'director';

// Per-segment resolution the backend derives (src/features/video_director/
// normalize.py: derive_segment_sub_type) whenever a preset's capabilities
// declare `segment_routing: true` (Wan's director mode) -- which loaded
// checkpoint set (t2v vs i2v-concat) a segment's generation runs on. `chain`
// here is a per-segment sub-type (a tail-frame continuation), distinct from the
// retired `chain` MODE.
export type SegmentSubType = 't2v' | 'i2v' | 'flf' | 'chain';

// One entry of a segment's per-shot SELECTION from the preset's whole-form
// reference pool (`references` capability 'per_shot' only): either a
// resolved storage path, or a `{field, label?|path?}` pointer into one of the
// preset's `reference_fields` pool fields on the submitted form -- the same
// addressing the chat tool's `upsert_media.form_media` already uses
// (src/features/llm/tools/builtin/video_director_tool.py). This ONE shape is
// used identically in three places: on the editor segment itself
// (`ChainSegment.references`/`DirectorPromptSegment.references` -- the chat
// tool's `get_video_director` read model reads it directly off the document,
// same as `sub_type_override`, so it is never translated to a different
// editor-local shape), in the `upsert_segment` chat op, and on the wire
// (`WireSegment.references`). `dereferenceFormMediaRefs` resolves a
// `form_media` entry against the live form before submission
// (src/features/video_director/normalize.py's `_resolve_reference_entry`
// mirrors the same resolution server-side against `form_data` as a backstop).
export type SegmentReference = { path: string } | { form_media: { field: string; label?: string; path?: string } };

export interface DirectorLoraRef {
	model: string;
	strength: number;
	/** Strength to restore when re-enabled after being toggled off - see
	 * LoraPickerItem in types/models.ts. */
	saved_strength?: number;
}

export interface DirectorLoraStacks {
	high: DirectorLoraRef[];
	low: DirectorLoraRef[];
}

export interface DirectorPromptSegment {
	id: string;
	start: number;
	end: number;
	text: string;
	prompt_segments: Segment[];
	/** Per-shot selection from the whole-form reference pool -- see the same
	 * field on ChainSegment. */
	references?: SegmentReference[];
}

// A Director media entry's value can point at an item living on the
// generate FORM's own media-loader field(s) instead of embedding one of its
// own (Stage B "global reference media"). `path` is the item's stable
// storage path, never an array index -- reordering the form field's items
// must not silently repoint the reference. Resolution against the live form
// (display, and the wire-doc dereference at submission) is byte-deterministic
// pure logic in utils/videoDirector.ts; the WIRE contract server-side is
// unchanged -- normalize.py never sees `form_ref`, only the resolved
// embedded media the frontend substitutes in before submitting.
export interface FormMediaRef {
	form_ref: { field: string; path: string };
}

export type DirectorMediaValue = MediaRef | FormMediaRef;

export interface DirectorKeyframe {
	id: string;
	start: number;
	role: 'first' | 'last' | 'free';
	strength: number;
	media: DirectorMediaValue | null;
}

// How a family's pipes consume an audio track (mirrors _AUDIO_ROLES in
// src/features/video_director/normalize.py): `condition` feeds it into the
// generation, `mux` lays it onto the finished video. Absent means condition.
export type DirectorAudioRole = 'condition' | 'mux';

export interface DirectorAudioSegment {
	id: string;
	start: number;
	trim_start: number;
	length: number;
	media: DirectorMediaValue | null;
	role?: DirectorAudioRole;
}

export interface DirectorIcLoraEntry {
	id: string;
	lora: DirectorLoraRef | null;
	ref_media: DirectorMediaValue | null;
	strength: number;
}

// One independent LTX clip inside a timeline director film. Each shot is its
// own generation (buildDirectorSubmission emits one VideoDirectorWireDoc per
// shot) -- `duration`/`segments`/`keyframes`/`audio`/`ic_lora` are all
// shot-local (film time and shot-local time are the same thing for a single
// shot, and every editor lane already reads/writes shot-local time). See
// PLAN.md §B.
export interface DirectorTimelineShot {
	id: string;
	title?: string;
	duration: number;
	/** True when this shot should inherit its leading frame from the
	 * PREVIOUS shot's rendered output (LTX has no native multi-shot
	 * continuation, so this is purely an editor/compile-time join -- see
	 * `buildDirectorSubmission`'s doc comment and `validateDirector`'s
	 * "needs its previous shot" reason). Meaningless (and always false) on
	 * the first shot. */
	continue_from_previous: boolean;
	segments: DirectorPromptSegment[];
	keyframes: DirectorKeyframe[];
	audio: DirectorAudioSegment[];
	ic_lora: DirectorIcLoraEntry[];
}

export interface DirectorTimelineDoc {
	fps: number;
	shots: DirectorTimelineShot[];
}

export interface ChainSegment {
	id: string;
	prompt: string;
	prompt_segments: Segment[];
	duration: number;
	loras: DirectorLoraStacks | null;
	/** This segment's own leading (start) frame -- legal on ANY segment that
	 * "opens fresh" (index 0, or a segment not continuing from its
	 * predecessor), not just segment 0 -- see `chainSegmentEdgeAllowances` in
	 * utils/videoDirector.ts. */
	keyframe: DirectorMediaValue | null;
	keyframe_strength: number;
	/** This segment's own trailing (end) frame. Only ever consumed by the
	 * generator paired with `keyframe` on the SAME segment (that combination
	 * resolves to the `flf` sub-type -- see `deriveSegmentSubType`); a
	 * trailing frame with no leading frame on the same segment has no effect
	 * and `chainSegmentEdgeAllowances` never offers it alone. */
	last_keyframe: DirectorMediaValue | null;
	last_keyframe_strength: number;
	// Explicit override of the derived sub-type (see `deriveChainSegmentSubType`
	// in utils/videoDirector.ts). null means "let derivation decide" -- the
	// only override the editor exposes is forcing a prompt-only segment that
	// would otherwise continue the previous one to a fresh t2v shot instead.
	sub_type_override: 't2v' | null;
	/** Per-shot selection from the whole-form reference pool (`references`
	 * capability 'per_shot' only). Absent/empty means "the whole pool" -- see
	 * `withShotReferences` in stageModel.ts for the empty-selection rule. */
	references?: SegmentReference[];
	/** Explicit per-segment override of the wire settings.steps/cfg -- null
	 * (the default) means "let the backend use its own default", mirroring
	 * `WireSegment.steps`/`cfg`. Surfaced by the Overrides disclosure. */
	steps: number | null;
	cfg: number | null;
	/** Derived-label override -- see `deriveShotLabel`; empty/absent keeps the
	 * derived label. */
	title?: string;
}

// A keyframe placed anywhere along a chain-style director's concatenated
// timeline (`keyframes: "anywhere"`). `at` is seconds from the start of the
// chain, bounded by the total of the per-segment frame counts over fps. Unlike
// DirectorKeyframe there is no first/last/free role: every entry maps to a
// wire `role: "keyframe"` media reference.
export interface ChainKeyframe {
	id: string;
	at: number;
	strength: number;
	media: DirectorMediaValue | null;
}

// Chain-wide continuity settings (Wan's director mode). Seeded from the
// preset's `director.continuation` capability, tweakable in the editor's
// advanced controls, and emitted verbatim into the wire doc's settings.
export interface ChainContinuation {
	overlap_frames: number;
	stitch: boolean;
}

export interface SimpleComposition {
	duration: number;
	fps: number;
	start_image: DirectorMediaValue | null;
	first_frame: DirectorMediaValue | null;
	last_frame: DirectorMediaValue | null;
}

export interface VideoDirectorUiState {
	zoom?: number;
	collapsed?: Record<string, boolean>;
	/** Set once `toModelessDirectorValue` has folded a legacy t2v/i2v/flf
	 * document's `simple.*` fields into `chain`/`timeline` -- the idempotency
	 * guard that stops a later re-normalize from re-projecting stale `simple`
	 * data over live edits the modeless editor already made. Never read
	 * outside that function. */
	modeless?: boolean;
	/** Which shot is expanded in the Shot Console. Null/absent means "the
	 * first shot" (`ShotConsole.svelte`'s own fallback never persists a
	 * dangling id). */
	activeShotId?: string | null;
}

export interface VideoDirectorValue {
	schema_version: 1;
	mode: DirectorMode;
	global_prompt: string;
	global_prompt_segments: Segment[];
	negative_prompt: string;
	negative_prompt_segments: Segment[];
	simple: SimpleComposition;
	timeline: DirectorTimelineDoc;
	chain: {
		fps: number;
		segments: ChainSegment[];
		continuation: ChainContinuation;
		keyframes: ChainKeyframe[];
		audio: DirectorAudioSegment[];
		/** Mirrors the backend's `settings.timing_profile` (see
		 * `src.pipelines.pipes.generator.chain_video_wan22.geometry`'s module
		 * docstring): a generation-time pipe config value (Wan's
		 * `motion_latent_count`) the orchestrator attaches onto a normalized
		 * document from the bound form. Nothing in this editor currently
		 * WRITES this field from a live `svi_motion_latent_count` value (see
		 * `resolveDirectorTimingProfile` in `$lib/utils/videoDirector` for the
		 * live-form half of the resolution) -- this is the round-trip half:
		 * parsed if a reopened/restored document happens to carry it, `null`
		 * for every fresh document. */
		timingProfile?: { motionLatentCount: number } | null;
	};
	ui?: VideoDirectorUiState;
}

// A preset's stance on the whole-form reference pool (MiniMax-H3 refs mode
// and any future family that conditions on a set of reference images/videos/
// audio rather than a single keyframe): null means the preset (mode) has no
// such pool at all, 'whole' means every shot always conditions on the entire
// pool (no wire field, no per-shot UI), 'per_shot' means each shot may select
// a subset (see `references` on ChainSegment/DirectorPromptSegment and
// WireSegment). Lives on `DirectorCapabilities` (not per DirectorMode) --
// mirrors `capabilities.references`/`reference_fields` read at the top level
// in src/features/video_director/normalize.py, alongside `segment_routing`.
export type DirectorReferencesCapability = 'whole' | 'per_shot' | null;

export interface DirectorModeCapability {
	tips: string[];
	maxDuration: number | null;
	// director-mode capabilities
	audio: boolean;
	icLora: boolean;
	maxKeyframes: number | null;
	// chain-mode capabilities
	perSegmentLoras: boolean;
	// 'anywhere' is only meaningful alongside segment_routing: the chain-style
	// composer may place keyframes at any point along the concatenated timeline,
	// not just as the opening shot's start image.
	keyframes: 'first_only' | 'anywhere' | 'none';
	maxSegments: number | null;
	maxFramesPerSegment: number | null;
	defaultSegmentDuration: number;
	// Wan director continuity defaults (null when the mode doesn't declare them).
	continuation: { source: 'tail_frames' | 'last_frame'; overlapFrames: number; stitch: boolean } | null;
	// Hard ceiling the backend enforces on settings.continuation.overlap_frames
	// (null when the mode declares none).
	maxOverlapFrames: number | null;
	// True only when this mode's raw capability block carries `continuation`
	// EXPLICITLY set to null (the key present, not merely absent) -- mirrors
	// normalize.py's `chain_continuation_disabled`. MiniMax-H3's refs override
	// sets this: continuation's condition-row overlay and ref2va's
	// reference-block prefix have no combined layout, so every shot in that
	// mode is a hard cut. Absence of the key (every other chain preset today)
	// leaves this false and changes nothing about existing continue-join
	// behaviour -- `continuation: null` above already means "no default to
	// seed a fresh document with" for those, unrelated to this flag.
	continuationDisabled: boolean;
	/** True when the generator fixes fps (e.g. MiniMax-H3 video at 24 fps) --
	 * surfaces as a "24 fps" header cap-strip chip and a "fps fixed" shot-card
	 * fact instead of an editable fps control. Mirrors the preset's
	 * `video_director.fps_locked` block. Absent/false everywhere else. */
	fpsLocked: boolean;
}

export interface DirectorCapabilities {
	/** null = all preset modes are eligible for the director UI */
	presetModes: string[] | null;
	modes: Partial<Record<DirectorMode, DirectorModeCapability>>;
	/** ordered t2v,i2v,flf,director,chain filtered to declared modes */
	enabledModes: DirectorMode[];
	defaultDuration: number;
	defaultFps: number;
	maxDuration: number | null;
	/** Optional generator frame-count ceiling. When present, the backend snaps
	 * non-chain clips to its causal-VAE 1 + k*8 lattice. */
	maxFrames: number | null;
	/** mirrors capabilities.segment_routing -- gates the per-segment sub-type
	 * badge/toggle in the chain editor (Wan only; LTX's director mode never
	 * sets this). */
	segmentRouting: boolean;
	references: DirectorReferencesCapability;
	/** Names of the form fields that hold the reference pool (e.g. `references`,
	 * `reference_videos`, `reference_audios` for MiniMax-H3's refs mode). Empty
	 * when `references` is null. */
	referenceFields: string[];
	/** Mirrors `video_director.family` (preset.yml) -- names which family's own
	 * window-planner arithmetic owns this preset's chain geometry (see
	 * `src.features.video_director.compile`'s `_MINIMAX_H3_FAMILY`). Read only
	 * by railModel.ts's H3 geometry port today; optional/absent for every
	 * preset that hasn't declared one (Wan, LTX) -- NOT a claim that the
	 * legacy raw-frame axis is correct for them, only that no family-specific
	 * port exists for them yet. */
	family?: string | null;
	/** Mirrors `video_director.timing` (preset.yml) -- names the sibling FORM
	 * FIELD (never part of the video_director document) that carries a
	 * generation-time pipe config value affecting this family's stitched
	 * timeline (Wan's `motion_latent_count`), and the value to assume when
	 * that field is genuinely absent from the bound form -- the SAME
	 * `motion_latent_count_field`/`motion_latent_count_default` pair the
	 * orchestrator reads (`src/features/generation/orchestrator.py`'s
	 * `timing_capability` handling). `null`/absent for every preset that
	 * hasn't declared one -- `resolveDirectorTimingProfile` (videoDirector.ts)
	 * treats that as "unknown", never a guessed default. */
	timing?: { motionLatentCountField: string; motionLatentCountDefault: number } | null;
}

// ─── Wire document (form_data.video_director sent to the backend) ─────────────

export interface WireSegment {
	id: string;
	prompt: string;
	negative_prompt: string;
	start: number | null;
	end: number | null;
	frames: number | null;
	seed: number | null;
	steps: number | null;
	cfg: number | null;
	loras: { high: DirectorLoraRef[]; low: DirectorLoraRef[] } | null;
	// Explicit sub-type override -- only sent when the user picked one; absent
	// means the backend derives it (derive_segment_sub_type). Never send the
	// derived value.
	sub_type?: SegmentSubType;
	// Per-shot reference-pool selection (`references` capability 'per_shot'
	// only) -- absent means "the whole pool" for this shot. Same `SegmentReference`
	// shape as the editor segment carries; `dereferenceFormMediaRefs` resolves
	// any `form_media` entry to `{ path }` before submission.
	references?: SegmentReference[];
}

// `media`/`media` below stay `DirectorMediaValue` (possibly a `form_ref`)
// through `buildDirectorSubmission` -- `dereferenceFormMediaRefs` is the ONLY
// place a `form_ref` is resolved to a concrete `MediaRef` before the wire doc
// reaches `form_data.video_director` (the server-side contract in
// src/features/video_director/normalize.py never sees `form_ref`).
export interface WireMedia {
	id: string;
	role: 'first' | 'last' | 'keyframe';
	segment_id: string | null;
	at: number;
	strength: number;
	media: DirectorMediaValue;
}

export interface WireAudio {
	id: string;
	// Omitted where the editor state carries none — the backend defaults an
	// absent role to "condition".
	role?: DirectorAudioRole;
	start: number;
	trim_start: number;
	length: number;
	media: DirectorMediaValue;
}

export interface WireIcLora {
	id: string;
	lora: DirectorLoraRef;
	reference: DirectorMediaValue | null;
	strength: number;
}

export interface VideoDirectorWireDoc {
	schema_version: 1;
	mode: DirectorMode;
	settings: {
		fps: number;
		duration: number;
		resolution?: string;
		seed: number;
		continuation?: { source: 'tail_frames' | 'last_frame'; overlap_frames: number; stitch: boolean };
	};
	segments: WireSegment[];
	media: WireMedia[];
	audio: WireAudio[];
	ic_lora: WireIcLora[];
	/** Provenance only -- "one clip = one generation" still holds, schema_version
	 * is unchanged. Present only when this doc was compiled from one shot of a
	 * multi-shot timeline film (a chain doc, or a single-shot timeline/t2v/
	 * i2v/flf doc, never carries it). `count` is the film's total shot count,
	 * `index` this shot's 0-based position. See PLAN.md §B. */
	shot?: { id: string; index: number; count: number; title?: string };
	/** Which slice of a CHAIN document to actually render -- only ever set by
	 * `buildDirectorSubmission` when the caller passes a `checkedShotIds`
	 * argument (the Shot Console's per-shot generation, PLAN.md §C W3);
	 * absent (the default) is the whole-film submission every existing
	 * caller/test already produces. `src/features/video_director/normalize.py`
	 * validates and echoes this; `compile_shot_plan`
	 * (`src/features/video_director/compile.py`) reads it, hooked from
	 * `orchestrator.py` after normalize. Meaningless (never set) on a
	 * timeline document -- a checked LTX shot is already its own standalone
	 * wire doc, nothing left to slice server-side. */
	render?: { scope: 'film' | 'shots'; shot_ids: string[] };
}

// ─── Document operations ─────────────────────────────────────────────
// The wire shape of one op in a Video Director `operations` array -- the
// Stage & Rail editor's own write vocabulary. Ids on upserts are pre-assigned
// by the backend; the frontend reducer (`applyDirectorOperations` in
// utils/videoDirector.ts) never mints one.

export interface DirectorOpSetMode {
	op: 'set_mode';
	mode: DirectorMode;
}

export interface DirectorOpSetSettings {
	op: 'set_settings';
	settings: { fps?: number; duration?: number; resolution?: string; seed?: number };
	/** Timeline style only, and only meaningful for `duration`: which shot's
	 * own length to set. Unambiguous (and may be omitted) when the document
	 * has exactly one shot; required once there are two or more -- an
	 * omitted/unresolvable id on a multi-shot document makes the duration
	 * write a no-op rather than guessing the first shot (see
	 * applyDirectorOperations's doc comment). Ignored on chain style, which
	 * has no document-wide duration to set (see `applySetSettings`). */
	shot_id?: string;
}

export interface DirectorOpSetPrompt {
	op: 'set_prompt';
	prompt: string;
}

export interface DirectorOpSetNegativePrompt {
	op: 'set_negative_prompt';
	negative_prompt: string;
}

export interface DirectorOpUpsertSegment {
	op: 'upsert_segment';
	segment: {
		id: string;
		prompt?: string;
		negative_prompt?: string;
		start?: number;
		end?: number;
		/** Chain style: the shot's length in seconds, authoritative over
		 * `frames` (which the tool derives from it at the document's fps). */
		duration?: number | null;
		frames?: number | null;
		/** Chain style: 't2v' forces a hard cut, null continues the previous shot. */
		sub_type_override?: 't2v' | null;
		/** Timeline style only: whether this shot should inherit its leading
		 * frame from the previous shot's rendered output. Ignored on chain
		 * style, which has its own join vocabulary (`sub_type_override`). */
		continue_from_previous?: boolean;
		seed?: number;
		/** `null` explicitly clears back to "auto" (the backend's own default);
		 * omitting the key entirely leaves the segment's current value alone. */
		steps?: number | null;
		cfg?: number | null;
		title?: string;
		/** Per-shot reference-pool selection -- same `SegmentReference` shape the
		 * document itself carries; the chat tool's `get_video_director` read
		 * model reads `segment.references` directly off the editor document. */
		references?: SegmentReference[];
	};
	/** Timeline style only: which shot's own beat list `segment.id` addresses
	 * -- a beat id is only unique WITHIN its shot (`mintId` scopes to one
	 * shot's own list), so this is required once the document has more than
	 * one shot (see `applyDirectorOperations`'s doc comment). Ignored on
	 * chain style, where `segment.id` already names the shot directly. */
	shot_id?: string;
}

export interface DirectorOpRemoveSegment {
	op: 'remove_segment';
	id: string;
	/** Timeline style only -- see `DirectorOpUpsertSegment.shot_id`. */
	shot_id?: string;
}

export interface DirectorOpReorderSegments {
	op: 'reorder_segments';
	ids: string[];
	/** Timeline style only -- see `DirectorOpUpsertSegment.shot_id`. */
	shot_id?: string;
}

export interface DirectorOpUpsertMedia {
	op: 'upsert_media';
	media: {
		id: string;
		role: 'first' | 'last' | 'keyframe';
		/** Which segment a 'first'/'last' image belongs to; null for a keyframe
		 * placed along the timeline (or the chain) rather than on a shot. */
		segment_id?: string | null;
		at?: number;
		strength?: number;
		/** Always the resolved, concrete storage path -- filled in by the
		 * backend tool even when the request addressed the item via
		 * `form_media` (see the tool's parameters), so a document applied
		 * without `form_ref` support still lands a working embedded media. */
		path: string;
		/** Present only when the chat request addressed this item by
		 * `form_media` -- tells the frontend applier to store a live
		 * form-field reference (see `FormMediaRef`) instead of the resolved
		 * `path` above. */
		form_ref?: { field: string; path: string };
	};
	/** Timeline style only, and only for a 'keyframe' (free-placement) entry
	 * or a shot's own 'first'/'last' edge -- see `DirectorOpUpsertSegment
	 * .shot_id`. Chain style keeps addressing a segment's own edge via
	 * `media.segment_id` (unchanged). */
	shot_id?: string;
}

export interface DirectorOpRemoveMedia {
	op: 'remove_media';
	id: string;
	/** Timeline style only -- see `DirectorOpUpsertSegment.shot_id`. */
	shot_id?: string;
}

export interface DirectorOpUpsertAudio {
	op: 'upsert_audio';
	audio: {
		id: string;
		role?: DirectorAudioRole;
		start?: number;
		trim_start?: number;
		length?: number;
		path: string;
	};
	/** Timeline style only -- see `DirectorOpUpsertSegment.shot_id`. */
	shot_id?: string;
}

export interface DirectorOpRemoveAudio {
	op: 'remove_audio';
	id: string;
	/** Timeline style only -- see `DirectorOpUpsertSegment.shot_id`. */
	shot_id?: string;
}

export interface DirectorOpSetContinuation {
	op: 'set_continuation';
	continuation: { overlap_frames?: number; stitch?: boolean };
}

export type DirectorOperation =
	| DirectorOpSetMode
	| DirectorOpSetSettings
	| DirectorOpSetPrompt
	| DirectorOpSetNegativePrompt
	| DirectorOpUpsertSegment
	| DirectorOpRemoveSegment
	| DirectorOpReorderSegments
	| DirectorOpUpsertMedia
	| DirectorOpRemoveMedia
	| DirectorOpUpsertAudio
	| DirectorOpRemoveAudio
	| DirectorOpSetContinuation;
