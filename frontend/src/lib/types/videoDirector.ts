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

export interface DirectorTimelineDoc {
	duration: number;
	fps: number;
	segments: DirectorPromptSegment[];
	keyframes: DirectorKeyframe[];
	audio: DirectorAudioSegment[];
	ic_lora: DirectorIcLoraEntry[];
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
}

// ─── Chat operations (update_video_director tool) ──────────────────────
// The wire shape of one op in the `operations` array the chat tool's approval
// result carries. Ids on upserts are pre-assigned by the backend; the frontend
// reducer (`applyDirectorOperations` in utils/videoDirector.ts) never mints one.

export interface DirectorOpSetMode {
	op: 'set_mode';
	mode: DirectorMode;
}

export interface DirectorOpSetSettings {
	op: 'set_settings';
	settings: { fps?: number; duration?: number; resolution?: string; seed?: number };
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
		seed?: number;
		steps?: number;
		cfg?: number;
		/** Per-shot reference-pool selection -- same `SegmentReference` shape the
		 * document itself carries; the chat tool's `get_video_director` read
		 * model reads `segment.references` directly off the editor document. */
		references?: SegmentReference[];
	};
}

export interface DirectorOpRemoveSegment {
	op: 'remove_segment';
	id: string;
}

export interface DirectorOpReorderSegments {
	op: 'reorder_segments';
	ids: string[];
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
}

export interface DirectorOpRemoveMedia {
	op: 'remove_media';
	id: string;
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
}

export interface DirectorOpRemoveAudio {
	op: 'remove_audio';
	id: string;
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
