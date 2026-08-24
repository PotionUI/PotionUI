// Pure geometry/derivation layer for the Stage & Rail rework's RAIL. Turns a
// VideoDirectorValue + DirectorCapabilities into the flat,
// render-ready shape Rail.svelte draws -- no Svelte imports, no Date.now/
// Math.random, byte-deterministic for a given (doc, caps) pair so a $derived
// in the component never diverges from what a test asserts.
//
// Builds ON src/lib/utils/videoDirector.ts's document model; does not fork
// it. The chain/timeline split below mirrors buildDirectorSubmission's own
// caps.segmentRouting branch in that file.

import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorModeCapability,
	DirectorReferencesCapability,
	ChainKeyframe,
	DirectorKeyframe,
	DirectorAudioSegment,
	DirectorPromptSegment
} from '$lib/types/videoDirector';
import {
	chainEdgeKeyframeId,
	chainKeyframeWindow,
	chainSegmentEdgeAllowances,
	deriveChainSegmentSubType,
	deriveDirectorMode,
	resolveDirectorEdgeAllowances,
	DEFAULT_MAX_KEYFRAMES
} from '$lib/utils/videoDirector';
import { sortByStart, neighborBounds, trimSegmentLeft, trimSegmentRight, clamp } from '../timelineCore';

export type RailObjectKind = 'shot' | 'seam' | 'keyframe' | 'audio' | 'ic_lora';

export interface RailSelectionId {
	kind: RailObjectKind;
	id: string;
}

export type RailRouting = 'chain' | 'timeline';

/** Snap/free landing time-tolerance for both display ("is this keyframe
 * currently sitting on a boundary") and interactive drag resolution. Time-
 * based rather than pixel-based -- the pixel radius from the design brief
 * (~6px) is a view-layer concern the component converts to seconds using its
 * own zoom before calling `resolveKeyframeSnap`. */
export const SNAP_EPSILON_SECONDS = 0.05;

export interface RailShotBlock {
	id: string;
	index: number;
	label: string;
	/** Cumulative start of this shot's contributed span, i.e. where its block
	 * begins on the rail. */
	startFrame: number;
	startSeconds: number;
	/** The block's RENDER width: frames this shot contributes to the final
	 * output. For a shot after a continue join this is totalFrames minus the
	 * overlap the join consumed -- never the shot's own generated length. */
	contributedFrames: number;
	contributedSeconds: number;
	/** The shot's own generated length -- duration(s) * fps, rounded. Equal
	 * to contributedFrames unless an incoming continue join subtracts overlap. */
	totalFrames: number;
	hasOverlapIn: boolean;
	overlapInFrames: number;
	/** Per-shot generator cap (chain routing only; null for timeline blocks
	 * and for a mode that declares none). */
	capFrames: number | null;
	/** How many frames totalFrames exceeds capFrames by; 0 when within cap or
	 * capFrames is null. */
	overCapBy: number;
	/** Where the cap boundary falls along this block's own [0, contributedFrames]
	 * span, as a 0..1 fraction -- only set when overCapBy > 0 (nothing to draw
	 * otherwise). */
	capLocalFraction: number | null;
}

export interface RailSeam {
	id: string;
	/** Index of the shot ending at this seam; the seam sits between
	 * shots[beforeShotIndex] and shots[beforeShotIndex + 1]. */
	beforeShotIndex: number;
	kind: 'continue' | 'cut';
	overlapFrames: number;
	atFrame: number;
	atSeconds: number;
	/** The hatched "shared frames" shoulder drawn over the tail of the
	 * PREVIOUS block, [shoulderStartFrame, atFrame]. Null for a cut seam. */
	shoulderStartFrame: number | null;
	shoulderStartSeconds: number | null;
}

export interface RailKeyframe {
	id: string;
	atSeconds: number;
	atFrame: number;
	hasMedia: boolean;
	role: 'first' | 'last' | 'free' | 'keyframe';
	snapped: boolean;
	snappedToLabel: string | null;
}

export interface RailSnapTarget {
	label: string;
	atSeconds: number;
}

export interface RailAudioClip {
	id: string;
	startSeconds: number;
	endSeconds: number;
	hasMedia: boolean;
	role: 'mux' | 'condition' | null;
}

export interface RailIcLoraHead {
	id: string;
	hasLora: boolean;
	hasReference: boolean;
}

export interface RailLanes {
	shots: boolean;
	keyframes: boolean;
	audio: boolean;
	icLora: boolean;
	references: boolean;
}

export interface RailModel {
	routing: RailRouting;
	lanes: RailLanes;
	fps: number;
	/** Content-extent total that every lane position is a 0..1 fraction of --
	 * the chain's summed contributed length, or (for a timeline) the furthest
	 * point any block/keyframe/audio clip reaches. Not the preset's max
	 * duration; an unused tail of allowed duration is never drawn. */
	totalFrames: number;
	totalSeconds: number;
	maxKeyframes: number | null;
	maxSegments: number | null;
	canAddShot: boolean;
	/** Whether the free-keyframe "+" affordance (and free/'anywhere' dragging)
	 * should be offered right now. Chain routing (Wan/H3) gates this on
	 * `resolveDirectorEdgeAllowances`'s `freePlacementAllowed` capability AND
	 * the document currently being director-shaped (`deriveDirectorMode(doc,
	 * caps) === 'director'`, i.e. 2+ shots, or existing timed
	 * keyframes/audio/ic_lora in a foreign doc) -- a single-shot t2v/i2v/flf
	 * chain document never offers it -- only its own locked edge keyframes
	 * (see `keyframes` below) -- escalating to it is the existing "+ Add
	 * shot" affordance (`canAddShot`), the moment a second shot exists
	 * `deriveDirectorMode` reads 'director' and this flips on. Timeline
	 * routing (LTX) has no such escalation path -- a preset that declares a
	 * director block with arbitrary-keyframe conditioning offers this lane
	 * from a bare/t2v-shaped document too, on `freePlacementAllowed` alone,
	 * so a user isn't locked out of the only affordance that would ever grow
	 * the document past one shot. */
	freePlacementActive: boolean;
	/** Whole-video generator frame cap (timeline/LTX's causal-VAE lattice cap;
	 * null when the mode declares none). Distinct from a chain shot's
	 * per-block capFrames. */
	maxFrames: number | null;
	totalOverCapBy: number;
	shots: RailShotBlock[];
	seams: RailSeam[];
	/** Chain routing prepends read-model mirrors of the shot-edit wells (ids
	 * from `chainEdgeKeyframeId`, no separate storage -- see `deriveChainRail`)
	 * so a filled leading/trailing frame always shows here too, locked, even
	 * when `freePlacementActive` is off. */
	keyframes: RailKeyframe[];
	snapTargets: RailSnapTarget[];
	audio: RailAudioClip[];
	icLora: RailIcLoraHead | null;
	/** The active mode's whole-form reference-pool stance ('references'
	 * capability); null when the mode has no reference pool at all. Mirrors
	 * `lanes.references` -- present here too so a component can read the
	 * capability value itself (e.g. to word the strip) without re-deriving
	 * from `caps`. */
	referencesCapability: DirectorReferencesCapability;
	/** Names of the form fields the reference-pool strip reads via
	 * `collectFormMediaOptions`; empty when `referencesCapability` is null. */
	referenceFields: string[];
}

function safeFps(fps: number): number {
	return Number.isFinite(fps) && fps > 0 ? fps : 0;
}

function framesToSeconds(frames: number, fps: number): number {
	return fps > 0 ? frames / fps : 0;
}

/** A shot has no name field in the document -- only a prompt. Mirrors the
 * mock's per-shot titles without inventing new document schema: the first
 * clause of the prompt stands in for a name, falling back to an ordinal. */
export function deriveShotLabel(prompt: string, index: number): string {
	const text = prompt.trim();
	if (!text) return `Shot ${index + 1}`;
	const firstClause = text.split(/[,.;\n]/, 1)[0]?.trim() ?? text;
	const MAX = 40;
	return firstClause.length > MAX ? `${firstClause.slice(0, MAX - 1)}…` : firstClause;
}

/** A 'first'/'last' keyframe was placed by filling a Stage gate well and is
 * pinned to that shot edge -- repositioning it (rail drag, or the Stage
 * detail panel's "snap to") would silently disagree with what the gate still
 * shows there. Only 'free' (and chain's own 'keyframe' free-placement role)
 * may move. Media on a locked keyframe can still be cleared/replaced --
 * this only ever gates POSITION. */
export function isKeyframeLocked(role: RailKeyframe['role']): boolean {
	return role === 'first' || role === 'last';
}

function resolveKeyframeSnap(
	atSeconds: number,
	targets: RailSnapTarget[],
	epsilonSeconds: number = SNAP_EPSILON_SECONDS
): { snapped: boolean; label: string | null } {
	let best: RailSnapTarget | null = null;
	let bestDist = Infinity;
	for (const target of targets) {
		const dist = Math.abs(target.atSeconds - atSeconds);
		if (dist < bestDist) {
			bestDist = dist;
			best = target;
		}
	}
	if (best && bestDist <= epsilonSeconds) return { snapped: true, label: best.label };
	return { snapped: false, label: null };
}

/** Interactive helper for dragging a keyframe on the rail: snaps to the
 * nearest target within `epsilonSeconds`, otherwise clamps into [0, windowSeconds]. */
export function resolveKeyframeDrag(
	proposedSeconds: number,
	targets: RailSnapTarget[],
	windowSeconds: number,
	epsilonSeconds: number = SNAP_EPSILON_SECONDS
): number {
	const clamped = clamp(proposedSeconds, 0, Math.max(0, windowSeconds));
	const { snapped, label } = resolveKeyframeSnap(clamped, targets, epsilonSeconds);
	if (!snapped || label == null) return clamped;
	const target = targets.find((t) => t.label === label);
	return target ? target.atSeconds : clamped;
}

/** Interactive helper for dragging an LTX block's start/end edge: clamps
 * against the neighbouring block rather than crossing it (v1 chooses clamp
 * over "push the neighbour" -- simpler, and a block can never silently steal
 * a neighbour's time from an edge drag alone). */
export function resizeTimelineBlockEdge(
	segments: DirectorPromptSegment[],
	id: string,
	edge: 'start' | 'end',
	proposedSeconds: number,
	totalDuration: number
): number {
	const sorted = sortByStart(segments);
	const seg = sorted.find((s) => s.id === id);
	if (!seg) return proposedSeconds;
	const { leftBound, rightBound } = neighborBounds(sorted, id, totalDuration);
	return edge === 'start'
		? trimSegmentLeft(seg.start, seg.end, (proposedSeconds - seg.start) * 1, 1, leftBound)
		: trimSegmentRight(seg.start, seg.end, (proposedSeconds - seg.end) * 1, 1, rightBound);
}

/** Repositions one 'anywhere' chain keyframe -- the only field a rail drag
 * changes on it. Pure; returns a new document, never mutates `doc`. */
export function withChainKeyframeAt(doc: VideoDirectorValue, id: string, atSeconds: number): VideoDirectorValue {
	return {
		...doc,
		chain: {
			...doc.chain,
			keyframes: doc.chain.keyframes.map((k) => (k.id === id ? { ...k, at: atSeconds } : k))
		}
	};
}

/** Repositions one timeline keyframe (first/last/free role). Pure. */
export function withTimelineKeyframeAt(doc: VideoDirectorValue, id: string, atSeconds: number): VideoDirectorValue {
	return {
		...doc,
		timeline: {
			...doc.timeline,
			keyframes: doc.timeline.keyframes.map((k) => (k.id === id ? { ...k, start: atSeconds } : k))
		}
	};
}

/** Moves one LTX prompt block's start or end edge. Pure; the caller is
 * expected to have already clamped `seconds` via `resizeTimelineBlockEdge`. */
export function withTimelineSegmentEdge(
	doc: VideoDirectorValue,
	id: string,
	edge: 'start' | 'end',
	seconds: number
): VideoDirectorValue {
	return {
		...doc,
		timeline: {
			...doc.timeline,
			segments: doc.timeline.segments.map((s) => (s.id === id ? { ...s, [edge]: seconds } : s))
		}
	};
}

function deriveChainRail(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities
): Omit<RailModel, 'routing' | 'lanes' | 'freePlacementActive'> {
	const chain = doc.chain;
	const directorCap = caps.modes.director;
	const fps = safeFps(chain.fps);
	const capFrames = directorCap?.maxFramesPerSegment ?? null;
	const overlapSetting = Math.max(0, chain.continuation.overlap_frames);
	// MiniMax-H3 refs mode: continuation and the reference pool can't coexist
	// (normalize.py's `chain_continuation_disabled`) -- every shot is an
	// independent hard cut, so the structural derivation is overridden rather
	// than consulted.
	const continuationDisabled = directorCap?.continuationDisabled === true;

	const shots: RailShotBlock[] = [];
	const seams: RailSeam[] = [];
	let cumulativeFrames = 0;

	chain.segments.forEach((segment, index) => {
		const totalFrames = Math.round(segment.duration * fps);
		// A segment continues from its predecessor iff it resolves to the
		// 'chain' sub-type -- NOT merely "isn't 't2v'": once a non-first
		// segment can carry its own leading (or leading+trailing) media, it can
		// resolve to 'i2v'/'flf' too, and either of those is a FRESH open, the
		// same as an explicit 't2v' cut (see chainSegmentEdgeAllowances).
		const isContinue = !continuationDisabled && index > 0 && deriveChainSegmentSubType(segment, index) === 'chain';
		const overlapInFrames = isContinue ? Math.min(overlapSetting, totalFrames) : 0;
		const contributedFrames = Math.max(0, totalFrames - overlapInFrames);
		const startFrame = cumulativeFrames;

		const overCapBy = capFrames != null ? Math.max(0, totalFrames - capFrames) : 0;
		const capLocalFraction =
			overCapBy > 0 && contributedFrames > 0
				? clamp((capFrames! - overlapInFrames) / contributedFrames, 0, 1)
				: null;

		shots.push({
			id: segment.id,
			index,
			label: deriveShotLabel(segment.prompt, index),
			startFrame,
			startSeconds: framesToSeconds(startFrame, fps),
			contributedFrames,
			contributedSeconds: framesToSeconds(contributedFrames, fps),
			totalFrames,
			hasOverlapIn: isContinue,
			overlapInFrames,
			capFrames,
			overCapBy,
			capLocalFraction
		});

		if (index > 0) {
			const atFrame = startFrame;
			seams.push({
				id: `seam-${chain.segments[index - 1].id}-${segment.id}`,
				beforeShotIndex: index - 1,
				kind: isContinue ? 'continue' : 'cut',
				overlapFrames: overlapInFrames,
				atFrame,
				atSeconds: framesToSeconds(atFrame, fps),
				shoulderStartFrame: isContinue ? atFrame - overlapInFrames : null,
				shoulderStartSeconds: isContinue ? framesToSeconds(atFrame - overlapInFrames, fps) : null
			});
		}

		cumulativeFrames += contributedFrames;
	});

	const totalFrames = cumulativeFrames;
	const totalSeconds = framesToSeconds(totalFrames, fps);

	const snapTargets: RailSnapTarget[] = [
		{ label: 'Start', atSeconds: 0 },
		...seams.map((seam) => ({
			label: `Join ${seam.beforeShotIndex + 1}→${seam.beforeShotIndex + 2}`,
			atSeconds: seam.atSeconds
		})),
		{ label: 'End', atSeconds: totalSeconds }
	];

	const keyframeWindow = chainKeyframeWindow(chain);
	const placedKeyframes: RailKeyframe[] = chain.keyframes.map((kf: ChainKeyframe) => {
		const { snapped, label } = resolveKeyframeSnap(kf.at, snapTargets);
		return {
			id: kf.id,
			atSeconds: kf.at,
			atFrame: Math.round(kf.at * fps),
			hasMedia: kf.media != null,
			role: 'keyframe',
			snapped,
			snappedToLabel: label
		};
	});

	// Read-model mirrors of the shot-edit wells (stageModel.ts's
	// chainLeadingGate/chainTrailingGate) -- ids carry the segment id (see
	// chainEdgeKeyframeId), no separate storage: recomputed from
	// segment.keyframe/segment.last_keyframe on every render, never minted.
	// This is what makes filling the well and filling the lane entry
	// (buildKeyframeModel's chain-edge branch) the same write. One pair per
	// segment that carries edge media -- any segment can, once it opens/closes
	// fresh (chainSegmentEdgeAllowances), not just segment 0 / a lone segment.
	// Role is keyed off the EDGE ('first' vs 'last'), never the segment's own
	// index -- every edge mirror is locked to its shot boundary the same way
	// (isKeyframeLocked reads role === 'first' | 'last'), whether that shot is
	// segment 0, the last one, or a fresh-open/fresh-close shot in the middle.
	const edgeKeyframes: RailKeyframe[] = [];
	chain.segments.forEach((segment, index) => {
		if (segment.keyframe) {
			edgeKeyframes.push({
				id: chainEdgeKeyframeId('first', segment.id),
				atSeconds: shots[index].startSeconds,
				atFrame: shots[index].startFrame,
				hasMedia: true,
				role: 'first',
				snapped: true,
				snappedToLabel: index === 0 ? 'Start' : shots[index].label
			});
		}
		if (segment.last_keyframe) {
			const atFrame = shots[index].startFrame + shots[index].contributedFrames;
			edgeKeyframes.push({
				id: chainEdgeKeyframeId('last', segment.id),
				atSeconds: framesToSeconds(atFrame, fps),
				atFrame,
				hasMedia: true,
				role: 'last',
				snapped: true,
				snappedToLabel: index === chain.segments.length - 1 ? 'End' : shots[index].label
			});
		}
	});
	const keyframes: RailKeyframe[] = [...edgeKeyframes, ...placedKeyframes];

	const audio: RailAudioClip[] = chain.audio.map((a: DirectorAudioSegment) => ({
		id: a.id,
		startSeconds: a.start,
		endSeconds: a.start + a.length,
		hasMedia: a.media != null,
		role: a.role ?? null
	}));

	const maxSegments = directorCap?.maxSegments ?? null;
	const maxFrames = caps.maxFrames;
	const totalOverCapBy = maxFrames != null ? Math.max(0, totalFrames - maxFrames) : 0;

	return {
		fps,
		totalFrames,
		totalSeconds,
		maxKeyframes: directorCap?.maxKeyframes ?? (directorCap?.keyframes === 'anywhere' ? DEFAULT_MAX_KEYFRAMES : null),
		maxSegments,
		canAddShot: maxSegments == null || shots.length < maxSegments,
		maxFrames,
		totalOverCapBy,
		shots,
		seams,
		keyframes,
		snapTargets,
		audio,
		icLora: null,
		referencesCapability: caps.references,
		referenceFields: caps.referenceFields
	};
}

function deriveTimelineRail(
	timeline: VideoDirectorValue['timeline'],
	caps: DirectorCapabilities
): Omit<RailModel, 'routing' | 'lanes' | 'freePlacementActive'> {
	const directorCap = caps.modes.director;
	const fps = safeFps(timeline.fps);
	const sorted = sortByStart(timeline.segments);

	const contentEnd = Math.max(
		0,
		...sorted.map((s) => s.end),
		...timeline.keyframes.map((k) => k.start),
		...timeline.audio.map((a) => a.start + a.length)
	);
	const totalSeconds = contentEnd > 0 ? contentEnd : Math.max(timeline.duration, 1);
	const totalFrames = Math.round(totalSeconds * fps);

	const shots: RailShotBlock[] = sorted.map((segment, index) => {
		const startFrame = Math.round(segment.start * fps);
		const spanFrames = Math.max(0, Math.round((segment.end - segment.start) * fps));
		return {
			id: segment.id,
			index,
			label: deriveShotLabel(segment.text, index),
			startFrame,
			startSeconds: segment.start,
			contributedFrames: spanFrames,
			contributedSeconds: segment.end - segment.start,
			totalFrames: spanFrames,
			hasOverlapIn: false,
			overlapInFrames: 0,
			// A timeline block has no per-block generator cap -- the whole
			// generation's frame lattice (globalMaxFrames) is the only ceiling,
			// tracked at the RailModel level via totalOverCapBy.
			capFrames: null,
			overCapBy: 0,
			capLocalFraction: null
		};
	});

	const boundaryTargets = new Map<number, string>();
	boundaryTargets.set(0, 'Start');
	sorted.forEach((segment, index) => {
		if (!boundaryTargets.has(segment.start)) boundaryTargets.set(segment.start, `Block ${index + 1} start`);
		if (!boundaryTargets.has(segment.end)) boundaryTargets.set(segment.end, `Block ${index + 1} end`);
	});
	boundaryTargets.set(totalSeconds, 'End');
	const snapTargets: RailSnapTarget[] = [...boundaryTargets.entries()]
		.map(([atSeconds, label]) => ({ atSeconds, label }))
		.sort((a, b) => a.atSeconds - b.atSeconds);

	const keyframes: RailKeyframe[] = timeline.keyframes.map((kf: DirectorKeyframe) => {
		const { snapped, label } = resolveKeyframeSnap(kf.start, snapTargets);
		return {
			id: kf.id,
			atSeconds: kf.start,
			atFrame: Math.round(kf.start * fps),
			hasMedia: kf.media != null,
			role: kf.role,
			snapped,
			snappedToLabel: label
		};
	});

	const audio: RailAudioClip[] = timeline.audio.map((a: DirectorAudioSegment) => ({
		id: a.id,
		startSeconds: a.start,
		endSeconds: a.start + a.length,
		hasMedia: a.media != null,
		role: a.role ?? null
	}));

	const icLoraEnabled = directorCap?.icLora === true;
	const firstIcLora = timeline.ic_lora[0] ?? null;
	const icLora: RailIcLoraHead | null = icLoraEnabled
		? {
				id: firstIcLora?.id ?? 'ic-lora-head',
				hasLora: firstIcLora?.lora != null,
				hasReference: firstIcLora?.ref_media != null
			}
		: null;

	const maxFrames = caps.maxFrames;
	const totalOverCapBy = maxFrames != null ? Math.max(0, totalFrames - maxFrames) : 0;

	return {
		fps,
		totalFrames,
		totalSeconds,
		maxKeyframes: directorCap?.maxKeyframes ?? null,
		maxSegments: null,
		canAddShot: true,
		maxFrames,
		totalOverCapBy,
		shots,
		seams: [],
		keyframes,
		snapTargets,
		audio,
		icLora,
		referencesCapability: caps.references,
		referenceFields: caps.referenceFields
	};
}

export function deriveRailModel(doc: VideoDirectorValue, caps: DirectorCapabilities): RailModel {
	const directorCap = caps.modes.director;
	const routing: RailRouting = caps.segmentRouting ? 'chain' : 'timeline';
	const body = routing === 'chain' ? deriveChainRail(doc, caps) : deriveTimelineRail(doc.timeline, caps);

	// Composition-scoped, not just capability-scoped: a single-shot t2v/i2v/flf
	// document (deriveDirectorMode reads anything but 'director') offers ONLY
	// that shape's own locked edge affordances -- no audio/ic-lora lane, even
	// when the mode's capability declares them. Those only open once the
	// document is actually director-shaped (2+ shots, or existing timed
	// keyframes/audio/ic_lora on a foreign doc -- which already forces
	// deriveDirectorMode to 'director' by construction, see singleShotEdges in
	// videoDirector.ts, so "never hide existing content" falls out of this for
	// free rather than needing its own check).
	const isDirectorShaped = deriveDirectorMode(doc, caps) === 'director';
	const freePlacementAllowed = resolveDirectorEdgeAllowances(caps).freePlacementAllowed;
	// Free placement follows the same composition gate for chain routing --
	// its only escalation path is "+ Add shot". Timeline routing (LTX) has no
	// such path: a bare/t2v-shaped document is exactly the state a user is in
	// before ever placing a keyframe, so gating the lane on isDirectorShaped
	// there is a chicken-and-egg lockout. A real LTX preset declares
	// max_keyframes with no `keyframes` field (parses to 'none'), so
	// freePlacementAllowed already IS "director declared at all" for timeline
	// routing -- see resolveDirectorEdgeAllowances's doc comment.
	const freePlacementActive = routing === 'timeline' ? freePlacementAllowed : freePlacementAllowed && isDirectorShaped;

	const lanes: RailLanes = {
		shots: true,
		// The lane itself still renders with just the locked edge mirrors
		// (chain's chainEdgeKeyframeId entries, timeline's role first/last) even
		// when free placement isn't active -- only the ADD affordance is
		// composition-gated (freePlacementActive, consumed by Rail.svelte).
		keyframes: body.keyframes.length > 0 || freePlacementActive,
		audio: directorCap?.audio === true && isDirectorShaped,
		icLora: directorCap?.icLora === true && isDirectorShaped,
		references: caps.references != null
	};

	return { routing, lanes, freePlacementActive, ...body };
}
