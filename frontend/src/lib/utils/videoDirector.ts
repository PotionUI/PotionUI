// Pure logic for the Video Director frontend feature: preset-capability parsing,
// default/normalize/validate for the editor value, and the editor→wire mapping
// sent to the backend as `form_data.video_director`. No Svelte imports.

import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import { sortByStart } from '$lib/components/video-director/timelineCore';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import type {
	DirectorCapabilities,
	DirectorMode,
	DirectorModeCapability,
	DirectorLoraRef,
	DirectorLoraStacks,
	ChainContinuation,
	ChainKeyframe,
	DirectorAudioRole,
	DirectorPromptSegment,
	DirectorKeyframe,
	DirectorAudioSegment,
	DirectorIcLoraEntry,
	DirectorTimelineDoc,
	ChainSegment,
	SegmentSubType,
	SimpleComposition,
	VideoDirectorValue,
	VideoDirectorUiState,
	VideoDirectorWireDoc,
	WireSegment,
	WireMedia,
	WireAudio,
	WireIcLora,
	DirectorMediaValue,
	FormMediaRef,
	SegmentReference
} from '$lib/types/videoDirector';

const MODE_ORDER: DirectorMode[] = ['t2v', 'i2v', 'flf', 'director'];

// Fixed (not counter-based) id for the single default chain segment.
//
// createDefaultDirectorValue/normalizeDirectorValue MUST be pure functions of
// their inputs: normalizeDirectorValue calls createDefaultDirectorValue
// internally as a fallback source every time it re-derives a document, and
// VideoDirectorEditor.svelte's re-sync $effect calls normalizeDirectorValue
// on every reactive re-run (including runs triggered by its own writes). A
// module-level *mutable* counter here (as originally used, RelayTimeline-
// style) made every such call produce a different id, so the JSON-equality
// re-sync check never converged — infinite effect loop, hard browser hang.
// User-triggered additions (ShotTimeline's "+ Add shot", KeyframeTimeline's
// "+ Direction" etc.) still use timelineCore's makeIdFactory per component
// instance, which is safe because those only run on discrete click events,
// not inside a reactive derivation. Only one tab's editor is mounted at a
// time, so a fixed id here never collides with a live document.
const DEFAULT_CHAIN_SEGMENT_ID = 'chain-0';

// Same fixed-id reasoning as DEFAULT_CHAIN_SEGMENT_ID, for the single
// placeholder segment toModelessDirectorValue mints on the timeline side of
// a projected t2v/i2v/flf document.
const DEFAULT_TIMELINE_SEGMENT_ID = 'timeline-0';

/** What `_normalize_media` falls back to when a keyframe-capable mode declares
 * no `max_keyframes` (src/features/video_director/normalize.py). */
export const DEFAULT_MAX_KEYFRAMES = 8;

function isRecord(v: unknown): v is Record<string, unknown> {
	return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function num(v: unknown, fallback: number): number {
	return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

function str(v: unknown, fallback = ''): string {
	return typeof v === 'string' ? v : fallback;
}

function normPromptSegments(v: unknown, fallbackText: string, fallbackId: string): Segment[] {
	if (Array.isArray(v)) {
		return v
			.filter(isRecord)
			.filter((segment) => typeof segment.id === 'string' && typeof segment.content === 'string')
			.map((segment) => ({ ...segment, id: segment.id as string, content: segment.content as string }) as Segment);
	}
	if (!fallbackText.trim()) return [];
	return [{ id: fallbackId, content: fallbackText, chips: {}, type: 'content', enabled: true }];
}

function isMediaRef(v: unknown): v is MediaRef {
	return isRecord(v) && typeof v.path === 'string';
}

export function isFormMediaRef(v: unknown): v is FormMediaRef {
	return (
		isRecord(v) &&
		isRecord(v.form_ref) &&
		typeof v.form_ref.field === 'string' &&
		typeof v.form_ref.path === 'string'
	);
}

/** Normalizes a possibly-stale/garbage stored value into a `DirectorMediaValue`
 * -- either a full embedded `MediaRef` or a `FormMediaRef` pointer -- or null. */
function normMedia(v: unknown): DirectorMediaValue | null {
	if (isFormMediaRef(v)) return { form_ref: { field: v.form_ref.field, path: v.form_ref.path } };
	return isMediaRef(v) ? (v as MediaRef) : null;
}

// ─── Form media references ───────────────────────────────────────────────────
// A Director media entry may point at an item living on the generate form's
// own media-loader field(s) (single object, or one entry of a `multiple`
// field's array) instead of embedding its own copy. Resolution keys off the
// item's stable `path` (never an array index -- reordering the form field
// must not silently repoint the reference).

function mediaItemMatches(item: unknown, path: string): item is MediaRef {
	return isRecord(item) && (item.path === path || item.relative_path === path);
}

/** The single form field value or `multiple` array entry at `field` whose
 * `path`/`relative_path` matches, or null if the field is gone or the item no
 * longer lives on it. Pure: reads `formData`, mutates nothing. */
export function resolveFormMediaItem(
	field: string,
	path: string,
	formData: Record<string, unknown> | null | undefined
): MediaRef | null {
	const value = (formData ?? {})[field];
	if (Array.isArray(value)) {
		const found = value.find((item) => mediaItemMatches(item, path));
		return found ? (found as MediaRef) : null;
	}
	return mediaItemMatches(value, path) ? (value as MediaRef) : null;
}

/** UI-facing resolution of a Director media slot's current value against the
 * live form: what to render (embedded media as-is, a form_ref's resolved
 * item, or a broken/missing state) without throwing. */
export type DirectorMediaDisplay =
	| { kind: 'empty' }
	| { kind: 'embedded'; media: MediaRef }
	| { kind: 'form_ref'; media: MediaRef; field: string }
	| { kind: 'broken'; field: string };

export function resolveDirectorMediaDisplay(
	value: DirectorMediaValue | null | undefined,
	formData: Record<string, unknown> | null | undefined
): DirectorMediaDisplay {
	if (!value) return { kind: 'empty' };
	if (isFormMediaRef(value)) {
		const resolved = resolveFormMediaItem(value.form_ref.field, value.form_ref.path, formData);
		return resolved ? { kind: 'form_ref', media: resolved, field: value.form_ref.field } : { kind: 'broken', field: value.form_ref.field };
	}
	return { kind: 'embedded', media: value };
}

export interface FormMediaOption {
	field: string;
	/** Humanized field name (e.g. "reference_image" -> "Reference Image") --
	 * the picker has no access to the field's schema `title`, so this is a
	 * best-effort label, not the form's own copy. */
	fieldLabel: string;
	item: MediaRef;
}

function humanizeFieldName(name: string): string {
	return name
		.replace(/[_-]+/g, ' ')
		.trim()
		.replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Every media item sitting on the current form's own media-loader field(s)
 * (single value or `multiple` array), for the Director "From form" picker.
 * Duck-types a field's value as media by the same `{path}` shape every
 * media-loader value carries -- other field value shapes in this codebase
 * (model refs use `modelPath`, LoRA rows have no `path`) don't collide with
 * it. `kind` narrows to items whose probed `type` matches (image/video/audio);
 * an item with no `type` recorded is never excluded by this filter.
 */
export function collectFormMediaOptions(
	formData: Record<string, unknown> | null | undefined,
	kind?: 'image' | 'video' | 'audio'
): FormMediaOption[] {
	const options: FormMediaOption[] = [];
	for (const [field, value] of Object.entries(formData ?? {})) {
		const items = Array.isArray(value) ? value : [value];
		for (const item of items) {
			if (!isMediaRef(item)) continue;
			if (kind && item.type && item.type !== kind) continue;
			options.push({ field, fieldLabel: humanizeFieldName(field), item });
		}
	}
	return options;
}

/**
 * Stable each-keys for a `FormMediaOption[]` list, uniqued with an index
 * suffix on the (add-flow-legal, e.g. the same library resource picked twice)
 * duplicate `field:path` pair -- a keyed each CRASHES on duplicate keys.
 * Mirrors LoraPickerField.svelte's `rowKeys` pattern: only entries after the
 * first occurrence get the suffix, so the first occurrence's key stays
 * stable across re-renders.
 */
export function formMediaOptionKeys(options: FormMediaOption[]): string[] {
	return options.map((opt, i) => {
		const key = `${opt.field}:${opt.item.path}`;
		return options.slice(0, i).some((p) => p.field === opt.field && p.item.path === opt.item.path) ? `${key}#${i}` : key;
	});
}

/**
 * Replaces every `form_ref` media value in a submission-ready wire document
 * with a full copy of the current form item it points at, resolved live
 * against `formData`. Pure and byte-deterministic for a given
 * (document, formData) pair -- never mints an id or reads a clock. An
 * unresolvable reference (the field is gone, or the item was removed/
 * reordered out) is reported in `errors` rather than thrown or silently
 * dropped, so the caller can block submission and show the user which
 * reference broke instead of sending a document the backend would also
 * reject via `_resolve_media_ref`.
 */
export function dereferenceFormMediaRefs(
	document: VideoDirectorWireDoc,
	formData: Record<string, unknown> | null | undefined
): { doc: VideoDirectorWireDoc; errors: string[] } {
	const errors: string[] = [];

	function resolve(value: DirectorMediaValue, context: string): MediaRef | FormMediaRef {
		if (!isFormMediaRef(value)) return value;
		const resolved = resolveFormMediaItem(value.form_ref.field, value.form_ref.path, formData);
		if (!resolved) {
			errors.push(`${context}: missing from form field "${value.form_ref.field}"`);
			return value;
		}
		return resolved;
	}

	const media = document.media.map((m, i) => ({ ...m, media: resolve(m.media, `media[${i}]`) as MediaRef }));
	const audio = document.audio.map((a, i) => ({ ...a, media: resolve(a.media, `audio[${i}]`) as MediaRef }));
	const ic_lora = document.ic_lora.map((e, i) =>
		e.reference ? { ...e, reference: resolve(e.reference, `ic_lora[${i}].reference`) as MediaRef } : e
	);

	// A segment's `references` entries address the whole-form reference pool by
	// `form_media` (field + label-or-path) -- resolved here the same way
	// `_resolve_form_media` resolves it server-side (exact path match, or a
	// case-insensitive trimmed label match).
	function resolveSegmentReference(value: SegmentReference, context: string): SegmentReference {
		if (!isSegmentFormMediaReference(value)) return value;
		const { field, label, path } = value.form_media;
		const options = collectFormMediaOptions(formData).filter((o) => o.field === field);
		let resolved: MediaRef | undefined;
		if (path) {
			resolved = options.find((o) => o.item.path === path)?.item;
		} else if (label) {
			const needle = label.trim().toLowerCase();
			resolved = options.find((o) => (o.item.label ?? o.item.name ?? '').trim().toLowerCase() === needle)?.item;
		}
		if (!resolved) {
			errors.push(`${context}: missing from form field "${field}"`);
			return value;
		}
		return { path: resolved.path };
	}
	const segments = document.segments.map((s, i) =>
		s.references
			? { ...s, references: s.references.map((r, j) => resolveSegmentReference(r, `segments[${i}].references[${j}]`)) }
			: s
	);

	return { doc: { ...document, segments, media, audio, ic_lora }, errors };
}

export function isSegmentFormMediaReference(v: unknown): v is { form_media: { field: string; label?: string; path?: string } } {
	return isRecord(v) && isRecord(v.form_media) && typeof v.form_media.field === 'string';
}

/** Defensively narrows one raw `segments[].references[]` entry to a
 * `SegmentReference` -- the ONE shape used identically on the editor segment,
 * the `upsert_segment` chat op, and the wire (see the type's own doc comment
 * in types/videoDirector.ts). Anything that isn't `{path: string}` or
 * `{form_media: {field: string, ...}}` is dropped. */
function parseSegmentReferenceEntry(raw: unknown): SegmentReference | null {
	if (isSegmentFormMediaReference(raw)) {
		const { field, label, path } = raw.form_media;
		return {
			form_media: {
				field,
				...(typeof label === 'string' ? { label } : {}),
				...(typeof path === 'string' ? { path } : {})
			}
		};
	}
	if (isRecord(raw) && typeof raw.path === 'string') return { path: raw.path };
	return null;
}

/**
 * Normalizes a possibly-stale/garbage stored per-shot reference selection.
 * An empty (or all-garbage) list is never stored as `[]` -- it collapses to
 * `undefined`, meaning "the whole pool", the same rule `withShotReferences`
 * (stageModel.ts) and the `upsert_segment` op applier enforce on every write.
 */
function normSegmentReferences(v: unknown): SegmentReference[] | undefined {
	if (!Array.isArray(v)) return undefined;
	const parsed = v.map(parseSegmentReferenceEntry).filter((r): r is SegmentReference => r !== null);
	return parsed.length > 0 ? parsed : undefined;
}

function normLoraRef(v: unknown): DirectorLoraRef | null {
	if (!isRecord(v) || typeof v.model !== 'string') return null;
	const ref: DirectorLoraRef = { model: v.model, strength: num(v.strength, 1) };
	// Preserved verbatim (not defaulted) so the toggle-off/on memory survives
	// this function running on every reactive re-normalize - see
	// LoraPickerItem.saved_strength in types/models.ts.
	if (typeof v.saved_strength === 'number' && Number.isFinite(v.saved_strength)) {
		ref.saved_strength = v.saved_strength;
	}
	return ref;
}

function normAudioRole(v: unknown): DirectorAudioRole | undefined {
	return v === 'condition' || v === 'mux' ? v : undefined;
}

function normLoraStacks(v: unknown): DirectorLoraStacks | null {
	if (!isRecord(v)) return null;
	const high = Array.isArray(v.high)
		? v.high.map(normLoraRef).filter((x): x is DirectorLoraRef => x !== null)
		: [];
	const low = Array.isArray(v.low)
		? v.low.map(normLoraRef).filter((x): x is DirectorLoraRef => x !== null)
		: [];
	return { high, low };
}

// ─── Capability parsing ──────────────────────────────────────────────────────

function parseModeCapability(
	raw: unknown,
	globalMaxDuration: number | null,
	globalDefaultDuration: number
): DirectorModeCapability {
	const r = isRecord(raw) ? raw : {};
	const tips = Array.isArray(r.tips) ? r.tips.filter((t): t is string => typeof t === 'string') : [];
	const maxDuration = typeof r.max_duration === 'number' ? r.max_duration : globalMaxDuration;
	const keyframes: DirectorModeCapability['keyframes'] =
		r.keyframes === 'first_only' || r.keyframes === 'anywhere' ? r.keyframes : 'none';

	const contRaw = isRecord(r.continuation) ? r.continuation : null;
	const continuation = contRaw
		? {
				source: (contRaw.source === 'last_frame' ? 'last_frame' : 'tail_frames') as 'tail_frames' | 'last_frame',
				overlapFrames: typeof contRaw.overlap_frames === 'number' ? contRaw.overlap_frames : 4,
				stitch: contRaw.stitch !== false
			}
		: null;
	// Mirrors normalize.py's `chain_continuation_disabled`: only true when the
	// raw block carries `continuation` EXPLICITLY as null (key present, value
	// null) -- distinct from `continuation` above being null merely because
	// the key was never declared at all (every chain preset before MiniMax-H3's
	// refs mode).
	const continuationDisabled = 'continuation' in r && r.continuation === null;

	return {
		tips,
		maxDuration,
		audio: r.audio === true,
		icLora: r.ic_lora === true,
		maxKeyframes: typeof r.max_keyframes === 'number' ? r.max_keyframes : null,
		perSegmentLoras: r.per_segment_loras === true,
		keyframes,
		maxSegments: typeof r.max_segments === 'number' ? r.max_segments : null,
		maxFramesPerSegment: typeof r.max_frames_per_segment === 'number' ? r.max_frames_per_segment : null,
		defaultSegmentDuration:
			typeof r.default_segment_duration === 'number' ? r.default_segment_duration : globalDefaultDuration,
		continuation,
		maxOverlapFrames: typeof r.max_overlap_frames === 'number' ? r.max_overlap_frames : null,
		continuationDisabled
	};
}

/**
 * Merges a `preset_mode_overrides` entry's raw block onto the base preset's
 * raw `vars.video_director` block -- mirrors `apply_preset_mode_overlay` in
 * src/features/video_director/normalize.py EXACTLY: every top-level key in
 * the override replaces the base's raw value wholesale (a `limits` override
 * is not itself field-merged with the base's `limits` -- it must repeat every
 * sub-field it wants to keep), except `modes`, where each named composition
 * mode is itself shallow-merged (`{...baseEntry, ...override}`) onto the
 * base's entry for that mode. Operating on raw JSON and re-running the merged
 * result through `parseDirectorCapabilities` (rather than merging already-
 * parsed/coerced capabilities field-by-field) is what keeps this byte-
 * identical to the backend -- there is no separate coercion path to drift
 * out of sync with `parseModeCapability`.
 */
function mergeRawCapabilities(base: Record<string, unknown>, override: Record<string, unknown>): Record<string, unknown> {
	const merged: Record<string, unknown> = { ...base };
	for (const [key, value] of Object.entries(override)) {
		if (key !== 'modes') merged[key] = value;
	}
	const baseModes = isRecord(base.modes) ? base.modes : {};
	const overrideModes = isRecord(override.modes) ? override.modes : {};
	const mergedModes: Record<string, unknown> = { ...baseModes };
	for (const [compMode, compOverride] of Object.entries(overrideModes)) {
		const baseEntry = baseModes[compMode];
		mergedModes[compMode] = isRecord(compOverride) && isRecord(baseEntry) ? { ...baseEntry, ...compOverride } : compOverride;
	}
	merged.modes = mergedModes;
	return merged;
}

/**
 * The single entry point for reading Director capabilities everywhere they're
 * used (the editor mount gate, UnifiedAIChat's form_state export, submission,
 * validation): when `presetMode` names an entry in `raw.preset_mode_overrides`,
 * merges that raw block onto the base raw block (`mergeRawCapabilities`) and
 * parses the RESULT; otherwise identical to `parseDirectorCapabilities(raw)`.
 */
export function resolveDirectorCapabilities(raw: unknown, presetMode: string | null | undefined): DirectorCapabilities | null {
	if (!isRecord(raw) || !presetMode) return parseDirectorCapabilities(raw);
	const overridesRaw = isRecord(raw.preset_mode_overrides) ? raw.preset_mode_overrides : null;
	const overrideRaw = overridesRaw && isRecord(overridesRaw[presetMode]) ? overridesRaw[presetMode] : null;
	if (!overrideRaw) return parseDirectorCapabilities(raw);
	return parseDirectorCapabilities(mergeRawCapabilities(raw, overrideRaw));
}

/** Parses the preset var `vars.video_director`. Returns null when the shape has no usable modes. */
export function parseDirectorCapabilities(raw: unknown): DirectorCapabilities | null {
	if (!isRecord(raw)) return null;
	const modesRaw = isRecord(raw.modes) ? raw.modes : null;
	if (!modesRaw) return null;

	const enabledModes = MODE_ORDER.filter((m) => isRecord(modesRaw[m]));
	if (enabledModes.length === 0) return null;

	const limits = isRecord(raw.limits) ? raw.limits : {};
	const defaultDuration = typeof limits.default_duration === 'number' ? limits.default_duration : 5;
	const defaultFps = typeof limits.default_fps === 'number' ? limits.default_fps : 24;
	const maxDuration = typeof limits.max_duration === 'number' ? limits.max_duration : null;
	const maxFrames = typeof limits.max_frames === 'number' ? limits.max_frames : null;

	const modes: Partial<Record<DirectorMode, DirectorModeCapability>> = {};
	for (const m of enabledModes) {
		modes[m] = parseModeCapability(modesRaw[m], maxDuration, defaultDuration);
	}

	const presetModes = Array.isArray(raw.preset_modes)
		? raw.preset_modes.filter((p): p is string => typeof p === 'string')
		: null;

	const segmentRouting = raw.segment_routing === true;
	// Top-level capability (not per composition mode) -- mirrors
	// `capabilities.get("references")`/`capabilities.get("reference_fields")`
	// read at the top of `normalize_video_director` in normalize.py, alongside
	// `segment_routing`.
	const references: DirectorCapabilities['references'] =
		raw.references === 'whole' || raw.references === 'per_shot' ? raw.references : null;
	const referenceFields = Array.isArray(raw.reference_fields)
		? raw.reference_fields.filter((f): f is string => typeof f === 'string')
		: [];

	return {
		presetModes,
		modes,
		enabledModes,
		defaultDuration,
		defaultFps,
		maxDuration,
		maxFrames,
		segmentRouting,
		references,
		referenceFields
	};
}

// ─── Edge/keyframe allowances ────────────────────────────────────────────────
// The single source of truth for whether a shot's leading/trailing edge (the
// Stage gate "well") and the free keyframes lane may exist at all, given a
// preset's declared capabilities -- shared by stageModel.ts (gate rendering),
// railModel.ts (lane visibility) and validateDirector's timeline branch
// (stale-document reasons). Every model family reduces to the same rule: flf
// ⇒ both edges locked; i2v ⇒ leading edge locked; free placement ⇒ an open
// lane; t2v-only ⇒ nothing.

export interface DirectorEdgeAllowances {
	/** Whether a keyframe may be placed away from a shot's own edges -- also
	 * what gates the free keyframes lane. Chain routing reads this off
	 * `director.keyframes === 'anywhere'`; timeline routing reads it off
	 * `director` being declared at all -- a real LTX preset declares
	 * `max_keyframes` with no `keyframes` field, which parses to 'none';
	 * gating timeline free placement on `keyframes === 'anywhere'` would hide
	 * every well/lane LTX has today (docs/video-director.md: timeline
	 * keyframes are "Always" legal, capped only by `max_keyframes` -- that
	 * vocabulary is chain-style and timeline never reads it). */
	freePlacementAllowed: boolean;
	/** Whether a shot's own leading edge may offer a well. Chain routing keeps
	 * its existing capability-only rule (`keyframes: 'first_only'|'anywhere'`,
	 * first shot only -- the caller still enforces the shot-index check; a
	 * chain mode that merely DECLARES `i2v`/`flf` without opening `keyframes`,
	 * e.g. MiniMax-H3's `refs` override, still offers no well). Timeline
	 * routing also opens the edge when `i2v` or `flf` is declared, or free
	 * placement is on. */
	leadingEdgeAllowed: boolean;
	/** Whether a shot's own trailing edge may offer a well: `flf` declared or
	 * free placement, for either routing. Chain routing additionally
	 * restricts WHERE this can render -- single-shot chain only, since a
	 * `ChainSegment` has no field for a trailing keyframe at all (see
	 * stageModel.ts's `chainTrailingGate`). */
	trailingEdgeAllowed: boolean;
}

export function resolveDirectorEdgeAllowances(caps: DirectorCapabilities): DirectorEdgeAllowances {
	const directorCap = caps.modes.director;
	const freePlacementAllowed = caps.segmentRouting ? directorCap?.keyframes === 'anywhere' : directorCap != null;
	const leadingEdgeAllowed = caps.segmentRouting
		? directorCap?.keyframes === 'first_only' || directorCap?.keyframes === 'anywhere'
		: caps.enabledModes.includes('i2v') || caps.enabledModes.includes('flf') || freePlacementAllowed;
	const trailingEdgeAllowed = caps.enabledModes.includes('flf') || freePlacementAllowed;
	return { freePlacementAllowed, leadingEdgeAllowed, trailingEdgeAllowed };
}

// ─── Non-chain timing validation ────────────────────────────────────────────

export interface DirectorTimingResult {
	requestedFrames: number | null;
	/** Null for legacy presets without `limits.max_frames`; those generators do
	 * not opt into the LTX causal-VAE snap contract. */
	frameCount: number | null;
	effectiveDuration: number | null;
	fieldErrors: Partial<Record<'duration' | 'fps', string>>;
}

/**
 * Mirrors `normalize.py::_normalize_settings` for every non-chain director
 * mode. The backend first rounds duration*fps half-up, rejects a raw count over
 * `max_frames`, then (only for a max-frames preset) snaps to 1 + k*8 with ties
 * down. Keeping this pure gives each editor the same preflight result.
 */
export function evaluateDirectorTiming(
	duration: number,
	fps: number,
	limits: { maxDuration: number | null; maxFrames: number | null }
): DirectorTimingResult {
	const fieldErrors: DirectorTimingResult['fieldErrors'] = {};
	const durationValid = Number.isFinite(duration) && duration > 0;
	const fpsValid = Number.isFinite(fps) && fps >= 1 && fps <= 60;

	if (!durationValid) fieldErrors.duration = 'Duration must be a finite number greater than 0.';
	else if (limits.maxDuration != null && duration > limits.maxDuration) {
		fieldErrors.duration = `Duration exceeds maximum of ${limits.maxDuration}s`;
	}
	if (!fpsValid) fieldErrors.fps = `FPS must be between 1 and 60, got ${fps}.`;

	if (!durationValid || !fpsValid || fieldErrors.duration) {
		return { requestedFrames: null, frameCount: null, effectiveDuration: null, fieldErrors };
	}

	const requestedFrames = Math.floor(duration * fps + 0.5);
	if (limits.maxFrames != null) {
		if (requestedFrames > limits.maxFrames) {
			fieldErrors.duration =
				`Duration ${duration} at ${fps} FPS needs ${requestedFrames} frames, exceeding this preset's generator cap of ${limits.maxFrames} frames ` +
				`(use a duration of ${(limits.maxFrames / fps).toFixed(2)}s or less at this FPS, or lower FPS).`;
			return { requestedFrames, frameCount: null, effectiveDuration: null, fieldErrors };
		}

		const latticeIndex = Math.max(0, Math.ceil((requestedFrames - 1) / 8 - 0.5));
		const frameCount = 1 + latticeIndex * 8;
		return { requestedFrames, frameCount, effectiveDuration: frameCount / fps, fieldErrors };
	}

	return { requestedFrames, frameCount: null, effectiveDuration: null, fieldErrors };
}

// ─── Segment sub-type derivation ─────────────────────────────────────────────
// Mirrors src/features/video_director/normalize.py::derive_segment_sub_type
// EXACTLY (same priority order) so the chain editor can show the same badge
// the backend would resolve, without a round-trip. Only reachable when a
// preset declares `segment_routing: true` (Wan chain mode); LTX's director
// mode never sets this and stays untouched.

export function deriveSegmentSubType(params: {
	index: number;
	hasFirstMedia: boolean;
	hasLastMedia: boolean;
	override?: SegmentSubType | null;
}): SegmentSubType {
	const { index, hasFirstMedia, hasLastMedia, override } = params;
	if (override) return override;
	if (hasFirstMedia && hasLastMedia) return 'flf';
	if (hasFirstMedia) return 'i2v';
	if (index === 0) return 't2v';
	return 'chain';
}

// A chain segment's leading/trailing media are per-segment fields, legal on
// ANY segment -- not pinned to index 0/the last segment. WHICH segment may
// actually offer an empty well is a join-aware question `chainSegmentEdgeAllowances`
// answers; these two just read whatever media the segment (at whatever index)
// already carries, exactly mirroring src/features/video_director/normalize.py's
// `derive_segment_sub_type` inputs.
function chainSegmentHasFirstMedia(segment: ChainSegment): boolean {
	return segment.keyframe != null;
}
function chainSegmentHasLastMedia(segment: ChainSegment): boolean {
	return segment.last_keyframe != null;
}

export function deriveChainSegmentSubType(segment: ChainSegment, index: number): SegmentSubType {
	return deriveSegmentSubType({
		index,
		hasFirstMedia: chainSegmentHasFirstMedia(segment),
		hasLastMedia: chainSegmentHasLastMedia(segment),
		override: segment.sub_type_override
	});
}

/** True where the "Continue previous | New shot" toggle applies: a prompt-only
 * segment that would otherwise derive to 'chain'. Ignores any override already
 * set -- this is about whether the ambiguity EXISTS, not which side of it the
 * user picked. */
export function chainSegmentIsAmbiguous(segment: ChainSegment, index: number): boolean {
	return (
		deriveSegmentSubType({
			index,
			hasFirstMedia: chainSegmentHasFirstMedia(segment),
			hasLastMedia: chainSegmentHasLastMedia(segment),
			override: null
		}) === 'chain'
	);
}

// ─── Join-aware chain edge allowances ───────────────────────────────────────
// resolveDirectorEdgeAllowances answers "does this preset admit the leading/
// trailing role AT ALL" (a document-level capability read). For chain routing
// that's not the whole story any more: WHICH segment may carry one is a
// property of the chain's actual topology -- a segment "opens fresh" when it
// does not continue from its predecessor (any join before it is a cut, or
// it's the first segment), and "closes fresh" when its successor does not
// continue from it (any join after it is a cut, or it's the last segment).
//
//   - leading is offered on segment i iff it opens fresh (a post-cut shot IS
//     a first frame in its own generation -- adding one is self-consistent:
//     first media on a segment with no contradicting override always makes
//     `deriveSegmentSubType` resolve it away from 'chain').
//   - trailing is offered on segment i iff it BOTH opens AND closes fresh --
//     narrower than the maintainer's literal "closing join is a cut" because
//     the generator only ever reads a trailing frame paired with a leading
//     one on the SAME segment (`derive_segment_sub_type`'s 'flf' case,
//     generator/chain_video_wan22/main.py's `end = (... if sub_type == "flf"
//     and has_last else None)`); a trailing well on a segment that still
//     continues IN from its predecessor would be a dead knob, so it's never
//     offered there. A CONTINUE join keeps no wells on either side. Trailing
//     ALSO requires `leadingEdgeAllowed` (not just `trailingEdgeAllowed`) --
//     for chain style that flag IS "this mode's own `keyframes` capability
//     opens at all" (`resolveDirectorEdgeAllowances`'s chain formula never
//     ORs in i2v/flf the way the document-level `trailingEdgeAllowed` does),
//     so a mode that merely inherits `flf`/`i2v` from the preset's top-level
//     modes without itself declaring `keyframes: 'first_only'|'anywhere'`
//     (MiniMax-H3's `refs` override: `keyframes: null`) still offers no well
//     on either edge, even once every segment is independently cut
//     (`continuationDisabled`).
//
// Single-shot documents are the degenerate case (i == 0 == N-1, both always
// true), so this exactly reproduces the pre-existing single-shot behaviour.
export interface ChainSegmentEdgeAllowances {
	leading: boolean[];
	trailing: boolean[];
}

export function chainSegmentEdgeAllowances(
	segments: ChainSegment[],
	continuationDisabled: boolean,
	allowances: DirectorEdgeAllowances
): ChainSegmentEdgeAllowances {
	const opensFresh = segments.map(
		(seg, i) => continuationDisabled || deriveChainSegmentSubType(seg, i) !== 'chain'
	);
	const closesFresh = segments.map((_, i) => i === segments.length - 1 || opensFresh[i + 1]);
	return {
		leading: opensFresh.map((fresh) => fresh && allowances.leadingEdgeAllowed),
		trailing: opensFresh.map(
			(fresh, i) => fresh && closesFresh[i] && allowances.leadingEdgeAllowed && allowances.trailingEdgeAllowed
		)
	};
}

/** Rail keyframe ids that mirror a chain segment's own leading/trailing well
 * (stageModel.ts's `chainLeadingGate`/`chainTrailingGate`) -- read-model
 * projections of `segment.keyframe`/`segment.last_keyframe`, not real
 * `chain.keyframes` entries. One pair per segment that currently carries edge
 * media (any segment can, once it opens/closes fresh -- see
 * `chainSegmentEdgeAllowances` above), so the id carries the segment id
 * rather than being fixed. Shared by railModel.ts (mints/reads them) and
 * stageModel.ts (fills/clears them) -- neither owns the other, so the id
 * scheme lives here instead of forcing one to import from the other. */
export function chainEdgeKeyframeId(edge: 'first' | 'last', segmentId: string): string {
	return `chain-edge-${edge}:${segmentId}`;
}

export function parseChainEdgeKeyframeId(id: string): { edge: 'first' | 'last'; segmentId: string } | null {
	const m = /^chain-edge-(first|last):(.+)$/.exec(id);
	if (!m) return null;
	return { edge: m[1] as 'first' | 'last', segmentId: m[2] };
}

export function isChainEdgeKeyframeId(id: string): boolean {
	return parseChainEdgeKeyframeId(id) != null;
}

// ─── Chain keyframe window ───────────────────────────────────────────────────

/**
 * The `[0, window]` range a chain-style keyframe's `at` must fall in. Chain
 * style never validates settings.duration, so `_normalize_media` derives the
 * window from the per-segment frame counts instead — summing the ROUNDED
 * frames, not the raw durations, so a shot of 2.5s at 16 fps contributes 40
 * frames here exactly as it does on the wire. Returns 0 for an unusable fps.
 */
export function chainKeyframeWindow(chain: { fps: number; segments: ChainSegment[] }): number {
	const { fps } = chain;
	if (!Number.isFinite(fps) || fps <= 0) return 0;
	return chain.segments.reduce((sum, s) => sum + Math.round(s.duration * fps), 0) / fps;
}

// ─── Default / normalize ─────────────────────────────────────────────────────

/** Continuity defaults for Wan's director mode, seeded from its capability
 * (falls back to the tail-frames/overlap-4/stitch recipe when undeclared). */
function defaultChainContinuation(caps: DirectorCapabilities): ChainContinuation {
	const c = caps.modes.director?.continuation;
	return { overlap_frames: c?.overlapFrames ?? 4, stitch: c?.stitch ?? true };
}

export function createDefaultDirectorValue(caps: DirectorCapabilities): VideoDirectorValue {
	const mode = caps.enabledModes[0] ?? 't2v';
	const duration = caps.defaultDuration;
	const fps = caps.defaultFps;
	const directorCap = caps.modes.director;

	return {
		schema_version: 1,
		mode,
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration, fps, start_image: null, first_frame: null, last_frame: null },
		timeline: { duration, fps, segments: [], keyframes: [], audio: [], ic_lora: [] },
		chain: {
			fps,
			segments: [
				{
					id: DEFAULT_CHAIN_SEGMENT_ID,
					prompt: '',
					prompt_segments: [],
					duration: directorCap?.defaultSegmentDuration ?? duration,
					loras: null,
					keyframe: null,
					keyframe_strength: 1,
					last_keyframe: null,
					last_keyframe_strength: 1,
					sub_type_override: null
				}
			],
			continuation: defaultChainContinuation(caps),
			keyframes: [],
			audio: []
		}
	};
}

/** Defensively re-shapes a possibly-stale/garbage stored value. Idempotent. */
export function normalizeDirectorValue(raw: unknown, caps: DirectorCapabilities): VideoDirectorValue {
	const def = createDefaultDirectorValue(caps);
	const r = isRecord(raw) ? raw : {};

	// Lenient read of pre-director stored values: the retired `chain` mode is the
	// routed `director` mode now. Remap before validating against enabledModes.
	const rawMode = r.mode === 'chain' ? 'director' : r.mode;
	const mode: DirectorMode =
		typeof rawMode === 'string' && (caps.enabledModes as string[]).includes(rawMode) ? (rawMode as DirectorMode) : def.mode;

	const simpleR = isRecord(r.simple) ? r.simple : {};
	const simple: SimpleComposition = {
		duration: num(simpleR.duration, def.simple.duration),
		fps: num(simpleR.fps, def.simple.fps),
		start_image: normMedia(simpleR.start_image),
		first_frame: normMedia(simpleR.first_frame),
		last_frame: normMedia(simpleR.last_frame)
	};

	const tlR = isRecord(r.timeline) ? r.timeline : {};
	const segments: DirectorPromptSegment[] = Array.isArray(tlR.segments)
		? tlR.segments
				.filter(isRecord)
				.filter((s) => typeof s.id === 'string' && (typeof s.text === 'string' || Array.isArray(s.prompt_segments)))
				.map((s) => {
					const legacyText = str(s.text);
					const promptSegments = normPromptSegments(s.prompt_segments, legacyText, `${s.id}-prompt-0`);
					const references = normSegmentReferences(s.references);
					return {
						id: s.id as string,
						start: num(s.start, 0),
						end: num(s.end, 0),
						text: Array.isArray(s.prompt_segments) ? resolvePromptSegments(promptSegments) : legacyText,
						prompt_segments: promptSegments,
						...(references ? { references } : {})
					};
				})
		: [];
	const keyframes: DirectorKeyframe[] = Array.isArray(tlR.keyframes)
		? tlR.keyframes
				.filter(isRecord)
				.filter((k) => typeof k.id === 'string' && (k.role === 'first' || k.role === 'last' || k.role === 'free'))
				.map((k) => ({
					id: k.id as string,
					start: num(k.start, 0),
					role: k.role as DirectorKeyframe['role'],
					strength: num(k.strength, 1),
					media: normMedia(k.media)
				}))
		: [];
	// `role` is preserved only where the stored entry already carries one: a
	// timeline director that never showed the control must keep normalizing to
	// exactly the document (and wire audio entry) it produced before the role
	// existed.
	const audio: DirectorAudioSegment[] = Array.isArray(tlR.audio)
		? tlR.audio
				.filter(isRecord)
				.filter((a) => typeof a.id === 'string')
				.map((a) => {
					const role = normAudioRole(a.role);
					return {
						id: a.id as string,
						start: num(a.start, 0),
						trim_start: num(a.trim_start, 0),
						length: num(a.length, 0),
						media: normMedia(a.media),
						...(role ? { role } : {})
					};
				})
		: [];
	const icLora: DirectorIcLoraEntry[] = Array.isArray(tlR.ic_lora)
		? tlR.ic_lora
				.filter(isRecord)
				.filter((e) => typeof e.id === 'string')
				.map((e) => ({
					id: e.id as string,
					lora: normLoraRef(e.lora),
					ref_media: normMedia(e.ref_media),
					strength: num(e.strength, 1)
				}))
		: [];

	const timeline: DirectorTimelineDoc = {
		duration: num(tlR.duration, def.timeline.duration),
		fps: num(tlR.fps, def.timeline.fps),
		segments,
		keyframes,
		audio,
		ic_lora: icLora
	};

	const chainR = isRecord(r.chain) ? r.chain : {};
	const chainSegs: ChainSegment[] = Array.isArray(chainR.segments)
		? chainR.segments
				.filter(isRecord)
				.filter((s) => typeof s.id === 'string' && (typeof s.prompt === 'string' || Array.isArray(s.prompt_segments)))
				.map((s, i) => {
					const legacyPrompt = str(s.prompt);
					const promptSegments = normPromptSegments(s.prompt_segments, legacyPrompt, `${s.id}-prompt-0`);
					const references = normSegmentReferences(s.references);
					const seg: ChainSegment = {
						id: s.id as string,
						prompt: Array.isArray(s.prompt_segments) ? resolvePromptSegments(promptSegments) : legacyPrompt,
						prompt_segments: promptSegments,
						duration: num(s.duration, caps.modes.director?.defaultSegmentDuration ?? def.simple.duration),
						loras: normLoraStacks(s.loras),
						keyframe: normMedia(s.keyframe),
						keyframe_strength: num(s.keyframe_strength, 1),
						last_keyframe: normMedia(s.last_keyframe),
						last_keyframe_strength: num(s.last_keyframe_strength, 1),
						sub_type_override: s.sub_type_override === 't2v' ? 't2v' : null,
						...(references ? { references } : {})
					};
					// Reordering or gaining edge media can leave a stale override behind
					// (it only ever makes sense on an ambiguous, prompt-only segment) --
					// drop it rather than persist a value that would never be sent to the
					// backend anyway.
					if (!chainSegmentIsAmbiguous(seg, i)) seg.sub_type_override = null;
					return seg;
				})
		: [];
	const chainKeyframes: ChainKeyframe[] = Array.isArray(chainR.keyframes)
		? chainR.keyframes
				.filter(isRecord)
				.filter((k) => typeof k.id === 'string')
				.map((k) => ({
					id: k.id as string,
					at: num(k.at, 0),
					strength: num(k.strength, 1),
					media: normMedia(k.media)
				}))
		: [];
	// The chain's own audio tracks always carry an explicit role so the editor's
	// select has a value to bind; the timeline's stay role-less unless stored.
	const chainAudio: DirectorAudioSegment[] = Array.isArray(chainR.audio)
		? chainR.audio
				.filter(isRecord)
				.filter((a) => typeof a.id === 'string')
				.map((a) => ({
					id: a.id as string,
					role: normAudioRole(a.role) ?? 'condition',
					start: num(a.start, 0),
					trim_start: num(a.trim_start, 0),
					length: num(a.length, 0),
					media: normMedia(a.media)
				}))
		: [];
	const contR = isRecord(chainR.continuation) ? chainR.continuation : {};
	const chain = {
		fps: num(chainR.fps, def.chain.fps),
		segments: chainSegs.length > 0 ? chainSegs : def.chain.segments,
		continuation: {
			overlap_frames: num(contR.overlap_frames, def.chain.continuation.overlap_frames),
			stitch: typeof contR.stitch === 'boolean' ? contR.stitch : def.chain.continuation.stitch
		},
		keyframes: chainKeyframes,
		audio: chainAudio
	};

	const ui: VideoDirectorUiState | undefined = isRecord(r.ui) ? (r.ui as VideoDirectorUiState) : undefined;

	const legacyGlobalPrompt = str(r.global_prompt);
	const globalPromptSegments = normPromptSegments(r.global_prompt_segments, legacyGlobalPrompt, 'global-prompt-0');
	const globalPrompt = Array.isArray(r.global_prompt_segments) ? resolvePromptSegments(globalPromptSegments) : legacyGlobalPrompt;
	const legacyNegativePrompt = str(r.negative_prompt);
	const negativePromptSegments = normPromptSegments(r.negative_prompt_segments, legacyNegativePrompt, 'negative-prompt-0');
	const negativePrompt = Array.isArray(r.negative_prompt_segments)
		? resolvePromptSegments(negativePromptSegments)
		: legacyNegativePrompt;

	return {
		schema_version: 1,
		mode,
		global_prompt: globalPrompt,
		global_prompt_segments: globalPromptSegments,
		negative_prompt: negativePrompt,
		negative_prompt_segments: negativePromptSegments,
		simple,
		timeline,
		chain,
		...(ui ? { ui } : {})
	};
}

// ─── Default-document detection ──────────────────────────────────────────────
// Submission usage-gate: whether `form_data.video_director` should be attached
// to a generation request at all. `videoDirectorActive` (generate/+page.svelte)
// only says the preset+mode HAS Director capability, not that the user did
// anything with it -- a document is meaningful when it was either touched this
// session (see Tab.videoDirectorTouched) or, for a session restored from a
// prior save, differs from what a fresh editor would start with.

function omitUiState(value: VideoDirectorValue): Omit<VideoDirectorValue, 'ui'> {
	const rest: Partial<VideoDirectorValue> = { ...value };
	delete rest.ui;
	return rest as Omit<VideoDirectorValue, 'ui'>;
}

/**
 * True when `value` carries nothing beyond what `createDefaultDirectorValue(caps)`
 * itself would produce -- the document a fresh, never-edited editor holds.
 * `value` is normalized first so a stale/garbage stored value compares against
 * the same canonical shape the default does, rather than its own raw one.
 * `ui` (zoom/panel-collapse) is excluded: it's view state that
 * never reaches the wire and must never make an otherwise-untouched document
 * look "used".
 */
export function isDefaultDirectorDocument(value: unknown, caps: DirectorCapabilities): boolean {
	const normalized = omitUiState(normalizeDirectorValue(value, caps));
	const def = omitUiState(createDefaultDirectorValue(caps));
	return JSON.stringify(normalized) === JSON.stringify(def);
}

/**
 * One-time migration for a preset mode that only just gained Director: the
 * document (`Tab.videoDirector`) is a field of its own, never routed through
 * `modeState.ts`'s per-mode prompt cache, so when a mode goes from a plain
 * prompt editor to Director-active (H3's `refs` did exactly this), whatever
 * text a tab already carries in its plain prompt fields has nowhere to land
 * in the fresh/default document the editor mounts with -- the editor renders
 * empty, `validateDirector` reports "Missing prompt", and Generate goes dead
 * with the user's real prompt sitting invisible in a field the UI no longer
 * shows.
 *
 * Mirrors `toModelessDirectorValue`'s `simple.*` media promotion: only fires
 * on a document that is still exactly `createDefaultDirectorValue(caps)`
 * (`isDefaultDirectorDocument`), so it never resurrects a prompt onto a
 * document the user has already started shaping, and returns `null` (not the
 * unchanged document) once the migration has nothing left to do -- the
 * seeded prompt is itself what makes the document non-default, so the caller
 * naturally stops re-triggering it after the first write.
 */
export function seedDirectorPromptFromLegacyText(
	value: VideoDirectorValue | undefined,
	caps: DirectorCapabilities,
	legacyText: string
): VideoDirectorValue | null {
	const text = legacyText.trim();
	if (!text) return null;
	const normalized = normalizeDirectorValue(value, caps);
	if (!isDefaultDirectorDocument(normalized, caps)) return null;

	if (caps.segmentRouting) {
		const [firstSeg, ...restSegs] = normalized.chain.segments;
		const seg: ChainSegment = {
			...firstSeg,
			prompt: text,
			prompt_segments: normPromptSegments(undefined, text, `${firstSeg.id}-prompt-0`)
		};
		return { ...normalized, chain: { ...normalized.chain, segments: [seg, ...restSegs] } };
	}

	const segment: DirectorPromptSegment = {
		id: DEFAULT_TIMELINE_SEGMENT_ID,
		start: 0,
		end: normalized.timeline.duration,
		text,
		prompt_segments: normPromptSegments(undefined, text, `${DEFAULT_TIMELINE_SEGMENT_ID}-prompt-0`)
	};
	return { ...normalized, timeline: { ...normalized.timeline, segments: [segment] } };
}

// ─── Modeless mode derivation ────────────────────────────────────────────────
// The Stage & Rail editor has no mode switch: `chain`/`timeline` are the only
// things the user edits, and `mode` becomes a derived READ of their structure
// -- kept on the document only because the wire contract and chat tooling
// still key off it (docs/video-director.md's four-mode `mode` field).

/** Reads whether the single shot this document represents (if any) carries a
 * leading/trailing frame, independent of routing -- `null` when the document
 * isn't shaped like a single, unadorned shot at all. */
function singleShotEdges(
	value: VideoDirectorValue,
	caps: DirectorCapabilities
): { leading: boolean; trailing: boolean } | null {
	if (caps.segmentRouting) {
		const { segments, keyframes, audio } = value.chain;
		if (segments.length !== 1 || keyframes.length > 0 || audio.length > 0) return null;
		return { leading: segments[0].keyframe != null, trailing: segments[0].last_keyframe != null };
	}
	const { segments, keyframes, audio, ic_lora } = value.timeline;
	if (segments.length > 1 || audio.length > 0 || ic_lora.length > 0) return null;
	if (keyframes.some((k) => k.role === 'free')) return null;
	const first = keyframes.find((k) => k.role === 'first');
	const last = keyframes.find((k) => k.role === 'last');
	if (keyframes.length > (first ? 1 : 0) + (last ? 1 : 0)) return null;
	return { leading: first?.media != null, trailing: last?.media != null };
}

/**
 * Derives the wire `mode` from the document's actual structure rather than a
 * stored switch: a single, unadorned shot with no edge media is `t2v`, with
 * only a leading frame is `i2v`, with both edges is `flf` (only when the
 * preset's capabilities enable `flf` -- otherwise a document that HAPPENS to
 * carry both edges stays `director`, since there's nowhere legal to send an
 * `flf` wire doc). Anything with more than one shot, or any timed extra
 * (keyframes beyond the two edges, audio, ic_lora), is `director`.
 *
 * Projects through `toModelessDirectorValue` first: a document that still
 * carries a legacy `simple.*` composition (its edge media hasn't been folded
 * into `chain`/`timeline` yet) would otherwise read as a bare, media-less
 * shot here -- `singleShotEdges` only ever looks at `chain`/`timeline`.
 */
export function deriveDirectorMode(rawValue: VideoDirectorValue, caps: DirectorCapabilities): DirectorMode {
	const value = toModelessDirectorValue(rawValue, caps);
	const edges = singleShotEdges(value, caps);
	if (!edges) return 'director';
	if (edges.leading && edges.trailing) return caps.enabledModes.includes('flf') ? 'flf' : 'director';
	if (edges.leading) return caps.enabledModes.includes('i2v') ? 'i2v' : 'director';
	if (edges.trailing) return 'director'; // trailing with no leading has no legacy shape
	return 't2v';
}

/** The single shot `buildDirectorSubmission`/`validateDirector` read for a
 * derived t2v/i2v/flf document -- duration/fps/edge media pulled from
 * whichever of `chain`/`timeline` routing actually reads, not from `simple`
 * (which `toModelessDirectorValue` never keeps in sync with further edits).
 * The one exception is chain routing's trailing edge: `ChainSegment` has no
 * field for it, so it's the one place `simple.last_frame` is still read as
 * live storage rather than a legacy source. */
interface SingleShot {
	duration: number;
	fps: number;
	leading: DirectorMediaValue | null;
	trailing: DirectorMediaValue | null;
	/** The shot's own prompt text, joined onto the global prompt exactly as
	 * `director` mode already joins its first segment's -- see
	 * `directorSegmentPrompt` below. Empty for a document migrated by
	 * `toModelessDirectorValue` from a legacy `simple.*` composition, which
	 * never had a per-shot prompt field of its own. */
	promptText: string;
	/** Per-shot reference-pool selection -- the `references` capability is
	 * top-level, not `director`-only, so a t2v/i2v/flf single shot reads/emits
	 * it exactly like a multi-shot chain/timeline segment does. */
	references?: SegmentReference[];
}

function extractSingleShot(value: VideoDirectorValue, caps: DirectorCapabilities): SingleShot {
	if (caps.segmentRouting) {
		const seg = value.chain.segments[0];
		return {
			duration: seg?.duration ?? value.simple.duration,
			fps: value.chain.fps,
			leading: seg?.keyframe ?? null,
			trailing: seg?.last_keyframe ?? null,
			promptText: seg?.prompt ?? '',
			references: seg?.references
		};
	}
	const first = value.timeline.keyframes.find((k) => k.role === 'first');
	const last = value.timeline.keyframes.find((k) => k.role === 'last');
	return {
		duration: value.timeline.duration,
		fps: value.timeline.fps,
		leading: first?.media ?? null,
		trailing: last?.media ?? null,
		promptText: value.timeline.segments[0]?.text ?? '',
		references: value.timeline.segments[0]?.references
	};
}

/** What a legacy t2v/i2v/flf document's `simple.*` projects onto a single
 * shot's edges, before routing decides where that lands. */
function legacySimpleEdges(value: VideoDirectorValue): { leading: DirectorMediaValue | null; trailing: DirectorMediaValue | null } {
	switch (value.mode) {
		case 'i2v':
			return { leading: value.simple.start_image, trailing: null };
		case 'flf':
			return { leading: value.simple.first_frame, trailing: value.simple.last_frame };
		default:
			return { leading: null, trailing: null };
	}
}

/**
 * Projects a legacy t2v/i2v/flf document's `simple.*` composition into the
 * unified `chain`/`timeline` shape the Stage & Rail editor actually reads and
 * edits -- so a document that predates the modeless rework (or one a chat
 * `set_mode`/`upsert_media` op still writes, since `applyDirectorOperations`
 * is unchanged and still targets `simple.*` for i2v/flf) displays and edits
 * exactly like a document built from scratch in the new editor. A `director`
 * document is already unified; this only ever touches `chain.segments[0]`
 * (segment-routed) or `timeline.segments`/`timeline.keyframes`
 * (timeline-routed) for the other three.
 *
 * Two passes, each idempotent for a different reason:
 *
 * 1. A one-time STRUCTURAL seed (chain/timeline's placeholder shot, and its
 *    duration/fps from `simple`), gated by `ui.modeless` and never repeated.
 *    It must not re-run: past the first pass, Rail's own fps/duration inputs
 *    (and the chat `set_settings` op) write duration/fps into `chain`/
 *    `timeline` directly, with no accompanying signal that `simple.duration`/
 *    `simple.fps` (never touched again after this) are now stale -- a
 *    repeat seed would silently revert a Rail edit back to whatever `simple`
 *    happened to hold at migration time.
 * 2. A continuous MEDIA fold-in that is safe to re-run on every call: it only
 *    ever acts while `simple`'s corresponding field is non-null, and always
 *    clears that field the moment it folds it in -- so it self-terminates
 *    (a second pass sees null and no-ops) without needing a marker, and it
 *    never resurrects a value the modeless editor already cleared via Stage
 *    (which writes `chain`/`timeline` directly and never touches `simple`,
 *    so by the time such a clear happens `simple`'s field is already null).
 *    It also means a chat `upsert_media` landing on an already-migrated
 *    document (the realistic case -- chat and the editor share the same
 *    persisted value) still gets picked up: it makes `simple`'s field
 *    non-null again, which this happily folds in on the next pass.
 *
 * Chain routing's flf trailing edge is the one exception to "fold in and
 * clear": `ChainSegment` has no field for it (see `extractSingleShot`), so
 * `simple.last_frame` IS its storage, permanently, for that one combination.
 */
export function toModelessDirectorValue(value: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	if (value.mode === 'director') {
		return value.ui?.modeless ? value : { ...value, ui: { ...value.ui, modeless: true } };
	}

	let next = value;

	if (!next.ui?.modeless) {
		const { duration, fps } = next.simple;
		if (caps.segmentRouting) {
			const [firstSeg, ...restSegs] = next.chain.segments;
			const seg: ChainSegment = firstSeg
				? { ...firstSeg, duration }
				: {
						id: DEFAULT_CHAIN_SEGMENT_ID,
						prompt: '',
						prompt_segments: [],
						duration,
						loras: null,
						keyframe: null,
						keyframe_strength: 1,
						last_keyframe: null,
						last_keyframe_strength: 1,
						sub_type_override: null
					};
			next = { ...next, chain: { ...next.chain, fps, segments: [seg, ...restSegs] } };
		} else {
			const segments: DirectorPromptSegment[] =
				next.timeline.segments.length > 0
					? next.timeline.segments
					: [{ id: DEFAULT_TIMELINE_SEGMENT_ID, start: 0, end: duration, text: '', prompt_segments: [] }];
			next = { ...next, timeline: { ...next.timeline, duration, fps, segments } };
		}
	}

	const { leading, trailing } = legacySimpleEdges(next);

	if (leading != null) {
		if (caps.segmentRouting) {
			const [firstSeg, ...restSegs] = next.chain.segments;
			if (firstSeg) {
				next = { ...next, chain: { ...next.chain, segments: [{ ...firstSeg, keyframe: leading, keyframe_strength: 1 }, ...restSegs] } };
			}
		} else {
			const keyframes = next.timeline.keyframes.filter((k) => k.role !== 'first');
			keyframes.push({ id: 'kf-first', start: 0, role: 'first', strength: 1, media: leading });
			next = { ...next, timeline: { ...next.timeline, keyframes } };
		}
		next = { ...next, simple: { ...next.simple, start_image: null, first_frame: null } };
	}

	if (trailing != null) {
		if (caps.segmentRouting) {
			const [firstSeg, ...restSegs] = next.chain.segments;
			if (firstSeg) {
				next = {
					...next,
					chain: { ...next.chain, segments: [{ ...firstSeg, last_keyframe: trailing, last_keyframe_strength: 1 }, ...restSegs] }
				};
			}
		} else {
			const keyframes = next.timeline.keyframes.filter((k) => k.role !== 'last');
			keyframes.push({ id: 'kf-last', start: next.timeline.duration, role: 'last', strength: 1, media: trailing });
			next = { ...next, timeline: { ...next.timeline, keyframes } };
		}
		next = { ...next, simple: { ...next.simple, last_frame: null } };
	}

	return next.ui?.modeless ? next : { ...next, ui: { ...next.ui, modeless: true } };
}

// ─── Validation ───────────────────────────────────────────────────────────────

/**
 * Checks every segment's per-shot reference selection against the preset's
 * top-level `references` capability: under 'per_shot', a `form_media` entry
 * must point at one of the declared `reference_fields`; under 'whole'/null no
 * segment may carry a selection at all (the wire field would have nowhere
 * legal to land -- mirrors the audio/keyframes "not supported in this mode"
 * checks above, and normalize.py's `_normalize_segment_references`). A plain
 * `{path}` reference is never checked against `reference_fields` -- only a
 * form pointer's field name is validated here.
 */
function validateSegmentReferences(
	segments: { references?: SegmentReference[] }[],
	caps: DirectorCapabilities,
	reasons: string[]
): void {
	if (caps.references === 'per_shot') {
		const badField = segments
			.flatMap((s) => s.references ?? [])
			.find((ref) => isSegmentFormMediaReference(ref) && !caps.referenceFields.includes(ref.form_media.field));
		if (badField) reasons.push("A per-shot reference points at a field this mode doesn't declare as a reference field");
	} else if (segments.some((s) => s.references && s.references.length > 0)) {
		reasons.push('Per-shot references are not supported in this mode');
	}
}

/**
 * Validates an editor value against its capabilities. Projects through
 * `toModelessDirectorValue` first: a document that never passed through the
 * Stage & Rail editor (a chat tool's `applyDirectorOperations` writes
 * `simple.*` directly for i2v/flf, same as the pre-modeless editor did) still
 * needs its derived mode's single shot read from `chain`/`timeline` here,
 * where `deriveDirectorMode` and every other reader of this document look.
 */
export function validateDirector(
	rawValue: VideoDirectorValue,
	caps: DirectorCapabilities
): { ok: boolean; reasons: string[] } {
	const value = toModelessDirectorValue(rawValue, caps);
	const reasons: string[] = [];
	const mode = deriveDirectorMode(value, caps);
	const cap = caps.modes[mode];
	const addTimingReasons = (duration: number, fps: number) => {
		const timing = evaluateDirectorTiming(duration, fps, {
			maxDuration: caps.maxDuration,
			maxFrames: caps.maxFrames
		});
		for (const error of Object.values(timing.fieldErrors)) reasons.push(error);
	};

	switch (mode) {
		case 't2v': {
			const shot = extractSingleShot(value, caps);
			addTimingReasons(shot.duration, shot.fps);
			if (!value.global_prompt.trim() && !shot.promptText.trim()) reasons.push('Missing prompt');
			validateSegmentReferences([shot], caps, reasons);
			break;
		}
		case 'i2v': {
			const shot = extractSingleShot(value, caps);
			addTimingReasons(shot.duration, shot.fps);
			if (!shot.leading) reasons.push('Missing start image');
			if (!value.global_prompt.trim() && !shot.promptText.trim()) reasons.push('Missing prompt');
			validateSegmentReferences([shot], caps, reasons);
			break;
		}
		case 'flf': {
			const shot = extractSingleShot(value, caps);
			addTimingReasons(shot.duration, shot.fps);
			if (!shot.leading) reasons.push('Missing first frame');
			if (!shot.trailing) reasons.push('Missing last frame');
			if (!value.global_prompt.trim() && !shot.promptText.trim()) reasons.push('Missing prompt');
			validateSegmentReferences([shot], caps, reasons);
			break;
		}
		case 'director': {
			if (caps.segmentRouting) {
				const fps = value.chain.fps;
				if (!Number.isFinite(fps) || fps < 1 || fps > 60) {
					reasons.push(`FPS must be between 1 and 60, got ${fps}.`);
				}
				// Wan's routed multi-shot chain.
				const segs = value.chain.segments;
				if (segs.length === 0) reasons.push('At least one segment is required');
				if (segs.some((s) => !s.prompt.trim())) reasons.push('Every segment needs a prompt');
				if (cap?.maxSegments != null && segs.length > cap.maxSegments) {
					reasons.push(`Too many segments (max ${cap.maxSegments})`);
				}
				// A leading frame is join-aware, not index-pinned: any segment may
				// carry its own (chainSegmentEdgeAllowances) once it opens fresh --
				// the editor's own well only ever renders where that's already
				// true, so there's nothing further to check per-segment here.
				if (!(cap?.keyframes === 'first_only' || cap?.keyframes === 'anywhere') && segs.some((s) => s.keyframe)) {
					reasons.push('Keyframes are not supported in this mode');
				}
				// A trailing frame mirrors normalize.py's dead-knob guard: it's only
				// ever consumed paired with a leading frame on the SAME segment
				// (that combination resolves to 'flf' -- derive_segment_sub_type),
				// so an unpaired one is rejected rather than silently dropped.
				const trailingCapabilityAllowed = cap?.keyframes === 'anywhere' || caps.enabledModes.includes('flf');
				if (!trailingCapabilityAllowed && segs.some((s) => s.last_keyframe)) {
					reasons.push('Trailing frames are not supported in this mode');
				} else if (segs.some((s) => s.last_keyframe && !s.keyframe)) {
					reasons.push('A trailing frame needs a leading frame on the same segment to have any effect');
				}
				if (cap?.keyframes === 'anywhere') {
					const placed = value.chain.keyframes;
					const maxKeyframes = cap.maxKeyframes ?? DEFAULT_MAX_KEYFRAMES;
					const window = chainKeyframeWindow(value.chain);
					if (placed.some((k) => !k.media)) reasons.push('Keyframe missing media');
					if (placed.length > maxKeyframes) reasons.push(`Too many keyframes (max ${maxKeyframes})`);
					if (placed.some((k) => k.at < 0 || k.at > window)) {
						reasons.push(`Every keyframe must sit between 0s and ${window.toFixed(2)}s`);
					}
				} else if (value.chain.keyframes.length > 0) {
					reasons.push('Timed keyframes are not supported in this mode');
				}
				if (cap?.audio) {
					if (value.chain.audio.some((a) => !a.media)) reasons.push('Audio track missing media');
				} else if (value.chain.audio.length > 0) {
					reasons.push('Audio is not supported in this mode');
				}
				if (
					cap?.maxOverlapFrames != null &&
					value.chain.continuation.overlap_frames > cap.maxOverlapFrames
				) {
					reasons.push(`Overlap frames exceed this mode's maximum of ${cap.maxOverlapFrames}`);
				}
				if (!cap?.perSegmentLoras && segs.some((s) => s.loras)) {
					reasons.push('Per-segment LoRAs are not supported in this mode');
				}
				validateSegmentReferences(segs, caps, reasons);
			} else {
				// LTX's single keyframe/audio timeline generation.
				addTimingReasons(value.timeline.duration, value.timeline.fps);
				const hasSegmentText = value.timeline.segments.some((s) => s.text.trim());
				if (!value.global_prompt.trim() && !hasSegmentText) reasons.push('Missing prompt');
				if (value.timeline.keyframes.some((k) => !k.media)) reasons.push('Keyframe missing media');
				if (value.timeline.audio.some((a) => !a.media)) reasons.push('Audio segment missing media');
				if (value.timeline.ic_lora.some((e) => !e.lora)) reasons.push('IC-LoRA entry missing a LoRA');
				if (cap?.maxKeyframes != null && value.timeline.keyframes.length > cap.maxKeyframes) {
					reasons.push(`Too many keyframes (max ${cap.maxKeyframes})`);
				}
				// A stale/foreign document (a preset switch, or a chat op run
				// against an older capability set) can carry keyframes an edge this
				// mode no longer opens -- surface the same human-readable style the
				// chain branch above uses rather than letting the backend reject it.
				const edgeAllowances = resolveDirectorEdgeAllowances(caps);
				if (!edgeAllowances.leadingEdgeAllowed && value.timeline.keyframes.some((k) => k.role === 'first')) {
					reasons.push('This mode has no start-frame slot');
				}
				if (!edgeAllowances.trailingEdgeAllowed && value.timeline.keyframes.some((k) => k.role === 'last')) {
					reasons.push('This mode has no end-frame slot');
				}
				if (!edgeAllowances.freePlacementAllowed && value.timeline.keyframes.some((k) => k.role === 'free')) {
					reasons.push('Free keyframe placement is not supported in this mode');
				}
				validateSegmentReferences(value.timeline.segments, caps, reasons);
			}
			break;
		}
	}

	return { ok: reasons.length === 0, reasons };
}

// ─── Editor → wire mapping ────────────────────────────────────────────────────

function joinPrompt(globalPrompt: string, segmentText: string): string {
	const g = globalPrompt.trim();
	const s = segmentText.trim();
	if (!g) return s;
	if (!s) return g;
	return `${g}. ${s}`;
}

// Director mode maps its N wire segments into ONE joined LTX prompt on the
// backend, so prefixing the global prompt onto every segment (chain's rule)
// would repeat it N times. Only the first segment carries the global prefix;
// later segments carry their own text as-is (chain mode is unaffected — it
// stays on joinPrompt, since each chain segment is an independent generation
// where the per-segment prefix is intended).
function directorSegmentPrompt(globalPrompt: string, segmentText: string, isFirst: boolean): string {
	return isFirst ? joinPrompt(globalPrompt, segmentText) : segmentText.trim();
}

// A segment's `references` is already the exact wire shape (see SegmentReference's
// doc comment) -- only ever populated under the top-level 'per_shot' capability
// ('whole'/null never emit the field, the pool is implicit from the form), and
// an empty/absent per-shot selection means "the whole pool" for that one shot,
// so it stays absent too rather than serializing as `[]`.
function wireSegmentReferences(
	references: SegmentReference[] | undefined,
	caps: DirectorCapabilities
): SegmentReference[] | undefined {
	if (caps.references !== 'per_shot' || !references || references.length === 0) return undefined;
	return references;
}

function emptyWireSegment(
	id: string,
	prompt: string,
	negativePrompt: string,
	start: number | null,
	end: number | null,
	references?: SegmentReference[]
): WireSegment {
	return {
		id,
		prompt,
		negative_prompt: negativePrompt,
		start,
		end,
		frames: null,
		seed: null,
		steps: null,
		cfg: null,
		loras: null,
		...(references ? { references } : {})
	};
}

/**
 * Builds `form_data.video_director` from an editor value. Projects through
 * `toModelessDirectorValue` first (see `validateDirector`'s docstring -- same
 * reasoning), then derives `mode` from the resulting document's structure
 * (`deriveDirectorMode`) rather than trusting `value.mode`, which is only as
 * fresh as the last place something wrote it back (VideoDirectorEditor.svelte
 * keeps it coherent on every edit, but a caller reaching this directly, e.g. a
 * chat-produced document, has no such guarantee).
 */
export function buildDirectorSubmission(rawValue: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorWireDoc {
	const value = toModelessDirectorValue(rawValue, caps);
	const mode = deriveDirectorMode(value, caps);
	switch (mode) {
		case 't2v': {
			const shot = extractSingleShot(value, caps);
			const prompt = joinPrompt(value.global_prompt, shot.promptText);
			return {
				schema_version: 1,
				mode: 't2v',
				settings: { fps: shot.fps, duration: shot.duration, seed: -1 },
				segments: [
					emptyWireSegment('seg-1', prompt, value.negative_prompt, 0, shot.duration, wireSegmentReferences(shot.references, caps))
				],
				media: [],
				audio: [],
				ic_lora: []
			};
		}
		case 'i2v': {
			const shot = extractSingleShot(value, caps);
			const prompt = joinPrompt(value.global_prompt, shot.promptText);
			const media: WireMedia[] = shot.leading
				? [{ id: 'm-1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1, media: shot.leading }]
				: [];
			return {
				schema_version: 1,
				mode: 'i2v',
				settings: { fps: shot.fps, duration: shot.duration, seed: -1 },
				segments: [
					emptyWireSegment('seg-1', prompt, value.negative_prompt, 0, shot.duration, wireSegmentReferences(shot.references, caps))
				],
				media,
				audio: [],
				ic_lora: []
			};
		}
		case 'flf': {
			const shot = extractSingleShot(value, caps);
			const prompt = joinPrompt(value.global_prompt, shot.promptText);
			const media: WireMedia[] = [];
			if (shot.leading) media.push({ id: 'm-1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1, media: shot.leading });
			if (shot.trailing) media.push({ id: 'm-2', role: 'last', segment_id: 'seg-1', at: shot.duration, strength: 1, media: shot.trailing });
			return {
				schema_version: 1,
				mode: 'flf',
				settings: { fps: shot.fps, duration: shot.duration, seed: -1 },
				segments: [
					emptyWireSegment('seg-1', prompt, value.negative_prompt, 0, shot.duration, wireSegmentReferences(shot.references, caps))
				],
				media,
				audio: [],
				ic_lora: []
			};
		}
		case 'director': {
			if (caps.segmentRouting) return buildChainDirectorSubmission(value, caps);
			const { duration, fps } = value.timeline;
			const sorted = sortByStart(value.timeline.segments);
			let wireSegments: WireSegment[] = sorted.map((s, i) => {
				const references = wireSegmentReferences(s.references, caps);
				return {
					id: s.id,
					prompt: directorSegmentPrompt(value.global_prompt, s.text, i === 0),
					negative_prompt: value.negative_prompt,
					start: s.start,
					end: s.end,
					frames: null,
					seed: null,
					steps: null,
					cfg: null,
					loras: null,
					...(references ? { references } : {})
				};
			});
			if (wireSegments.length === 0) {
				wireSegments = [emptyWireSegment('seg-1', value.global_prompt, value.negative_prompt, 0, duration)];
			}
			const firstSegId = wireSegments[0].id;
			const lastSegId = wireSegments[wireSegments.length - 1].id;

			const media: WireMedia[] = [];
			let mIdx = 0;
			for (const kf of value.timeline.keyframes) {
				if (!kf.media) continue;
				mIdx += 1;
				if (kf.role === 'first') {
					media.push({ id: `m-${mIdx}`, role: 'first', segment_id: firstSegId, at: 0, strength: kf.strength, media: kf.media });
				} else if (kf.role === 'last') {
					media.push({ id: `m-${mIdx}`, role: 'last', segment_id: lastSegId, at: duration, strength: kf.strength, media: kf.media });
				} else {
					media.push({ id: `m-${mIdx}`, role: 'keyframe', segment_id: null, at: kf.start, strength: kf.strength, media: kf.media });
				}
			}

			const audio: WireAudio[] = value.timeline.audio
				.filter((a): a is DirectorAudioSegment & { media: DirectorMediaValue } => a.media !== null)
				.map((a) => ({
					id: a.id,
					...(a.role ? { role: a.role } : {}),
					start: a.start,
					trim_start: a.trim_start,
					length: a.length,
					media: a.media
				}));

			const icLora: WireIcLora[] = value.timeline.ic_lora
				.filter((e): e is DirectorIcLoraEntry & { lora: DirectorLoraRef } => e.lora !== null)
				.map((e) => ({ id: e.id, lora: e.lora, reference: e.ref_media, strength: e.strength }));

			return {
				schema_version: 1,
				mode: 'director',
				settings: { fps, duration, seed: -1 },
				segments: wireSegments,
				media,
				audio,
				ic_lora: icLora
			};
		}
	}
}

// Wan's routed multi-shot chain (director mode with segment_routing). Produces
// the same wire shape the retired `chain` mode did -- N segments with per-shot
// frames/loras, a first-only leading keyframe, and chain-wide continuation --
// but under mode "director". Kept separate from the timeline director branch
// because the two families emit deliberately different wire documents.
function buildChainDirectorSubmission(value: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorWireDoc {
	const fps = value.chain.fps;
	const cap = caps.modes.director;
	const totalDuration = value.chain.segments.reduce((sum, s) => sum + s.duration, 0);
	const segments: WireSegment[] = value.chain.segments.map((s, i) => {
		const references = wireSegmentReferences(s.references, caps);
		return {
			id: s.id,
			prompt: joinPrompt(value.global_prompt, s.prompt),
			negative_prompt: value.negative_prompt,
			start: null,
			end: null,
			frames: Math.round(s.duration * fps),
			seed: null,
			steps: null,
			cfg: null,
			loras: s.loras,
			// Only sent on an ambiguous segment where the user actually chose
			// "New shot" -- never the derived value (keeps the document minimal
			// and the backend authoritative). A stale override left over from a
			// reorder or a keyframe add is dropped here even if normalize missed it.
			...(s.sub_type_override && chainSegmentIsAmbiguous(s, i) ? { sub_type: s.sub_type_override } : {}),
			...(references ? { references } : {})
		};
	});
	// Every segment's own leading/trailing frame reaches the wire, not just
	// segment 0 -- any segment may carry one once it opens/closes fresh
	// (chainSegmentEdgeAllowances); normalize.py resolves each independently
	// via derive_segment_sub_type.
	const media: WireMedia[] = [];
	for (const s of value.chain.segments) {
		if (s.keyframe) {
			media.push({ id: `m-${media.length + 1}`, role: 'first', segment_id: s.id, at: 0, strength: s.keyframe_strength, media: s.keyframe });
		}
		if (s.last_keyframe) {
			media.push({
				id: `m-${media.length + 1}`,
				role: 'last',
				segment_id: s.id,
				at: s.duration,
				strength: s.last_keyframe_strength,
				media: s.last_keyframe
			});
		}
	}
	// Placed keyframes and audio tracks only reach the wire while the mode still
	// declares them: a document carrying either after a preset switch would be
	// hard-rejected by the backend rather than silently ignored.
	if (cap?.keyframes === 'anywhere') {
		for (const kf of value.chain.keyframes) {
			if (!kf.media) continue;
			media.push({
				id: `m-${media.length + 1}`,
				role: 'keyframe',
				segment_id: null,
				at: kf.at,
				strength: kf.strength,
				media: kf.media
			});
		}
	}
	const audio: WireAudio[] = cap?.audio
		? value.chain.audio
				.filter((a): a is DirectorAudioSegment & { media: DirectorMediaValue } => a.media !== null)
				.map((a) => ({
					id: a.id,
					role: a.role ?? 'condition',
					start: a.start,
					trim_start: a.trim_start,
					length: a.length,
					media: a.media
				}))
		: [];
	const source = cap?.continuation?.source ?? 'tail_frames';
	// A mode whose capability carries `continuation` EXPLICITLY as null (MiniMax-H3's
	// refs mode) is hard-cut-only -- normalize.py hard-rejects a submitted
	// `settings.continuation` outright in that case, so it must never be sent.
	return {
		schema_version: 1,
		mode: 'director',
		settings: {
			fps,
			duration: totalDuration,
			seed: -1,
			...(cap?.continuationDisabled
				? {}
				: {
						continuation: {
							source,
							overlap_frames: value.chain.continuation.overlap_frames,
							stitch: value.chain.continuation.stitch
						}
					})
		},
		segments,
		media,
		audio,
		ic_lora: []
	};
}

/**
 * Mode-aware human-readable summary of the current prompt(s) — also what
 * `generate/+page.svelte`'s `startGeneration()` sends as `request.prompts[0]
 * .positive`. That field isn't merely cosmetic: a single, edgeless shot
 * derives to `t2v`/`i2v`/`flf` (see `deriveDirectorMode`) rather than
 * `director`, and a preset's pipeline (e.g. MiniMax-H3's `modes/refs/
 * pipeline.yml`) gates its OWN director-aware prompt encoder and generator
 * plan on the wire document's `mode == "director"` — a derived single-shot
 * document falls through to the plain single-window path, which reads
 * `generation.prompts.first.positive`, never the document's own
 * `chain.segments[0].prompt`/`timeline.segments[0].text`. Dropping the shot's
 * own text here (as `global_prompt` alone previously did) silently starves
 * that path of it even though the wire document itself carries it correctly
 * -- every new Director tab starts as exactly this single, edgeless shot.
 * Mirrors `extractSingleShot` + `joinPrompt`, the same join
 * `buildDirectorSubmission`'s own t2v/i2v/flf branches already use.
 */
export function representativeDirectorPrompt(value: VideoDirectorValue, caps: DirectorCapabilities): string {
	switch (deriveDirectorMode(value, caps)) {
		case 't2v':
		case 'i2v':
		case 'flf':
			return joinPrompt(value.global_prompt, extractSingleShot(value, caps).promptText);
		case 'director':
			return caps.segmentRouting
				? [value.global_prompt, ...value.chain.segments.map((s) => s.prompt)].filter((t) => t && t.trim()).join(' | ')
				: [value.global_prompt, ...value.timeline.segments.map((s) => s.text)].filter((t) => t && t.trim()).join(' | ');
	}
}

// ─── Chat operation application ──────────────────────────────────────
// Pure reducer for a Video Director operations array. The only live producer
// is applyDirectorSegmentPrompt below (the update_director_segment tag's
// single-op upsert_segment) -- kept general-purpose since it mirrors the
// document's full wire-op vocabulary, not just that one caller's shape.
// Ids on upserts are pre-assigned by the backend -- this never mints one or
// reads a clock, so replaying the same (value, operations, caps) is
// byte-deterministic. Unknown op types, and known ops touching a field the
// current editor value has no destination for (per-segment seed/steps/cfg,
// project-wide resolution/seed -- buildDirectorSubmission hardcodes seed: -1
// and there is no resolution field yet), are silently skipped rather than
// thrown: forward compatible with a backend that ships fields ahead of the
// editor.

function singleTextSegmentList(text: string, existing: Segment[], fallbackId: string): Segment[] {
	if (!text.trim()) return [];
	const id = existing.length === 1 ? existing[0].id : fallbackId;
	return [{ id, content: text, chips: {}, type: 'content', enabled: true }];
}

function reorderByIds<T extends { id: string }>(items: T[], ids: string[]): T[] {
	const byId = new Map(items.map((item) => [item.id, item] as const));
	const ordered = ids.map((id) => byId.get(id)).filter((item): item is T => item !== undefined);
	const orderedIds = new Set(ordered.map((item) => item.id));
	return [...ordered, ...items.filter((item) => !orderedIds.has(item.id))];
}

function applySetMode(value: VideoDirectorValue, mode: unknown): VideoDirectorValue {
	if (mode === 't2v' || mode === 'i2v' || mode === 'flf' || mode === 'director') {
		return { ...value, mode };
	}
	return value;
}

function applySetSettings(value: VideoDirectorValue, settings: unknown): VideoDirectorValue {
	if (!isRecord(settings)) return value;
	let next = value;
	if (typeof settings.fps === 'number') {
		const fps = settings.fps;
		next = {
			...next,
			simple: { ...next.simple, fps },
			timeline: { ...next.timeline, fps },
			chain: { ...next.chain, fps }
		};
	}
	// Chain mode has no destination for duration -- its total is derived as the
	// sum of segment durations (see buildChainDirectorSubmission), not stored.
	if (typeof settings.duration === 'number') {
		const duration = settings.duration;
		next = { ...next, simple: { ...next.simple, duration }, timeline: { ...next.timeline, duration } };
	}
	return next;
}

// Exported: also the write-back path for the compact Direction/Negative
// rows in VideoDirectorEditor.svelte, so a chat set_prompt op and a manual
// edit produce byte-identical segments for the same text.
export function applySetPrompt(value: VideoDirectorValue, prompt: unknown): VideoDirectorValue {
	if (typeof prompt !== 'string') return value;
	const segments = singleTextSegmentList(prompt, value.global_prompt_segments, 'global-prompt-0');
	return { ...value, global_prompt_segments: segments, global_prompt: resolvePromptSegments(segments) };
}

export function applySetNegativePrompt(value: VideoDirectorValue, negativePrompt: unknown): VideoDirectorValue {
	if (typeof negativePrompt !== 'string') return value;
	const segments = singleTextSegmentList(negativePrompt, value.negative_prompt_segments, 'negative-prompt-0');
	return { ...value, negative_prompt_segments: segments, negative_prompt: resolvePromptSegments(segments) };
}

// The `get_video_director` chat tool reads `segment.references` directly off
// the editor document (same as `sub_type_override`), so an op's entries are
// already the exact `SegmentReference` shape -- `parseSegmentReferenceEntry`
// just defensively narrows them, no translation. Same empty-selection-means-
// "All" rule as `withShotReferences` (stageModel.ts).
function parseOpSegmentReferences(raw: unknown, existing: SegmentReference[] | undefined): SegmentReference[] | undefined {
	if (!Array.isArray(raw)) return existing;
	const parsed = raw.map(parseSegmentReferenceEntry).filter((v): v is SegmentReference => v !== null);
	return parsed.length > 0 ? parsed : undefined;
}

function applyUpsertSegmentChain(value: VideoDirectorValue, raw: unknown, caps: DirectorCapabilities): VideoDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string') return value;
	const id = raw.id;
	const segments = value.chain.segments;
	const idx = segments.findIndex((s) => s.id === id);
	const existing = idx === -1 ? null : segments[idx];
	const fps = value.chain.fps || 1;
	// `duration` (seconds) is the tool's own unit and wins over the `frames` it
	// derived from it, so a shot's length survives a differing editor fps.
	const duration =
		typeof raw.duration === 'number'
			? raw.duration
			: typeof raw.frames === 'number'
				? raw.frames / fps
				: existing?.duration ?? caps.modes.director?.defaultSegmentDuration ?? caps.defaultDuration;
	const promptSegments =
		typeof raw.prompt === 'string'
			? singleTextSegmentList(raw.prompt, existing?.prompt_segments ?? [], `${id}-prompt-0`)
			: existing?.prompt_segments ?? [];
	const subTypeOverride =
		'sub_type_override' in raw
			? raw.sub_type_override === 't2v'
				? 't2v'
				: null
			: existing?.sub_type_override ?? null;
	const references = 'references' in raw ? parseOpSegmentReferences(raw.references, existing?.references) : existing?.references;
	const seg: ChainSegment = {
		id,
		prompt: resolvePromptSegments(promptSegments),
		prompt_segments: promptSegments,
		duration,
		loras: existing?.loras ?? null,
		keyframe: existing?.keyframe ?? null,
		keyframe_strength: existing?.keyframe_strength ?? 1,
		last_keyframe: existing?.last_keyframe ?? null,
		last_keyframe_strength: existing?.last_keyframe_strength ?? 1,
		sub_type_override: subTypeOverride,
		...(references ? { references } : {})
	};
	const nextSegments = idx === -1 ? [...segments, seg] : segments.map((s, i) => (i === idx ? seg : s));
	return { ...value, chain: { ...value.chain, segments: nextSegments } };
}

function applyUpsertSegmentTimeline(value: VideoDirectorValue, raw: unknown): VideoDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string') return value;
	const id = raw.id;
	const segments = value.timeline.segments;
	const idx = segments.findIndex((s) => s.id === id);
	const existing = idx === -1 ? null : segments[idx];
	const start = typeof raw.start === 'number' ? raw.start : existing?.start ?? 0;
	const end = typeof raw.end === 'number' ? raw.end : existing?.end ?? value.timeline.duration;
	const promptSegments =
		typeof raw.prompt === 'string'
			? singleTextSegmentList(raw.prompt, existing?.prompt_segments ?? [], `${id}-prompt-0`)
			: existing?.prompt_segments ?? [];
	const references = 'references' in raw ? parseOpSegmentReferences(raw.references, existing?.references) : existing?.references;
	const seg: DirectorPromptSegment = {
		id,
		start,
		end,
		text: resolvePromptSegments(promptSegments),
		prompt_segments: promptSegments,
		...(references ? { references } : {})
	};
	const nextSegments = idx === -1 ? [...segments, seg] : segments.map((s, i) => (i === idx ? seg : s));
	return { ...value, timeline: { ...value.timeline, segments: nextSegments } };
}

function applyRemoveSegment(value: VideoDirectorValue, id: unknown): VideoDirectorValue {
	if (typeof id !== 'string') return value;
	return {
		...value,
		timeline: { ...value.timeline, segments: value.timeline.segments.filter((s) => s.id !== id) },
		chain: { ...value.chain, segments: value.chain.segments.filter((s) => s.id !== id) }
	};
}

function applyReorderSegments(value: VideoDirectorValue, ids: unknown): VideoDirectorValue {
	if (!Array.isArray(ids) || !ids.every((id): id is string => typeof id === 'string')) return value;
	return {
		...value,
		timeline: { ...value.timeline, segments: reorderByIds(value.timeline.segments, ids) },
		chain: { ...value.chain, segments: reorderByIds(value.chain.segments, ids) }
	};
}

function applyUpsertMedia(value: VideoDirectorValue, raw: unknown, caps: DirectorCapabilities): VideoDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string' || typeof raw.path !== 'string') return value;
	const role = raw.role;
	if (role !== 'first' && role !== 'last' && role !== 'keyframe') return value;
	// A tool request addressed via `form_media` resolves server-side to a
	// concrete `path` (so this op is never unusable on its own) AND carries a
	// `form_ref` marker -- the applier stores the live reference instead of a
	// frozen path snapshot whenever one is present.
	const formRef =
		isRecord(raw.form_ref) && typeof raw.form_ref.field === 'string' && typeof raw.form_ref.path === 'string'
			? { field: raw.form_ref.field, path: raw.form_ref.path }
			: null;
	const media: DirectorMediaValue = formRef ? { form_ref: formRef } : { path: raw.path };

	if (value.mode === 'i2v') {
		return role === 'first' ? { ...value, simple: { ...value.simple, start_image: media } } : value;
	}
	if (value.mode === 'flf') {
		if (role === 'first') return { ...value, simple: { ...value.simple, first_frame: media } };
		if (role === 'last') return { ...value, simple: { ...value.simple, last_frame: media } };
		return value;
	}
	if (value.mode !== 'director') return value;

	if (caps.segmentRouting) {
		if (role === 'keyframe') {
			// Placed anywhere along the chain, keyed by its own id -- only where
			// the mode declares it, otherwise the op has no destination.
			if (caps.modes.director?.keyframes !== 'anywhere') return value;
			const idx = value.chain.keyframes.findIndex((k) => k.id === raw.id);
			const existing = idx === -1 ? null : value.chain.keyframes[idx];
			const kf: ChainKeyframe = {
				id: raw.id,
				at: typeof raw.at === 'number' ? raw.at : existing?.at ?? 0,
				strength: typeof raw.strength === 'number' ? raw.strength : existing?.strength ?? 1,
				media
			};
			const keyframes = idx === -1 ? [...value.chain.keyframes, kf] : value.chain.keyframes.map((k, i) => (i === idx ? kf : k));
			return { ...value, chain: { ...value.chain, keyframes } };
		}
		// Otherwise this is a segment's own leading/trailing frame -- legal on
		// ANY segment now (chainSegmentEdgeAllowances is join-aware, not
		// index-pinned); an omitted segment_id has always meant "the opening
		// shot" (the tool defaults it the same way).
		if ((role !== 'first' && role !== 'last') || value.chain.segments.length === 0) return value;
		const segments = value.chain.segments;
		const targetId = typeof raw.segment_id === 'string' ? raw.segment_id : segments[0].id;
		const targetIdx = segments.findIndex((s) => s.id === targetId);
		if (targetIdx === -1) return value;
		const target = segments[targetIdx];
		const strength =
			typeof raw.strength === 'number' ? raw.strength : role === 'first' ? target.keyframe_strength : target.last_keyframe_strength;
		const patch = role === 'first' ? { keyframe: media, keyframe_strength: strength } : { last_keyframe: media, last_keyframe_strength: strength };
		return {
			...value,
			chain: { ...value.chain, segments: segments.map((s, i) => (i === targetIdx ? { ...s, ...patch } : s)) }
		};
	}

	const kfRole: DirectorKeyframe['role'] = role === 'keyframe' ? 'free' : role;
	const idx = value.timeline.keyframes.findIndex((k) => k.id === raw.id);
	const existing = idx === -1 ? null : value.timeline.keyframes[idx];
	const kf: DirectorKeyframe = {
		id: raw.id,
		start: typeof raw.at === 'number' ? raw.at : existing?.start ?? 0,
		role: kfRole,
		strength: typeof raw.strength === 'number' ? raw.strength : existing?.strength ?? 1,
		media
	};
	const keyframes = idx === -1 ? [...value.timeline.keyframes, kf] : value.timeline.keyframes.map((k, i) => (i === idx ? kf : k));
	return { ...value, timeline: { ...value.timeline, keyframes } };
}

function applyRemoveMedia(value: VideoDirectorValue, id: unknown): VideoDirectorValue {
	if (typeof id !== 'string') return value;
	// Only timeline keyframes and placed chain keyframes carry a stable
	// per-entry id in the editor value; simple media is a single unlabeled slot
	// with nothing to match an id against, so removal there is a no-op. A chain
	// segment's own leading/trailing frame is a slot too, but the read tool
	// (video_director_tool.py's `_flatten`) reports it under a derived
	// `kf-<segment id>` (leading) / `kf-last-<segment id>` (trailing), so those
	// ids are honoured here rather than leaving the model unable to clear a
	// frame it can see.
	return {
		...value,
		timeline: { ...value.timeline, keyframes: value.timeline.keyframes.filter((k) => k.id !== id) },
		chain: {
			...value.chain,
			segments: value.chain.segments.map((s) => {
				if (id === `kf-${s.id}` && s.keyframe != null) return { ...s, keyframe: null };
				if (id === `kf-last-${s.id}` && s.last_keyframe != null) return { ...s, last_keyframe: null };
				return s;
			}),
			keyframes: value.chain.keyframes.filter((k) => k.id !== id)
		}
	};
}

/** Audio lives on whichever composition the current style edits: the chain's
 * own track list under segment_routing, the timeline's otherwise. */
function applyUpsertAudio(value: VideoDirectorValue, raw: unknown, caps: DirectorCapabilities): VideoDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string' || typeof raw.path !== 'string') return value;
	if (value.mode !== 'director' || !caps.modes.director?.audio) return value;
	const onChain = caps.segmentRouting;
	const list = onChain ? value.chain.audio : value.timeline.audio;
	const idx = list.findIndex((a) => a.id === raw.id);
	const existing = idx === -1 ? null : list[idx];
	// The chain editor's select always needs a value; a timeline track stays
	// role-less unless one was given (mirrors normalizeDirectorValue).
	const role = normAudioRole(raw.role) ?? existing?.role ?? (onChain ? 'condition' : undefined);
	const entry: DirectorAudioSegment = {
		id: raw.id,
		start: typeof raw.start === 'number' ? raw.start : existing?.start ?? 0,
		trim_start: typeof raw.trim_start === 'number' ? raw.trim_start : existing?.trim_start ?? 0,
		length: typeof raw.length === 'number' ? raw.length : existing?.length ?? 0,
		media: { path: raw.path },
		...(role ? { role } : {})
	};
	const next = idx === -1 ? [...list, entry] : list.map((a, i) => (i === idx ? entry : a));
	return onChain
		? { ...value, chain: { ...value.chain, audio: next } }
		: { ...value, timeline: { ...value.timeline, audio: next } };
}

function applyRemoveAudio(value: VideoDirectorValue, id: unknown): VideoDirectorValue {
	if (typeof id !== 'string') return value;
	return {
		...value,
		timeline: { ...value.timeline, audio: value.timeline.audio.filter((a) => a.id !== id) },
		chain: { ...value.chain, audio: value.chain.audio.filter((a) => a.id !== id) }
	};
}

function applySetContinuation(value: VideoDirectorValue, raw: unknown, caps: DirectorCapabilities): VideoDirectorValue {
	if (!isRecord(raw) || !caps.segmentRouting) return value;
	const current = value.chain.continuation;
	return {
		...value,
		chain: {
			...value.chain,
			continuation: {
				overlap_frames:
					typeof raw.overlap_frames === 'number' ? raw.overlap_frames : current.overlap_frames,
				stitch: typeof raw.stitch === 'boolean' ? raw.stitch : current.stitch
			}
		}
	};
}

/**
 * Applies a Video Director operations array onto an already-normalized
 * value. Pure and deterministic: same inputs, same output, every time.
 * `operations` is treated as untyped wire data (it arrives as JSON on a
 * tool result) -- each entry is defensively narrowed; anything malformed or
 * unrecognized is skipped rather than thrown.
 */
export function applyDirectorOperations(
	value: VideoDirectorValue,
	operations: unknown,
	caps: DirectorCapabilities
): VideoDirectorValue {
	if (!Array.isArray(operations)) return value;
	return operations.reduce<VideoDirectorValue>((acc, raw) => {
		if (!isRecord(raw) || typeof raw.op !== 'string') return acc;
		switch (raw.op) {
			case 'set_mode':
				return applySetMode(acc, raw.mode);
			case 'set_settings':
				return applySetSettings(acc, raw.settings);
			case 'set_prompt':
				return applySetPrompt(acc, raw.prompt);
			case 'set_negative_prompt':
				return applySetNegativePrompt(acc, raw.negative_prompt);
			case 'upsert_segment':
				return caps.segmentRouting
					? applyUpsertSegmentChain(acc, raw.segment, caps)
					: applyUpsertSegmentTimeline(acc, raw.segment);
			case 'remove_segment':
				return applyRemoveSegment(acc, raw.id);
			case 'reorder_segments':
				return applyReorderSegments(acc, raw.ids);
			case 'upsert_media':
				return applyUpsertMedia(acc, raw.media, caps);
			case 'remove_media':
				return applyRemoveMedia(acc, raw.id);
			case 'upsert_audio':
				return applyUpsertAudio(acc, raw.audio, caps);
			case 'remove_audio':
				return applyRemoveAudio(acc, raw.id);
			case 'set_continuation':
				return applySetContinuation(acc, raw.continuation, caps);
			default:
				return acc;
		}
	}, value);
}

/**
 * Applies an LLM-proposed `<tool_action type="update_director_segment">` --
 * a full-prompt replacement for one director shot -- onto an already-
 * normalized value. Targets whichever list the current mode actually reads
 * (`caps.segmentRouting` routes to `chain.segments`, otherwise
 * `timeline.segments`), resolving id-first then index-fallback like
 * `locateSegmentIndex` in `promptSegments.ts` (segments can be reordered
 * between the tool call and the user clicking Apply). Returns null when
 * neither resolves -- callers must treat that as a no-op, and this must
 * never append a new segment the way a raw `upsert_segment` op would for an
 * unknown id. Delegates to `applyDirectorOperations` so duration/loras/
 * keyframe/start/end on the resolved segment are preserved untouched.
 */
export function applyDirectorSegmentPrompt(
	value: VideoDirectorValue,
	caps: DirectorCapabilities,
	action: { segmentId: string; segmentIndex: number; content: string }
): VideoDirectorValue | null {
	const segments: { id: string }[] = caps.segmentRouting ? value.chain.segments : value.timeline.segments;
	const byId = segments.findIndex((s) => s.id === action.segmentId);
	const idx = byId !== -1 ? byId : action.segmentIndex >= 0 && action.segmentIndex < segments.length ? action.segmentIndex : -1;
	if (idx === -1) return null;
	return applyDirectorOperations(
		value,
		[{ op: 'upsert_segment', segment: { id: segments[idx].id, prompt: action.content } }],
		caps
	);
}
