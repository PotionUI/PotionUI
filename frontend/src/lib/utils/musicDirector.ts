// Pure logic for the Music Director frontend feature: preset-capability
// parsing, default/normalize/validate for the editor value, and the
// editor→wire mapping sent to the backend as `form_data.music_director`.
// Mirrors utils/videoDirector.ts in shape and idiom (see docs/music-director.md
// for the contract this implements), but is its own copy rather than a shared
// import -- same reasoning as the backend's normalize.py duplicating
// `apply_preset_mode_overlay` instead of importing Video Director's: Music
// Director has no chain/timeline duality and no whole-form reference pool, so
// reusing Video Director's richer machinery would mean threading unused
// parameters through every call. No Svelte imports.
//
// Song sections are a REAL segment list (`MusicDirectorValue.segments`),
// edited by the same shared `SegmentedPromptEditor` every other prompt
// surface uses -- there is no bespoke section rail/card UI. A segment's
// `name` carries the section kind (canonicalized against `SECTION_KINDS` by
// `canonicalizeSectionKind`/`matchSectionKind`); `content` is that section's
// lyrics.

import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import type {
	MusicDirectorCapabilities,
	MusicDirectorSettings,
	MusicDirectorValue,
	MusicDirectorWireDoc,
	MusicDirectorWireSection,
	MusicLimits,
	MusicMode,
	MusicModeCapability,
	MusicReferenceItem,
	MusicSettingsCapability,
	SectionKind
} from '$lib/types/musicDirector';
import { MUSIC_MODE_ORDER, SECTION_KINDS } from '$lib/types/musicDirector';
import { sectionKindColor, sectionKindLabel } from '$lib/components/music-director/sectionKindStyle';
import { isSegmentEnabled, toEditorSegment } from '$lib/utils/richSegments';
import { richTextToPlainText } from '$lib/utils/richTextUtils';

function isRecord(v: unknown): v is Record<string, unknown> {
	return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function num(v: unknown, fallback: number): number {
	return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

function str(v: unknown, fallback = ''): string {
	return typeof v === 'string' ? v : fallback;
}

function isSectionKind(v: unknown): v is SectionKind {
	return typeof v === 'string' && (SECTION_KINDS as string[]).includes(v);
}

function isMediaRef(v: unknown): v is MediaRef {
	return isRecord(v) && typeof v.path === 'string';
}

// ─── Section kind <-> segment name ───────────────────────────────────────────

const KIND_ALIASES: Record<string, SectionKind> = (() => {
	const aliases: Record<string, SectionKind> = {};
	for (const kind of SECTION_KINDS) {
		aliases[kind] = kind;
		aliases[kind.replace(/_/g, '-')] = kind;
		aliases[kind.replace(/_/g, ' ')] = kind;
		aliases[sectionKindLabel(kind).toLowerCase()] = kind;
	}
	return aliases;
})();

/**
 * Case-insensitive match of a segment `name` against the 9 canonical section
 * kinds, by label ("Pre-Chorus") or slug ("pre_chorus"/"pre-chorus"),
 * tolerating a trailing counter a repeated quick-add leaves behind ("Verse
 * 2" -> verse). An empty/absent name matches 'verse' (the wire default);
 * anything else that fails to match returns null so `validateMusicDirector`
 * can surface it as an error rather than a wire-build silently guessing.
 */
export function matchSectionKind(name: string | null | undefined): SectionKind | null {
	const trimmed = (name ?? '').trim();
	if (!trimmed) return 'verse';
	const lower = trimmed.toLowerCase();
	const withoutCounter = lower.replace(/[\s#-]+\d+$/, '').trim();
	return KIND_ALIASES[withoutCounter] ?? KIND_ALIASES[lower] ?? null;
}

/** `matchSectionKind` with an unconditional 'verse' fallback for wire-build
 * time -- an unknown name is a validation error caught by
 * `validateMusicDirector` before submission, not something this should
 * refuse to serialize. */
export function canonicalizeSectionKind(name: string | null | undefined): SectionKind {
	return matchSectionKind(name) ?? 'verse';
}

/** Resolve one segment's chip-templated content to plain lyric text --
 * mirrors `flattenRichSegments`'s chip resolution. */
function resolveSegmentLyrics(segment: Segment): string {
	const chips = segment.chips || {};
	const resolved = Object.keys(chips).length ? richTextToPlainText(segment.content || '', chips) : segment.content || '';
	return resolved.trim();
}

function isEnabledContentSegment(segment: Segment): boolean {
	return isSegmentEnabled(segment) && segment.type !== 'break';
}

// ─── Capability parsing ──────────────────────────────────────────────────────

function parseModeCapability(raw: unknown): MusicModeCapability {
	const r = isRecord(raw) ? raw : {};
	const references = r.references === 'whole' || r.references === 'per_section' ? r.references : null;
	const compile = r.compile === 'single_shot' ? 'single_shot' : null;
	return {
		maxReferenceSeconds: typeof r.max_reference_seconds === 'number' ? r.max_reference_seconds : null,
		// Mirrors normalize.py's `mode_caps.get("max_sections", 12)`.
		maxSections: typeof r.max_sections === 'number' ? r.max_sections : 12,
		perSectionPrompts: r.per_section_prompts === true,
		sectionDurationHints: r.section_duration_hints === true,
		references,
		compile,
		descriptionRequired: r.description_required === true
	};
}

/**
 * Merges a `preset_mode_overrides` entry's raw block onto the base preset's
 * raw `vars.music_director` block -- mirrors `apply_preset_mode_overlay` in
 * src/features/music_director/normalize.py EXACTLY: every top-level key in
 * the override replaces the base's raw value wholesale, except `modes`, where
 * each named composition mode is itself shallow-merged (`{...baseEntry,
 * ...override}`) onto the base's entry for that mode. Operating on raw JSON
 * and re-running the merged result through `parseMusicDirectorCapabilities`
 * (rather than merging already-parsed capabilities field-by-field) is what
 * keeps this byte-identical to the backend.
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
 * The single entry point for reading Music Director capabilities everywhere
 * they're used (the editor mount gate, submission, validation): when
 * `presetMode` names an entry in `raw.preset_mode_overrides`, merges that raw
 * block onto the base raw block (`mergeRawCapabilities`) and parses the
 * RESULT; otherwise identical to `parseMusicDirectorCapabilities(raw)`.
 */
export function resolveMusicDirectorCapabilities(raw: unknown, presetMode: string | null | undefined): MusicDirectorCapabilities | null {
	if (!isRecord(raw) || !presetMode) return parseMusicDirectorCapabilities(raw);
	const overridesRaw = isRecord(raw.preset_mode_overrides) ? raw.preset_mode_overrides : null;
	const overrideRaw = overridesRaw && isRecord(overridesRaw[presetMode]) ? overridesRaw[presetMode] : null;
	if (!overrideRaw) return parseMusicDirectorCapabilities(raw);
	return parseMusicDirectorCapabilities(mergeRawCapabilities(raw, overrideRaw));
}

/** Parses the preset var `vars.music_director`. Returns null when the shape has no usable modes. */
export function parseMusicDirectorCapabilities(raw: unknown): MusicDirectorCapabilities | null {
	if (!isRecord(raw)) return null;
	const modesRaw = isRecord(raw.modes) ? raw.modes : null;
	if (!modesRaw) return null;

	const enabledModes = MUSIC_MODE_ORDER.filter((m) => isRecord(modesRaw[m]));
	if (enabledModes.length === 0) return null;

	const modes: Partial<Record<MusicMode, MusicModeCapability>> = {};
	for (const m of enabledModes) {
		modes[m] = parseModeCapability(modesRaw[m]);
	}

	const settingsRaw = isRecord(raw.settings) ? raw.settings : {};
	const settings: MusicSettingsCapability = {
		bpm: settingsRaw.bpm === true,
		key: settingsRaw.key === true,
		timeSignature: settingsRaw.time_signature === true
	};

	const limitsRaw = isRecord(raw.limits) ? raw.limits : {};
	const limits: MusicLimits = {
		defaultDuration: typeof limitsRaw.default_duration === 'number' ? limitsRaw.default_duration : 120,
		maxDuration: typeof limitsRaw.max_duration === 'number' ? limitsRaw.max_duration : null,
		sampleRate: typeof limitsRaw.sample_rate === 'number' ? limitsRaw.sample_rate : null,
		stereo: limitsRaw.stereo !== false
	};

	const presetModes = Array.isArray(raw.preset_modes)
		? raw.preset_modes.filter((p): p is string => typeof p === 'string')
		: null;

	return { presetModes, modes, enabledModes, settings, limits, formOwnsSettings: raw.form_owns_settings === true };
}

// ─── References gate ─────────────────────────────────────────────────────────

/**
 * `(allowed, required)` for the top-level `references` pool, given the
 * submitting `mode` -- mirrors normalize.py's `_references_gate` exactly.
 * `style` always requires at least one reference; `song` may carry references
 * only when the preset ALSO declares a `style` mode; `director` follows its
 * own `references` capability (`'whole'`/`'per_section'`); every other mode
 * accepts none.
 */
export function musicReferencesGate(mode: MusicMode, caps: MusicDirectorCapabilities): { allowed: boolean; required: boolean } {
	if (mode === 'style') return { allowed: true, required: true };
	if (mode === 'song') return { allowed: caps.enabledModes.includes('style'), required: false };
	if (mode === 'director') {
		const refs = caps.modes.director?.references ?? null;
		return { allowed: refs === 'whole' || refs === 'per_section', required: false };
	}
	return { allowed: false, required: false };
}

/** Whether `mode` carries a `sections` timeline at all -- `song`/`director` only. */
export function musicModeHasSections(mode: MusicMode): boolean {
	return mode === 'song' || mode === 'director';
}

// ─── Id minting (explicit user actions only) ─────────────────────────────────
// Mirrors timelineCore.ts's `mintId`: normalize/create never mint an id --
// only a discrete user action (add reference) does, against the live
// collection, so a fresh mount over a session-restored document never
// re-mints an id the document already holds. Segment ids are minted by
// `toEditorSegment`'s own id factory (SegmentedPromptEditor's own add/split
// actions, or `appendQuickSection` below), not here.

export function mintReferenceId(existing: ReadonlyArray<{ id: string }>): string {
	const used = new Set(existing.map((e) => e.id));
	let n = existing.length + 1;
	let id = `ref-${n}`;
	while (used.has(id)) id = `ref-${++n}`;
	return id;
}

// ─── Default / normalize ─────────────────────────────────────────────────────

function defaultSegment(): Segment {
	return toEditorSegment({ type: 'content', name: sectionKindLabel('verse'), color: sectionKindColor('verse'), content: '', chips: {}, enabled: true });
}

/** The quick-add strip's click handler (MusicDirectorEditor.svelte) -- kept
 * here, pure, so it's covered by the same tests as every other section/
 * segment operation. */
export function appendQuickSection(segments: Segment[], kind: SectionKind): Segment[] {
	return [...segments, toEditorSegment({ type: 'content', name: sectionKindLabel(kind), color: sectionKindColor(kind), content: '', chips: {}, enabled: true })];
}

export function createDefaultMusicDirectorValue(caps: MusicDirectorCapabilities): MusicDirectorValue {
	const mode = caps.enabledModes[0] ?? 't2m';
	return {
		schema_version: 1,
		mode,
		description: '',
		instrumental: false,
		segments: musicModeHasSections(mode) ? [defaultSegment()] : [],
		references: [],
		extend_source: null,
		repaint: { source: null, start: 0, end: 10 },
		settings: { duration: caps.limits.defaultDuration, seed: -1, bpm: null, key: null, time_signature: null }
	};
}

function isSegmentLike(v: unknown): v is Segment {
	return isRecord(v) && typeof v.id === 'string' && typeof v.content === 'string';
}

/** Re-shapes a segment already in editor form, preserving its id (and
 * template provenance) rather than minting a fresh one -- this is a session/
 * history round-trip, not a new segment. */
function normSegmentEntry(raw: unknown): Segment | null {
	if (!isSegmentLike(raw)) return null;
	const preservedId = raw.id;
	return toEditorSegment(raw, () => preservedId, raw.template);
}

/** Forward migration from the retired per-section `MusicSection` shape
 * (kind + lyrics) to one content segment: `name` = the kind's label, `content`
 * = the lyrics. Docs from before this redesign (session history, the chat
 * tool's older transcripts) carry `sections`, never `segments`. */
function segmentFromOldSection(raw: unknown): Segment | null {
	if (!isRecord(raw)) return null;
	const kind = isSectionKind(raw.kind) ? raw.kind : 'verse';
	return toEditorSegment({ type: 'content', name: sectionKindLabel(kind), color: sectionKindColor(kind), content: str(raw.lyrics), chips: {}, enabled: true });
}

function normReference(raw: unknown, index: number): MusicReferenceItem | null {
	if (!isRecord(raw) || !isMediaRef(raw.media)) return null;
	return { id: typeof raw.id === 'string' && raw.id ? raw.id : `ref-${index + 1}`, media: raw.media };
}

/** Defensively re-shapes a possibly-stale/garbage stored value. Idempotent. */
export function normalizeMusicDirectorValue(raw: unknown, caps: MusicDirectorCapabilities): MusicDirectorValue {
	const def = createDefaultMusicDirectorValue(caps);
	const r = isRecord(raw) ? raw : {};

	const mode: MusicMode = typeof r.mode === 'string' && (caps.enabledModes as string[]).includes(r.mode) ? (r.mode as MusicMode) : def.mode;

	const segments = musicModeHasSections(mode)
		? (() => {
				if (Array.isArray(r.segments)) {
					const parsed = r.segments.map(normSegmentEntry).filter((s): s is Segment => s !== null);
					if (parsed.length > 0) return parsed;
				}
				const rawSectionsOld = Array.isArray(r.sections) ? r.sections : isRecord(r.sections) ? [r.sections] : [];
				const migrated = rawSectionsOld.map(segmentFromOldSection).filter((s): s is Segment => s !== null);
				return migrated.length > 0 ? migrated : [defaultSegment()];
			})()
		: [];

	const references = Array.isArray(r.references)
		? r.references.map((ref, i) => normReference(ref, i)).filter((ref): ref is MusicReferenceItem => ref !== null)
		: [];

	const extendSourceR = isRecord(r.extend_source) ? r.extend_source : null;
	const extend_source = extendSourceR && isMediaRef(extendSourceR.media) ? { media: extendSourceR.media } : null;

	const repaintR = isRecord(r.repaint) ? r.repaint : {};
	const repaintSourceR = isRecord(repaintR.source) ? repaintR.source : null;
	const repaint = {
		source: repaintSourceR && isMediaRef(repaintSourceR.media) ? { media: repaintSourceR.media } : null,
		start: num(repaintR.start, def.repaint.start),
		end: num(repaintR.end, def.repaint.end)
	};

	const settingsR = isRecord(r.settings) ? r.settings : {};
	const settings: MusicDirectorSettings = {
		duration: num(settingsR.duration, def.settings.duration),
		seed: num(settingsR.seed, def.settings.seed),
		bpm: caps.settings.bpm && typeof settingsR.bpm === 'number' && settingsR.bpm > 0 ? settingsR.bpm : null,
		key: caps.settings.key && typeof settingsR.key === 'string' && settingsR.key.trim() ? settingsR.key : null,
		time_signature:
			caps.settings.timeSignature && typeof settingsR.time_signature === 'string' && settingsR.time_signature.trim()
				? settingsR.time_signature
				: null
	};

	const instrumental = typeof r.instrumental === 'boolean' ? r.instrumental : mode === 't2m';

	return { schema_version: 1, mode, description: str(r.description), instrumental, segments, references, extend_source, repaint, settings };
}

// ─── Modeless mode derivation ────────────────────────────────────────────────
// The editor has no mode switch (mirrors Video Director's deriveDirectorMode/
// VideoDirectorEditor.svelte precedent): `mode` is a derived READ of the
// document's structure plus the "Instrumental (no vocals)" toggle, kept on
// the value only because the wire contract and chat tooling still key off it.

export function deriveMusicDirectorMode(value: MusicDirectorValue, caps: MusicDirectorCapabilities): MusicMode {
	const enabled = caps.enabledModes;
	const contentSegments = value.segments.filter(isEnabledContentSegment);

	if (enabled.includes('extend') && value.extend_source) return 'extend';
	if (enabled.includes('repaint') && value.repaint.source) return 'repaint';
	if (enabled.includes('style') && contentSegments.length === 0 && value.references.length > 0) return 'style';

	// More than one section is what distinguishes a `director` arrangement
	// from `song`'s single-lyrics shorthand -- per-section style hints/
	// duration hints/references no longer exist as an editable surface (see
	// `MusicDirectorValue.segments`'s doc comment), so section COUNT is the
	// only signal left.
	if (enabled.includes('director') && contentSegments.length > 1) return 'director';

	if (value.instrumental) {
		if (enabled.includes('t2m')) return 't2m';
		if (enabled.includes('song')) return 'song'; // pipeline composes "[instrumental]" lyrics
	}

	// `song` requires at least one section (normalize_music_director rejects an
	// empty one) -- a fresh/untouched document with no section content yet is
	// not validly a "song" no matter how eagerly the capability set allows it,
	// so it stays `t2m` (valid with zero content) until the user actually adds
	// a section. The segment editor itself renders off capability, not off
	// this derived mode (see MusicDirectorEditor.svelte's `hasSections`),
	// precisely so there's always a way to reach that first section.
	if (enabled.includes('song') && contentSegments.length > 0) return 'song';
	if (enabled.includes('t2m')) return 't2m';
	if (enabled.includes('song')) return 'song';
	return enabled[0] ?? 'song';
}

export function addMusicReference(references: MusicReferenceItem[], id: string, media: MediaRef): MusicReferenceItem[] {
	return [...references, { id, media }];
}

export function removeMusicReference(references: MusicReferenceItem[], id: string): MusicReferenceItem[] {
	return references.filter((r) => r.id !== id);
}

// ─── Validation ───────────────────────────────────────────────────────────────

/**
 * Validates an editor value against its capabilities, mirroring
 * `normalize_music_director`'s rules with human-readable reasons. Re-derives
 * `mode` from `value`'s structure (`deriveMusicDirectorMode`) rather than
 * trusting the stored `value.mode` field -- same "modeless" precedent as
 * Video Director's `validateDirector`.
 */
export function validateMusicDirector(value: MusicDirectorValue, caps: MusicDirectorCapabilities): { ok: boolean; reasons: string[] } {
	const reasons: string[] = [];
	const mode = deriveMusicDirectorMode(value, caps);
	const cap = caps.modes[mode];
	if (!cap) {
		reasons.push(`Mode '${mode}' is not supported by this preset`);
		return { ok: false, reasons };
	}

	const contentSegments = value.segments.filter(isEnabledContentSegment);

	if (musicModeHasSections(mode)) {
		if (contentSegments.length === 0) {
			reasons.push(mode === 'director' ? 'At least one section is required' : 'Lyrics require at least one section');
		}
		if (mode === 'director' && contentSegments.length > cap.maxSections) {
			reasons.push(`Too many sections (max ${cap.maxSections})`);
		}
		for (const segment of contentSegments) {
			if (matchSectionKind(segment.name) == null) {
				reasons.push(
					`Segment "${segment.name}" is not a recognized section kind -- use one of: ${SECTION_KINDS.map(sectionKindLabel).join(', ')}`
				);
			}
		}
	} else if (contentSegments.length > 0) {
		reasons.push(`Mode '${mode}' does not accept sections`);
	}

	const refGate = musicReferencesGate(mode, caps);
	if (!refGate.allowed && value.references.length > 0) {
		reasons.push(`Mode '${mode}' does not accept references`);
	} else if (refGate.required && value.references.length === 0) {
		reasons.push('At least one reference is required in this mode');
	}
	if (refGate.allowed && mode === 'style' && cap.maxReferenceSeconds != null) {
		if (value.references.some((r) => r.media.type === 'audio' && (r.media as { duration_seconds?: number }).duration_seconds != null && (r.media as { duration_seconds?: number }).duration_seconds! > cap.maxReferenceSeconds!)) {
			reasons.push(`A reference exceeds this mode's maximum of ${cap.maxReferenceSeconds}s`);
		}
	}

	if (mode === 'extend' && !value.extend_source) {
		reasons.push('An extend source track is required');
	}
	if (mode === 'repaint') {
		if (!value.repaint.source) reasons.push('A repaint source track is required');
		if (!(value.repaint.start >= 0 && value.repaint.start < value.repaint.end)) {
			reasons.push('Repaint range must satisfy 0 <= start < end');
		}
	}

	if (!caps.formOwnsSettings) {
		if (!(value.settings.duration > 0)) {
			reasons.push('Duration must be greater than 0');
		} else if (caps.limits.maxDuration != null && value.settings.duration > caps.limits.maxDuration) {
			reasons.push(`Duration exceeds the allowed maximum of ${caps.limits.maxDuration}s`);
		}

		if (cap.descriptionRequired && !value.description.trim()) {
			reasons.push('A style description is required -- there is nothing to generate music from otherwise');
		}
	}

	if (value.settings.bpm != null && !caps.settings.bpm) reasons.push('BPM is not supported by this preset');
	if (value.settings.key != null && !caps.settings.key) reasons.push('Key is not supported by this preset');
	if (value.settings.time_signature != null && !caps.settings.timeSignature) {
		reasons.push('Time signature is not supported by this preset');
	}

	return { ok: reasons.length === 0, reasons };
}

// ─── Editor → wire mapping ────────────────────────────────────────────────────

function wireSection(segment: Segment): MusicDirectorWireSection {
	return { id: segment.id, kind: canonicalizeSectionKind(segment.name), lyrics: resolveSegmentLyrics(segment) };
}

/**
 * Builds `form_data.music_director` from an editor value. Re-derives `mode`
 * from `value`'s structure (`deriveMusicDirectorMode`) rather than trusting
 * the stored `value.mode` field -- same "modeless" precedent as Video
 * Director's `buildDirectorSubmission`. `song` mode emits the single-section-
 * object shorthand (see docs/music-director.md `sections`) whenever the
 * document holds exactly one section -- the common case of plain lyrics with
 * no arrangement structure. `description`/`settings` are still emitted even
 * when `caps.formOwnsSettings` is true -- the preset's dynamic form fields
 * are the ones actually read by that preset's pipeline; this wire doc's
 * copies are simply unread, same as `settings.bpm` is unread by a preset
 * that never declared it.
 */
export function buildMusicDirectorSubmission(value: MusicDirectorValue, caps: MusicDirectorCapabilities): MusicDirectorWireDoc {
	const mode = deriveMusicDirectorMode(value, caps);

	const out: MusicDirectorWireDoc = {
		schema_version: 1,
		mode,
		description: value.description,
		settings: {
			duration: value.settings.duration,
			seed: value.settings.seed
		}
	};
	if (caps.settings.bpm && value.settings.bpm != null) out.settings.bpm = value.settings.bpm;
	if (caps.settings.key && value.settings.key != null) out.settings.key = value.settings.key;
	if (caps.settings.timeSignature && value.settings.time_signature != null) out.settings.time_signature = value.settings.time_signature;

	if (musicModeHasSections(mode)) {
		const contentSegments = value.segments.filter(isEnabledContentSegment);
		const wireSections = contentSegments.map(wireSection);
		out.sections = mode === 'song' && wireSections.length === 1 ? wireSections[0] : wireSections;
		out.segments = value.segments;
	}

	const refGate = musicReferencesGate(mode, caps);
	if (refGate.allowed && value.references.length > 0) {
		out.references = value.references.map((r) => ({ id: r.id, media: r.media }));
	}

	if (mode === 'extend' && value.extend_source) out.extend_source = value.extend_source;
	if (mode === 'repaint' && value.repaint.source) {
		out.repaint = { source: value.repaint.source, start: value.repaint.start, end: value.repaint.end };
	}

	return out;
}

// ─── Chat tool application (LLM operations → editor value) ──────────────────
// Mirrors `applyDirectorOperations` in utils/videoDirector.ts: pure, treats
// `operations` as untyped wire data (it arrives as JSON on a tool result) and
// defensively narrows every entry, and never mints an id itself -- like
// applyDirectorOperations, an upsert without a server-assigned `id` is simply
// skipped rather than invented here (update_music_director's confirmed result
// always fills one in). Callers normalize the result afterward
// (normalizeMusicDirectorValue), the same way applyDirectorSegmentPrompt's
// caller does for Video Director.
// Operation names/schemas are the wire-section shape the LLM already knows
// (upsert_section {id?, kind, lyrics}, remove_section {id}, reorder_sections
// {ids}) -- applied here onto the segment list: `kind` becomes the segment's
// `name` (its label), `lyrics` becomes `content`.

function applySetDescription(value: MusicDirectorValue, description: unknown): MusicDirectorValue {
	if (typeof description !== 'string') return value;
	return { ...value, description };
}

function applySetInstrumental(value: MusicDirectorValue, instrumental: unknown): MusicDirectorValue {
	if (typeof instrumental !== 'boolean') return value;
	return { ...value, instrumental };
}

function applySetMusicSettings(value: MusicDirectorValue, settings: unknown): MusicDirectorValue {
	if (!isRecord(settings)) return value;
	const next: MusicDirectorSettings = { ...value.settings };
	if (typeof settings.duration === 'number') next.duration = settings.duration;
	if (typeof settings.bpm === 'number' || settings.bpm === null) next.bpm = (settings.bpm as number | null) ?? null;
	if (typeof settings.key === 'string' || settings.key === null) next.key = (settings.key as string | null) ?? null;
	if (typeof settings.time_signature === 'string' || settings.time_signature === null) {
		next.time_signature = (settings.time_signature as string | null) ?? null;
	}
	return { ...value, settings: next };
}

function applyUpsertMusicSection(value: MusicDirectorValue, raw: unknown): MusicDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string') return value;
	const id = raw.id;
	const idx = value.segments.findIndex((s) => s.id === id);
	const existing = idx === -1 ? null : value.segments[idx];

	const kind = isSectionKind(raw.kind) ? raw.kind : existing ? (matchSectionKind(existing.name) ?? 'verse') : 'verse';
	const lyricsSet = typeof raw.lyrics === 'string';
	const content = lyricsSet ? (raw.lyrics as string) : (existing?.content ?? '');

	if (existing) {
		const updated: Segment = {
			...existing,
			name: sectionKindLabel(kind),
			color: sectionKindColor(kind),
			content,
			// A real lyrics edit resets any chip state to match the LLM's plain
			// text -- keeping stale chips around when the text they resolved
			// from is gone would desync the two.
			chips: lyricsSet ? {} : (existing.chips ?? {})
		};
		return { ...value, segments: value.segments.map((s, i) => (i === idx ? updated : s)) };
	}

	const segment = toEditorSegment(
		{ type: 'content', name: sectionKindLabel(kind), color: sectionKindColor(kind), content, chips: {}, enabled: true },
		() => id
	);
	return { ...value, segments: [...value.segments, segment] };
}

function applyRemoveMusicSection(value: MusicDirectorValue, id: unknown): MusicDirectorValue {
	if (typeof id !== 'string') return value;
	return { ...value, segments: value.segments.filter((s) => s.id !== id) };
}

function applyReorderMusicSections(value: MusicDirectorValue, ids: unknown): MusicDirectorValue {
	if (!Array.isArray(ids) || !ids.every((id) => typeof id === 'string')) return value;
	const byId = new Map(value.segments.map((s) => [s.id, s] as const));
	const ordered = (ids as string[]).map((id) => byId.get(id)).filter((s): s is Segment => s !== undefined);
	const orderedIds = new Set(ordered.map((s) => s.id));
	return { ...value, segments: [...ordered, ...value.segments.filter((s) => !orderedIds.has(s.id))] };
}

function applyUpsertMusicReference(value: MusicDirectorValue, raw: unknown): MusicDirectorValue {
	if (!isRecord(raw) || typeof raw.id !== 'string' || !isMediaRef(raw.media)) return value;
	const id = raw.id;
	const idx = value.references.findIndex((r) => r.id === id);
	const reference: MusicReferenceItem = { id, media: raw.media };
	const references = idx === -1 ? [...value.references, reference] : value.references.map((r, i) => (i === idx ? reference : r));
	return { ...value, references };
}

function applyRemoveMusicReference(value: MusicDirectorValue, id: unknown): MusicDirectorValue {
	if (typeof id !== 'string') return value;
	return { ...value, references: value.references.filter((r) => r.id !== id) };
}

/**
 * Applies `update_music_director`'s confirmed operations onto an
 * already-normalized editor value. `caps` is accepted for signature parity
 * with `applyDirectorOperations` (a future capability-gated op won't need a
 * signature change) but unused today -- none of these ops branch on
 * capability the way Video Director's chain/timeline split does.
 */
export function applyMusicDirectorOperations(
	value: MusicDirectorValue,
	operations: unknown,
	_caps: MusicDirectorCapabilities
): MusicDirectorValue {
	if (!Array.isArray(operations)) return value;
	return operations.reduce<MusicDirectorValue>((acc, raw) => {
		if (!isRecord(raw) || typeof raw.op !== 'string') return acc;
		switch (raw.op) {
			case 'set_description':
				return applySetDescription(acc, raw.description);
			case 'set_instrumental':
				return applySetInstrumental(acc, raw.instrumental);
			case 'set_settings':
				return applySetMusicSettings(acc, raw.settings);
			case 'upsert_section':
				return applyUpsertMusicSection(acc, raw.section);
			case 'remove_section':
				return applyRemoveMusicSection(acc, raw.id);
			case 'reorder_sections':
				return applyReorderMusicSections(acc, raw.ids);
			case 'upsert_reference':
				return applyUpsertMusicReference(acc, raw.reference);
			case 'remove_reference':
				return applyRemoveMusicReference(acc, raw.id);
			default:
				return acc;
		}
	}, value);
}
