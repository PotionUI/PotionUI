// Pure geometry layer for the Shot Console's per-shot RAIL (W1). Turns a
// VideoDirectorValue + DirectorCapabilities + a shot id into the local
// ruler/lane geometry ShotRail.svelte draws, entirely in PERCENT-OF-SHOT
// coordinates (no zoom, unlike the film-level RelayTimeline/Rail machinery in
// `timelineCore.ts`/`railModel.ts` this builds on) -- no Svelte imports, no
// Date.now/Math.random, byte-deterministic for a given (doc, caps, shotId,
// formData) tuple.
//
// Tick rule (fixed, not zoom-based -- see W1-BRIEF.md): a major tick every
// whole second from 0, ALWAYS a final major tick at the shot's own duration
// (right-aligned, even when that isn't a whole second), and a minor tick
// every 0.25s in between (skipping any 0.25s multiple that already landed on
// a whole second).
//
// Chain routing (Wan/H3 Video/H3 refs): a shot IS one `chain.segments` entry.
// Its local rail spans that segment's own generated duration (`segment
// .duration`, matching the mock's per-shot "5.1 s" rail width -- NOT the
// contributed length after overlap deduction, which is a RENDER concern the
// rail geometry has no reason to shrink for). Per PLAN.md's D3 ruling, a
// chain shot's Prompt lane is always exactly one full-span beat with adding
// disabled -- "H3 windows take one prompt per segment" -- overriding
// console.html's own (stale) partial-beat-plus-Global-fill markup for that
// profile, per PLAN.md's own instruction that its §D defaults are this
// wave's rulings. `chain.keyframes`/`chain.audio` are FILM-time (seconds from
// the start of the whole chain) and are rebased to this shot's local percent
// here; a free keyframe/audio clip is attributed to whichever shot it
// actually lands in (frame-accurate, mirrors stageModel.ts's own
// `chainLandingWindow`), never split across a join.
//
// Timeline routing (LTX, W2): each `doc.timeline.shots` entry is its own
// independent clip -- this derives the rail for exactly the one named by
// `shotId`, spanning that shot's own `deriveRailModel(doc, caps, shotId)
// .totalSeconds`; its `segments`/`keyframes`/`audio` are already shot-local
// (no rebasing needed, unlike chain routing, where a shot's placed
// keyframes/audio are FILM-time and must be rebased into the shot's own
// window).
//
// ─── Deviation from the written contract ────────────────────────────────────
// `formData` is an added OPTIONAL 4th parameter (see consoleModel.ts's own
// header note for why: resolving a `form_ref` media value's thumbnail needs
// the live form, and `deriveShotRail(doc, caps, shotId)` per the brief has no
// such parameter). Omitting it degrades to a null thumbUrl rather than throw.

import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue, DirectorTimelineShot } from '$lib/types/videoDirector';
import type { MediaRef } from '$lib/types/tabs';
import { deriveRailModel, deriveShotLabel, type RailModel } from '../stage-rail/railModel';
import { resolveDirectorMediaDisplay, chainEdgeKeyframeId, timelineEdgeKeyframeId, resolveDirectorEdgeAllowances } from '$lib/utils/videoDirector';
import { clamp } from '../timelineCore';

export interface RailTick {
	atPercent: number;
	major: boolean;
	label: string | null;
}

export interface RailBeat {
	id: string;
	startPercent: number;
	widthPercent: number;
	text: string;
	global: boolean;
}

export interface RailKeyframeMark {
	id: string;
	kind: 'start' | 'end' | 'free';
	atPercent: number;
	thumbUrl: string | null;
	label: string;
	empty: boolean;
}

export interface RailAudioClip {
	id: string;
	startPercent: number;
	widthPercent: number;
	role: string;
	filename: string;
}

export interface ShotRailModel {
	durationSeconds: number;
	ticks: RailTick[];
	lanes: {
		prompt: { beats: RailBeat[]; canAdd: boolean; addDisabledReason: string | null } | null;
		keyframes: { marks: RailKeyframeMark[]; count: number; cap: number | null; canAdd: boolean } | null;
		audio: { clips: RailAudioClip[]; canAdd: boolean } | null;
	};
}

const MINOR_STEP_SECONDS = 0.25;
const EPS = 1e-6;

function emptyShotRail(): ShotRailModel {
	return { durationSeconds: 0, ticks: [{ atPercent: 0, major: true, label: '0s' }], lanes: { prompt: null, keyframes: null, audio: null } };
}

/** '0s' / '1s' … / '5.1s' (one decimal, trailing zero trimmed) -- matches
 * console.html's own ruler labels exactly; the brief's own contract comment
 * ('0.00 s'…) doesn't match what the template actually draws, so the
 * template wins per this wave's "hold to the template literally" rule. */
function formatRailSeconds(seconds: number): string {
	const rounded = Math.round(seconds * 100) / 100;
	if (Number.isInteger(rounded)) return `${rounded}s`;
	const trimmed = rounded.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
	return `${trimmed}s`;
}

function formatFreeKeyframeLabel(seconds: number): string {
	// Same numeral as formatRailSeconds, with a space before the unit (the
	// stage/rail's free-keyframe label idiom, e.g. 'Keyframe — free, 1.8 s').
	return formatRailSeconds(seconds).replace(/s$/, ' s');
}

export function buildRailTicks(durationSeconds: number): RailTick[] {
	if (!(durationSeconds > 0)) return [{ atPercent: 0, major: true, label: '0s' }];
	const majorSeconds: number[] = [];
	for (let s = 0; s <= durationSeconds + EPS; s += 1) {
		const rounded = Math.min(Math.round(s * 1000) / 1000, durationSeconds);
		majorSeconds.push(rounded);
		if (rounded >= durationSeconds - EPS) break;
	}
	const lastRounded = Math.round(durationSeconds * 1000) / 1000;
	if (majorSeconds[majorSeconds.length - 1] !== lastRounded) majorSeconds.push(lastRounded);

	const majorSet = new Set(majorSeconds);
	const ticks: RailTick[] = majorSeconds.map((s) => ({
		atPercent: (s / durationSeconds) * 100,
		major: true,
		label: formatRailSeconds(s)
	}));

	for (let s = MINOR_STEP_SECONDS; s < durationSeconds - EPS; s += MINOR_STEP_SECONDS) {
		const rounded = Math.round(s * 1000) / 1000;
		if (majorSet.has(rounded) || Math.abs(rounded % 1) < EPS) continue;
		ticks.push({ atPercent: (rounded / durationSeconds) * 100, major: false, label: null });
	}

	return ticks.sort((a, b) => a.atPercent - b.atPercent);
}

/** Shot-local seconds from a 0..1 fraction of the rail's own width, snapped
 * to the nearest 0.25s and clamped into [0, durationSeconds] -- the pointer
 * math behind the rail's hover insert-cue and any future drag-to-place. */
export function railTimeFromFraction(rail: ShotRailModel, fraction: number): number {
	const clampedFraction = clamp(fraction, 0, 1);
	const raw = clampedFraction * rail.durationSeconds;
	const snapped = Math.round(raw / MINOR_STEP_SECONDS) * MINOR_STEP_SECONDS;
	return Math.round(clamp(snapped, 0, rail.durationSeconds) * 1000) / 1000;
}

function thumbUrlFor(media: DirectorMediaValue | null, formData: Record<string, unknown> | null | undefined): string | null {
	if (!media) return null;
	const display = resolveDirectorMediaDisplay(media, formData);
	if (display.kind === 'embedded' || display.kind === 'form_ref') return display.media.url ?? null;
	return null;
}

function audioFileLabel(media: DirectorMediaValue | null): string {
	if (!media) return 'No file';
	if ('form_ref' in media) return 'From form';
	const ref = media as MediaRef;
	if (ref.label) return ref.label;
	if (ref.name) return ref.name;
	const path = ref.path ?? '';
	const base = path.split('/').pop() ?? path;
	return base || 'Untitled audio';
}

/** Which shot index a chain 'anywhere' keyframe's/audio clip's film-time
 * position lands in -- mirrors stageModel.ts's (unexported) `chainLandingWindow`,
 * index-only (consoleModel.ts keeps its own copy for the same reason: neither
 * file forks railModel.ts's shot math, they just can't share a private helper
 * across files without exporting it, and stageModel.ts is being deleted this
 * wave anyway). */
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

function deriveChainShotRail(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel,
	shotId: string,
	formData: Record<string, unknown> | null | undefined
): ShotRailModel {
	const index = rail.shots.findIndex((s) => s.id === shotId);
	if (index === -1) return emptyShotRail();
	const block = rail.shots[index];
	const segment = doc.chain.segments[index];
	const durationSeconds = segment.duration;
	const ticks = buildRailTicks(durationSeconds);
	const dc = caps.modes.director;

	const promptLane = {
		// The id MUST be the real segment id, not a synthesized one: selecting
		// this beat (kind 'beat') maps to a 'shot' selection in
		// stage-rail/stageModel.ts's `buildShotModel`, which looks the id up
		// directly in `doc.chain.segments` -- exactly mirroring how a timeline
		// beat's id already IS its `DirectorPromptSegment.id` below.
		// A beat block shows the shot's SHORT derived label (mock: index + a
		// short name, `deriveShotLabel`'s own first-clause/40-char rule --
		// same one the compact row and card head already use for the shot
		// title), never the raw prompt text -- maintainer bug report (09-04):
		// a long prompt must never visibly flow across the lane.
		beats: [
			{
				id: segment.id,
				startPercent: 0,
				widthPercent: 100,
				text: deriveShotLabel(segment.prompt, index),
				global: false
			}
		],
		canAdd: false,
		addDisabledReason: 'Chain shots carry one prompt each.'
	};

	let keyframesLane: ShotRailModel['lanes']['keyframes'] = null;
	if (rail.lanes.keyframes) {
		const marks: RailKeyframeMark[] = [];
		// MUST be `chainEdgeKeyframeId` -- the same id `deriveStageModel`'s
		// `buildKeyframeModel` (via `parseChainEdgeKeyframeId`) and railModel.ts's
		// own film-level edge mirrors use. A synthesized `${id}-leading` id here
		// (the earlier W1 shape) parses as neither a chain-edge id nor a placed
		// `chain.keyframes` entry, so clicking the anchor selected nothing and
		// the stage never showed the keyframe panel (bug: 09-04 maintainer report).
		const startUrl = thumbUrlFor(segment.keyframe, formData);
		marks.push({
			id: chainEdgeKeyframeId('first', segment.id),
			kind: 'start',
			atPercent: 0,
			thumbUrl: startUrl,
			label: 'START',
			empty: startUrl == null
		});
		const endUrl = thumbUrlFor(segment.last_keyframe, formData);
		marks.push({
			id: chainEdgeKeyframeId('last', segment.id),
			kind: 'end',
			atPercent: 100,
			thumbUrl: endUrl,
			label: 'END',
			empty: endUrl == null
		});

		const fps = rail.fps;
		for (const kf of doc.chain.keyframes) {
			if (chainLandingShotIndex(rail, kf.at) !== index) continue;
			const localFrame = Math.max(0, Math.round((kf.at - block.startSeconds) * fps)) + block.overlapInFrames;
			const localSeconds = fps > 0 ? localFrame / fps : 0;
			const atPercent = block.totalFrames > 0 ? clamp((localFrame / block.totalFrames) * 100, 0, 100) : 0;
			marks.push({
				id: kf.id,
				kind: 'free',
				atPercent,
				thumbUrl: thumbUrlFor(kf.media, formData),
				label: formatFreeKeyframeLabel(localSeconds),
				empty: kf.media == null
			});
		}

		const cap = dc?.maxKeyframes ?? null;
		const globalCount = doc.chain.keyframes.length;
		keyframesLane = {
			marks,
			count: marks.filter((m) => m.kind === 'free').length,
			cap,
			canAdd: rail.freePlacementActive && (cap == null || globalCount < cap)
		};
	}

	let audioLane: ShotRailModel['lanes']['audio'] = null;
	if (dc?.audio === true) {
		const shotStart = block.startSeconds;
		const shotEnd = shotStart + durationSeconds;
		const clips: RailAudioClip[] = [];
		for (const a of doc.chain.audio) {
			const clipStart = Math.max(a.start, shotStart);
			const clipEnd = Math.min(a.start + a.length, shotEnd);
			if (clipEnd <= clipStart) continue;
			clips.push({
				id: a.id,
				startPercent: durationSeconds > 0 ? ((clipStart - shotStart) / durationSeconds) * 100 : 0,
				widthPercent: durationSeconds > 0 ? ((clipEnd - clipStart) / durationSeconds) * 100 : 0,
				role: a.role ?? 'condition',
				filename: audioFileLabel(a.media)
			});
		}
		audioLane = { clips, canAdd: true };
	}

	return { durationSeconds, ticks, lanes: { prompt: promptLane, keyframes: keyframesLane, audio: audioLane } };
}

// Same short-label rule as `deriveShotLabel` (first clause, 40 chars) minus
// its shot-specific ordinal fallback -- a beat block always shows one line,
// ellipsized, never the raw prompt text (maintainer bug report, 09-04).
const MAX_BEAT_LABEL = 40;
function shortBeatLabel(text: string, emptyFallback: string): string {
	const trimmed = text.trim();
	if (!trimmed) return emptyFallback;
	const firstClause = trimmed.split(/[,.;\n]/, 1)[0]?.trim() ?? trimmed;
	return firstClause.length > MAX_BEAT_LABEL ? `${firstClause.slice(0, MAX_BEAT_LABEL - 1)}…` : firstClause;
}

function globalFillBeat(start: number, end: number, durationSeconds: number): RailBeat {
	return {
		id: `global-${start.toFixed(3)}-${end.toFixed(3)}`,
		startPercent: durationSeconds > 0 ? (start / durationSeconds) * 100 : 0,
		widthPercent: durationSeconds > 0 ? ((end - start) / durationSeconds) * 100 : 0,
		text: 'Global',
		global: true
	};
}

function deriveTimelineShotRail(
	caps: DirectorCapabilities,
	rail: RailModel,
	shot: DirectorTimelineShot,
	formData: Record<string, unknown> | null | undefined
): ShotRailModel {
	const durationSeconds = rail.totalSeconds;
	const ticks = buildRailTicks(durationSeconds);
	const dc = caps.modes.director;

	const sorted = [...shot.segments].sort((a, b) => a.start - b.start);
	const beats: RailBeat[] = [];
	let cursor = 0;
	for (const seg of sorted) {
		if (seg.start > cursor + EPS) beats.push(globalFillBeat(cursor, seg.start, durationSeconds));
		beats.push({
			id: seg.id,
			startPercent: durationSeconds > 0 ? (seg.start / durationSeconds) * 100 : 0,
			widthPercent: durationSeconds > 0 ? ((seg.end - seg.start) / durationSeconds) * 100 : 0,
			text: shortBeatLabel(seg.text, 'Untitled beat'),
			global: false
		});
		cursor = Math.max(cursor, seg.end);
	}
	if (cursor < durationSeconds - EPS) beats.push(globalFillBeat(cursor, durationSeconds, durationSeconds));

	const promptLane = { beats, canAdd: true, addDisabledReason: null };

	let keyframesLane: ShotRailModel['lanes']['keyframes'] = null;
	if (rail.lanes.keyframes) {
		const marks: RailKeyframeMark[] = shot.keyframes
			.filter((kf) => kf.role === 'free')
			.map((kf) => ({
				id: kf.id,
				kind: 'free' as const,
				atPercent: durationSeconds > 0 ? clamp((kf.start / durationSeconds) * 100, 0, 100) : 0,
				thumbUrl: thumbUrlFor(kf.media, formData),
				label: formatFreeKeyframeLabel(kf.start),
				empty: kf.media == null
			}));

		// START/END anchors always render when this mode's capability opens
		// that edge, EMPTY or not (mirrors the chain anchor's own well --
		// maintainer bug report, 09-04: an empty edge must still be
		// clickable so the stage can offer a media pick). A row is looked up
		// by ROLE, never by id -- a historical document's own id for that
		// role (e.g. the toModelessDirectorValue fold-in's 'kf-first') still
		// resolves; an edge with no row at all uses the deterministic
		// placeholder id, which `withTimelineKeyframeMedia` then mints the
		// new row under on first pick.
		const edgeAllowances = resolveDirectorEdgeAllowances(caps);
		if (edgeAllowances.leadingEdgeAllowed) {
			const existing = shot.keyframes.find((kf) => kf.role === 'first');
			marks.push({
				id: existing?.id ?? timelineEdgeKeyframeId('first', shot.id),
				kind: 'start',
				atPercent: 0,
				thumbUrl: thumbUrlFor(existing?.media ?? null, formData),
				label: 'START',
				empty: (existing?.media ?? null) == null
			});
		}
		if (edgeAllowances.trailingEdgeAllowed) {
			const existing = shot.keyframes.find((kf) => kf.role === 'last');
			marks.push({
				id: existing?.id ?? timelineEdgeKeyframeId('last', shot.id),
				kind: 'end',
				atPercent: 100,
				thumbUrl: thumbUrlFor(existing?.media ?? null, formData),
				label: 'END',
				empty: (existing?.media ?? null) == null
			});
		}

		const freeCount = marks.filter((m) => m.kind === 'free').length;
		const cap = dc?.maxKeyframes ?? null;
		keyframesLane = { marks, count: freeCount, cap, canAdd: rail.freePlacementActive && (cap == null || freeCount < cap) };
	}

	let audioLane: ShotRailModel['lanes']['audio'] = null;
	if (dc?.audio === true) {
		const clips: RailAudioClip[] = shot.audio.map((a) => ({
			id: a.id,
			startPercent: durationSeconds > 0 ? clamp((a.start / durationSeconds) * 100, 0, 100) : 0,
			widthPercent: durationSeconds > 0 ? (a.length / durationSeconds) * 100 : 0,
			role: a.role ?? 'condition',
			filename: audioFileLabel(a.media)
		}));
		audioLane = { clips, canAdd: true };
	}

	return { durationSeconds, ticks, lanes: { prompt: promptLane, keyframes: keyframesLane, audio: audioLane } };
}

export function deriveShotRail(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData?: Record<string, unknown> | null
): ShotRailModel {
	const rail = deriveRailModel(doc, caps, shotId);
	if (rail.routing === 'chain') return deriveChainShotRail(doc, caps, rail, shotId, formData);
	const shot = doc.timeline.shots.find((s) => s.id === shotId) ?? doc.timeline.shots[0];
	if (!shot) return emptyShotRail();
	return deriveTimelineShotRail(caps, rail, shot, formData);
}
