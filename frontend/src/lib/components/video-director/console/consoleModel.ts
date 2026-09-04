// Pure derivation layer for the Shot Console rework (W1). Turns a
// VideoDirectorValue + DirectorCapabilities into the flat, render-ready shape
// ShotConsole.svelte and its children draw -- no Svelte imports, no
// Date.now/Math.random, byte-deterministic for a given (doc, caps, ui,
// formData) tuple.
//
// Builds ON railModel.ts's deriveRailModel/deriveShotLabel rather than
// re-deriving frame/seam math, and reuses utils/videoDirector.ts's
// validateDirector/collectFormMediaOptions/resolveDirectorMediaDisplay --
// never forks the document model.
//
// ─── W1 scope (see W1-BRIEF.md's "Rules" + PLAN.md §C W1/§D) ───────────────
// - A CHAIN document (Wan/H3 Video/H3 refs) renders one ConsoleShot per
//   `chain.segments` entry -- today's real per-shot document shape.
// - A TIMELINE document (LTX) has no shot boundaries in the document yet
//   (`DirectorTimelineDoc` is one clip with timed prompt BEATS inside it --
//   the shots[] field lands in W2). W1 renders it as exactly ONE ConsoleShot
//   spanning the whole timeline; the timeline's own beats/keyframes/audio
//   become that one shot's rail lanes. `canAddShot` is therefore always false
//   for timeline routing in W1 (see below).
// - Dependency badges: only 'independent' and 'continuous' are derivable
//   without a runs map (W3) -- never 'needs-previous'/'input-ready'/'stale'.
// - `run` is always null (no per-shot generation exists yet, W3).
// - `fpsLocked` is always false and no "24 fps" header chip is ever emitted:
//   there is no `fps_locked` capability on `DirectorModeCapability` yet (W2
//   adds one to MiniMax-H3's preset block) -- without it there is no signal
//   to derive "fixed" from, so this omits the chip entirely rather than
//   guess (see PLAN.md's open item D5 and the W1-BRIEF's own hedge on this).
//
// ─── Deviations from the written contract (documented, not silent) ────────
// - No `presetLabel` parameter and no `ConsoleHeader.modelLabel` field: the
//   W1-BRIEF's own contract had both (a 4th required `presetLabel` arg, a
//   model-picker chip in the header), but a maintainer ruling made after this
//   wave's brief was written removed the preset chip AND "Generate film"
//   from the console entirely -- the page's own Generate control outside the
//   Director is the only way to generate a film. `deriveConsoleModel` takes
//   `formData` where `presetLabel` used to sit (see the next bullet).
// - `formData` is an added OPTIONAL parameter, not in the W1-BRIEF's 4-arg
//   signature. Two things below are undecidable without it: a `form_ref`
//   media value's thumbnail URL, and a per_shot References tab's pool size
//   (`collectFormMediaOptions` reads the live form same as stageModel.ts's
//   `shotReferencesInfo`). Lanes (b)(c)(d) never call `deriveConsoleModel`
//   themselves (they only consume `ConsoleModel`/`ConsoleShot` etc. as
//   props), so this changes nothing for them; only `ShotConsole.svelte`
//   (this lane's own integration file) calls it. Omitting it degrades
//   gracefully (a `form_ref` thumb resolves to null/slate, an unresolvable
//   pool reads as 0) rather than throwing.
// - No bare "References" header cap-strip chip: `console.html`'s own
//   MiniMax H3 Video frame has `references: 'per_shot'` (its stage carries a
//   `References · 3 of 6` tab) yet its cap-strip never shows a "References"
//   chip -- the template itself is the tie-breaker per this wave's "hold to
//   the template literally" rule, over the brief's own chip-text list.

import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorModeCapability,
	DirectorMediaValue,
	ChainSegment
} from '$lib/types/videoDirector';
import {
	deriveRailModel,
	deriveShotLabel,
	type RailModel,
	type RailShotBlock,
	type RailSeam
} from '../stage-rail/railModel';
import {
	DEFAULT_MAX_KEYFRAMES,
	collectFormMediaOptions,
	resolveDirectorMediaDisplay,
	validateDirector
} from '$lib/utils/videoDirector';

// ─── Public types (verbatim from W1-BRIEF.md's contract) ───────────────────

export type ConsoleBadge = 'independent' | 'needs-previous' | 'input-ready' | 'stale' | 'continuous';
export type ConsoleRunState = null | { kind: 'queued' } | { kind: 'generating'; percent: number } | { kind: 'done'; time: string } | { kind: 'failed' };

export interface ConsoleThumb {
	url: string | null;
	source: 'keyframe' | 'start' | 'end' | 'output' | 'slate';
}

export interface ConsoleShot {
	id: string;
	index: number;
	number: string /* '01' */;
	title: string;
	durationSeconds: number;
	startSeconds: number;
	frames: number;
	capFrames: number | null;
	newFrames: number | null;
	fps: number;
	fpsLocked: boolean;
	thumb: ConsoleThumb;
	badge: ConsoleBadge;
	hasIcLora: boolean;
	icLoraCount: number;
	run: ConsoleRunState;
	tabs: Array<{ id: 'selection' | 'loras' | 'references' | 'ic_lora'; label: string }>;
	canRemove: boolean;
	canDuplicate: boolean;
}

export type ConsoleJoinKind = 'cut' | 'continue' | 'native' | 'missing';

export interface ConsoleJoin {
	afterShotId: string;
	beforeShotId: string;
	kind: ConsoleJoinKind;
	label: string /* 'HARD CUT' */;
	sentence: string;
	control: { kind: 'chip'; text: string } | { kind: 'toggle'; value: 'continue' | 'cut' };
	overlapFrames: number | null;
}

export interface ConsoleHeader {
	title: string;
	shotCount: number;
	totalSeconds: number;
	capChips: Array<{ icon?: string; text: string }>;
	readiness: { ok: boolean; text: string };
}

export interface ConsoleFilmRow {
	kind: 'global' | 'negative';
	label: string;
	text: string;
	segmentCount: number | null;
}

export interface ConsoleModel {
	header: ConsoleHeader;
	filmRows: ConsoleFilmRow[];
	shots: ConsoleShot[];
	joins: ConsoleJoin[];
	canAddShot: boolean;
	addShotDisabledReason: string | null;
}

/** Synthetic id for the single console shot a TIMELINE (LTX) document renders
 * as in W1 -- there is no per-shot id on `DirectorTimelineDoc` yet (that
 * lands with W2's `DirectorTimelineShot`). Stable across renders (never
 * minted from a counter/clock) so `ui.activeShotId` and `deriveShotRail`
 * agree on the same id. */
export const TIMELINE_SHOT_ID = 'timeline-shot';

// ─── Small shared helpers ───────────────────────────────────────────────────

function shotNumber(index: number): string {
	return String(index + 1).padStart(2, '0');
}

function thumbUrlFor(media: DirectorMediaValue | null, formData: Record<string, unknown> | null | undefined): string | null {
	if (!media) return null;
	const display = resolveDirectorMediaDisplay(media, formData);
	if (display.kind === 'embedded' || display.kind === 'form_ref') return display.media.url ?? null;
	return null;
}

function referencesPoolCount(caps: DirectorCapabilities, formData: Record<string, unknown> | null | undefined): number {
	return collectFormMediaOptions(formData).filter((o) => caps.referenceFields.includes(o.field)).length;
}

function referencesTabLabel(
	caps: DirectorCapabilities,
	formData: Record<string, unknown> | null | undefined,
	references: { length: number } | undefined
): string {
	const poolCount = referencesPoolCount(caps, formData);
	const selected = references && references.length > 0 ? references.length : null;
	return selected != null ? `References · ${selected} of ${poolCount}` : `References · All`;
}

/** `references: 'whole'` shots have no per-shot selection at all -- every
 * shot always conditions on the entire pool (PLAN.md §A) -- so the tab reads
 * as a bare pool count, never "All"/"n of m" (those imply a selection that
 * doesn't exist here). */
function referencesWholeTabLabel(caps: DirectorCapabilities, formData: Record<string, unknown> | null | undefined): string {
	return `References · ${referencesPoolCount(caps, formData)}`;
}

// ─── Header ──────────────────────────────────────────────────────────────────

function buildCapChips(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel
): Array<{ icon?: string; text: string }> {
	const dc = caps.modes.director;
	if (!dc) return [];
	const chips: Array<{ icon?: string; text: string }> = [];

	if (dc.keyframes === 'anywhere') chips.push({ icon: 'image', text: 'First + last frame' });
	else if (dc.keyframes === 'first_only') chips.push({ icon: 'image', text: 'Edge anchors only' });

	if (dc.keyframes === 'anywhere') {
		const cap = dc.maxKeyframes ?? DEFAULT_MAX_KEYFRAMES;
		const placed = caps.segmentRouting
			? doc.chain.keyframes.length
			: doc.timeline.keyframes.filter((k) => k.role === 'free').length;
		chips.push({ icon: 'layers', text: `Free keyframes · ${placed}/${cap}` });
	}

	if (dc.audio) chips.push({ text: 'Audio' });
	if (dc.icLora) chips.push({ text: 'IC-LoRA' });
	if (dc.perSegmentLoras) chips.push({ text: 'Per-shot LoRAs' });

	return chips;
}

function buildHeader(doc: VideoDirectorValue, caps: DirectorCapabilities, rail: RailModel, shotCount: number): ConsoleHeader {
	const result = validateDirector(doc, caps);
	return {
		title: 'Video Director',
		shotCount,
		totalSeconds: rail.totalSeconds,
		capChips: buildCapChips(doc, caps, rail),
		readiness: { ok: result.ok, text: result.ok ? 'Ready' : (result.reasons[0] ?? 'Not ready') }
	};
}

function buildFilmRows(doc: VideoDirectorValue): ConsoleFilmRow[] {
	return [
		{
			kind: 'global',
			label: 'Global prompt',
			text: doc.global_prompt,
			segmentCount: doc.global_prompt_segments.length > 0 ? doc.global_prompt_segments.length : null
		},
		{
			kind: 'negative',
			label: 'Negative prompt',
			text: doc.negative_prompt,
			segmentCount: null
		}
	];
}

// ─── Chain shots/joins ──────────────────────────────────────────────────────

function chainShotBadge(rail: RailModel, index: number): ConsoleBadge {
	const incoming = index > 0 ? rail.seams[index - 1] : null;
	const outgoing = index < rail.shots.length - 1 ? rail.seams[index] : null;
	const continuous = (incoming && incoming.kind === 'continue') || (outgoing && outgoing.kind === 'continue');
	return continuous ? 'continuous' : 'independent';
}

/** Which shot a chain 'anywhere' keyframe's film-time position lands in --
 * mirrors stageModel.ts's (unexported) `chainLandingWindow`, index-only. */
function chainLandingShotIndex(rail: RailModel, atSeconds: number): number {
	let targetIndex = rail.shots.length - 1;
	for (const shot of rail.shots) {
		if (atSeconds < shot.startSeconds + shot.contributedSeconds || shot.index === rail.shots.length - 1) {
			targetIndex = shot.index;
			break;
		}
	}
	return targetIndex;
}

function chainShotThumb(
	doc: VideoDirectorValue,
	rail: RailModel,
	segment: ChainSegment,
	block: RailShotBlock,
	formData: Record<string, unknown> | null | undefined
): ConsoleThumb {
	const freeKeyframe = doc.chain.keyframes.find(
		(kf) => kf.media != null && chainLandingShotIndex(rail, kf.at) === block.index
	);
	if (freeKeyframe) return { url: thumbUrlFor(freeKeyframe.media, formData), source: 'keyframe' };
	if (segment.keyframe) return { url: thumbUrlFor(segment.keyframe, formData), source: 'start' };
	if (segment.last_keyframe) return { url: thumbUrlFor(segment.last_keyframe, formData), source: 'end' };
	return { url: null, source: 'slate' };
}

function buildChainShots(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel,
	formData: Record<string, unknown> | null | undefined
): ConsoleShot[] {
	const dc = caps.modes.director;
	return rail.shots.map((block, index) => {
		const segment = doc.chain.segments[index];
		const tabs: ConsoleShot['tabs'] = [{ id: 'selection', label: 'Selection' }];
		if (dc?.perSegmentLoras) {
			const count = (segment.loras?.high.length ?? 0) + (segment.loras?.low.length ?? 0);
			tabs.push({ id: 'loras', label: `LoRAs · ${count}` });
		}
		if (caps.references === 'per_shot') {
			tabs.push({ id: 'references', label: referencesTabLabel(caps, formData, segment.references) });
		} else if (caps.references === 'whole') {
			tabs.push({ id: 'references', label: referencesWholeTabLabel(caps, formData) });
		}
		return {
			id: segment.id,
			index,
			number: shotNumber(index),
			title: deriveShotLabel(segment.prompt, index),
			durationSeconds: segment.duration,
			startSeconds: block.startSeconds,
			frames: block.totalFrames,
			capFrames: block.capFrames,
			newFrames: block.hasOverlapIn ? block.contributedFrames : null,
			fps: rail.fps,
			fpsLocked: false,
			thumb: chainShotThumb(doc, rail, segment, block, formData),
			badge: chainShotBadge(rail, index),
			hasIcLora: false,
			icLoraCount: 0,
			run: null,
			tabs,
			canRemove: rail.shots.length > 1,
			canDuplicate: dc?.maxSegments == null || rail.shots.length < dc.maxSegments
		};
	});
}

function buildChainJoins(rail: RailModel, caps: DirectorCapabilities): ConsoleJoin[] {
	const continuationAvailable = caps.modes.director?.continuationDisabled !== true;
	return rail.seams.map((seam: RailSeam) => {
		const fromShot = rail.shots[seam.beforeShotIndex];
		const toShot = rail.shots[seam.beforeShotIndex + 1];
		const isCut = seam.kind === 'cut';
		const kind: ConsoleJoinKind = isCut ? 'cut' : 'native';
		const label = isCut ? 'HARD CUT' : 'NATIVE CONTINUATION';
		const sentence = isCut
			? 'Starts fresh — nothing shared.'
			: `Inherits Shot ${shotNumber(seam.beforeShotIndex)}'s last frame · ${seam.overlapFrames}f overlap.`;
		const control: ConsoleJoin['control'] = continuationAvailable
			? { kind: 'toggle', value: isCut ? 'cut' : 'continue' }
			: { kind: 'chip', text: 'Independent' };
		return {
			afterShotId: fromShot.id,
			beforeShotId: toShot.id,
			kind,
			label,
			sentence,
			control,
			overlapFrames: isCut ? null : seam.overlapFrames
		};
	});
}

// ─── Timeline (LTX) single shot ────────────────────────────────────────────

function timelineFilmTitle(doc: VideoDirectorValue): string {
	const withText = doc.timeline.segments.find((s) => s.text.trim() !== '');
	if (withText) return deriveShotLabel(withText.text, 0);
	if (doc.global_prompt.trim() !== '') return deriveShotLabel(doc.global_prompt, 0);
	return deriveShotLabel('', 0);
}

function timelineShotThumb(doc: VideoDirectorValue, formData: Record<string, unknown> | null | undefined): ConsoleThumb {
	const sorted = [...doc.timeline.keyframes].sort((a, b) => a.start - b.start);
	const free = sorted.find((k) => k.role === 'free' && k.media != null);
	if (free) return { url: thumbUrlFor(free.media, formData), source: 'keyframe' };
	const start = sorted.find((k) => k.role === 'first' && k.media != null);
	if (start) return { url: thumbUrlFor(start.media, formData), source: 'start' };
	const end = sorted.find((k) => k.role === 'last' && k.media != null);
	if (end) return { url: thumbUrlFor(end.media, formData), source: 'end' };
	return { url: null, source: 'slate' };
}

function buildTimelineShot(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel,
	formData: Record<string, unknown> | null | undefined
): ConsoleShot {
	const dc = caps.modes.director;
	const icLoraEntries = doc.timeline.ic_lora.filter((e) => e.lora != null);
	const hasIcLora = dc?.icLora === true && icLoraEntries.length > 0;

	const tabs: ConsoleShot['tabs'] = [{ id: 'selection', label: 'Selection' }];
	if (caps.references === 'per_shot') {
		const references = doc.timeline.segments[0]?.references;
		tabs.push({ id: 'references', label: referencesTabLabel(caps, formData, references) });
	} else if (caps.references === 'whole') {
		tabs.push({ id: 'references', label: referencesWholeTabLabel(caps, formData) });
	}
	if (dc?.icLora) {
		tabs.push({ id: 'ic_lora', label: `IC-LoRA · ${icLoraEntries.length}` });
	}

	return {
		id: TIMELINE_SHOT_ID,
		index: 0,
		number: shotNumber(0),
		title: timelineFilmTitle(doc),
		durationSeconds: rail.totalSeconds,
		startSeconds: 0,
		frames: rail.totalFrames,
		capFrames: rail.maxFrames,
		newFrames: null,
		fps: rail.fps,
		fpsLocked: false,
		thumb: timelineShotThumb(doc, formData),
		badge: 'independent',
		hasIcLora,
		icLoraCount: icLoraEntries.length,
		run: null,
		tabs,
		canRemove: false,
		canDuplicate: false
	};
}

// ─── Entry point ────────────────────────────────────────────────────────────

export function deriveConsoleModel(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	ui: { activeShotId: string | null },
	formData?: Record<string, unknown> | null
): ConsoleModel {
	const rail = deriveRailModel(doc, caps);

	if (rail.routing === 'chain') {
		const shots = buildChainShots(doc, caps, rail, formData);
		const joins = buildChainJoins(rail, caps);
		return {
			header: buildHeader(doc, caps, rail, shots.length),
			filmRows: buildFilmRows(doc),
			shots,
			joins,
			canAddShot: rail.canAddShot,
			addShotDisabledReason: rail.canAddShot
				? null
				: `Maximum of ${caps.modes.director?.maxSegments ?? shots.length} shots reached`
		};
	}

	const shot = buildTimelineShot(doc, caps, rail, formData);
	return {
		header: buildHeader(doc, caps, rail, 1),
		filmRows: buildFilmRows(doc),
		shots: [shot],
		joins: [],
		// Multiple independent LTX shots need `DirectorTimelineShot` (W2) --
		// today's `DirectorTimelineDoc` is one clip, so W1 never offers this.
		canAddShot: false,
		addShotDisabledReason: 'Multiple shots per LTX film land in a later release'
	};
}
