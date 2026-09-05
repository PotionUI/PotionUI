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
	DirectorPromptSegment,
	DirectorTimelineShot
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

// A `doc.timeline.shots` array is never empty in a normalized document (see
// `normalizeDirectorValue`), but `deriveRailModel` is a pure function of
// whatever it's handed -- this keeps it from throwing on a hand-built test
// fixture that skipped normalization.
const EMPTY_TIMELINE_SHOT: DirectorTimelineShot = {
	id: 'empty-shot',
	duration: 0,
	continue_from_previous: false,
	segments: [],
	keyframes: [],
	audio: [],
	ic_lora: []
};

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

/** Converts a chain shot's LOCAL time (0..that shot's own generated length,
 * INCLUDING any leading overlap it inherits from a continue join -- the same
 * coordinate `stageModel.ts`'s `chainLandingWindow`/`StageKeyframeModel
 * .atSeconds` and shotRailModel.ts's rail both already use) back to the
 * chain's FILM/output time `chain.keyframes[].at` is stored in. Inverse of
 * `chainLandingWindow`'s own local-frame math -- shared by ShotConsole.svelte
 * (rail drag) and StageKeyframe.svelte (Time field / Snap chips) so a
 * snapped/typed/dragged value can never resolve to a different shot's window
 * (maintainer bug report, 09-04). */
export function chainFilmSecondsFromLocal(rail: RailModel, blockIndex: number, localSeconds: number): number {
	const block = rail.shots[blockIndex];
	const fps = rail.fps;
	const localFrame = Math.round(localSeconds * fps);
	const outputFrame = Math.max(0, localFrame - block.overlapInFrames);
	return block.startSeconds + (fps > 0 ? outputFrame / fps : 0);
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

/** Repositions one timeline keyframe (first/last/free role) on the named
 * shot. Pure. */
export function withTimelineKeyframeAt(doc: VideoDirectorValue, shotId: string, id: string, atSeconds: number): VideoDirectorValue {
	return {
		...doc,
		timeline: {
			...doc.timeline,
			shots: doc.timeline.shots.map((s) =>
				s.id === shotId ? { ...s, keyframes: s.keyframes.map((k) => (k.id === id ? { ...k, start: atSeconds } : k)) } : s
			)
		}
	};
}

/** Moves one LTX prompt block's start or end edge on the named shot. Pure;
 * the caller is expected to have already clamped `seconds` via
 * `resizeTimelineBlockEdge`. */
export function withTimelineSegmentEdge(
	doc: VideoDirectorValue,
	shotId: string,
	id: string,
	edge: 'start' | 'end',
	seconds: number
): VideoDirectorValue {
	return {
		...doc,
		timeline: {
			...doc.timeline,
			shots: doc.timeline.shots.map((s) =>
				s.id === shotId ? { ...s, segments: s.segments.map((seg) => (seg.id === id ? { ...seg, [edge]: seconds } : seg)) } : s
			)
		}
	};
}

// ─── MiniMax-H3 window geometry ─────────────────────────────────────────────
// Pure TS port of `resolve_window_geometry`
// (src/pipelines/pipes/generator/video_minimax_h3/windows.py, itself backed
// by geometry.py's `17n+5` lattice arithmetic) -- selected below only when
// the preset's `video_director.family` capability reads `"minimax_h3"`
// (parseDirectorCapabilities -> DirectorCapabilities.family), so the rail
// shows the SAME block widths and seam offsets the backend compiler
// (compile.py's `_effective_segment_durations`) and the generator itself
// actually run, instead of a raw `duration * fps` that ignores the video
// VAE's frame-count snap and its continuation-overlap trim.
//
// Requested (`duration * fps`) vs emitted (this module's `frames`/
// `contributedFrames`) timing: MiniMax-H3 snaps a segment's requested frame
// count UP to the video VAE's `17n+5` lattice before it ever generates, and a
// continuation shot's leading `overlapFrames` REPLAY the previous shot's tail
// rather than adding new footage -- see docs/video-director.md's "Requested
// vs. emitted timing (MiniMax-H3)" section. Every other family (Wan
// included) keeps the legacy raw axis below unchanged: Wan's own effective
// per-shot overlap depends on a generation-time pipe config value
// (`motion_latent_count`) that never travels with this document, so there is
// no document-only way to port Wan's real geometry here yet -- the raw axis
// is left in place as the existing (unfixed) approximation, not claimed
// correct for it.
const H3_FAMILY = 'minimax_h3';
const H3_FRAMES_PER_CHUNK = 17;
const H3_LATENTS_PER_CHUNK = 5;
const H3_LATENT_FRAME_PIXEL_SPANS = [1, 4, 4, 4, 4];

/** Snaps `requested` UP to the next `17n+5` the video VAE can encode. */
function h3AlignNumFrames(requested: number): number {
	let frames = Math.max(1, Math.round(requested));
	while (frames % H3_FRAMES_PER_CHUNK !== H3_LATENTS_PER_CHUNK) frames += 1;
	return frames;
}

/** Latent frame count for an already-aligned `frames` (`5n+2`). */
function h3VideoLatentNumFrames(frames: number): number {
	return ((frames - H3_LATENTS_PER_CHUNK) / H3_FRAMES_PER_CHUNK) * H3_LATENTS_PER_CHUNK + 2;
}

/** Pixel frames the first `numLatents` latent frames of a clip cover. */
function h3HeadFramesForLatents(numLatents: number): number {
	let total = 0;
	for (let i = 0; i < numLatents; i++) total += H3_LATENT_FRAME_PIXEL_SPANS[i % H3_LATENT_FRAME_PIXEL_SPANS.length];
	return total;
}

/** Trailing latent frames of an aligned clip needed to cover at least
 * `numFrames` pixel frames -- walked from the TAIL, a different phase of the
 * (1,4,4,4,4) cycle than the head walk above (see geometry.py's docstring). */
function h3TailLatentsForFrames(numFrames: number): number {
	let covered = 0;
	let count = 0;
	while (covered < numFrames) {
		const index = (((1 - count) % H3_LATENT_FRAME_PIXEL_SPANS.length) + H3_LATENT_FRAME_PIXEL_SPANS.length) % H3_LATENT_FRAME_PIXEL_SPANS.length;
		covered += H3_LATENT_FRAME_PIXEL_SPANS[index];
		count += 1;
	}
	return count;
}

interface H3SegmentGeometry {
	frames: number;
	overlapFrames: number;
}

/** One segment's frame/overlap geometry, mirroring `resolve_window_geometry`'s
 * per-segment body exactly (source dispatch, `min(default, numLatentFrames -
 * 1)` clamp, head/tail conversions). `continuation` is the CAPABILITY default
 * (`caps.modes.director.continuation`), not the document's own
 * `chain.continuation` -- the wire submission takes `source` from there too
 * (see `buildChainDirectorSubmission`). */
function resolveH3SegmentGeometry(
	requestedFrames: number,
	isContinue: boolean,
	overlapFramesSetting: number,
	continuationSource: 'tail_frames' | 'last_frame' | undefined
): H3SegmentGeometry {
	const frames = h3AlignNumFrames(requestedFrames);
	const numLatentFrames = h3VideoLatentNumFrames(frames);
	let overlapLatentsDefault = 0;
	if (continuationSource === 'last_frame') overlapLatentsDefault = 1;
	else if (continuationSource === 'tail_frames') overlapLatentsDefault = h3TailLatentsForFrames(Math.max(0, overlapFramesSetting));
	const overlapLatents = isContinue ? Math.min(overlapLatentsDefault, numLatentFrames - 1) : 0;
	return { frames, overlapFrames: h3HeadFramesForLatents(overlapLatents) };
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
	const isH3 = caps.family === H3_FAMILY;
	// MiniMax-H3 refs mode: continuation and the reference pool can't coexist
	// (normalize.py's `chain_continuation_disabled`) -- every shot is an
	// independent hard cut, so the structural derivation is overridden rather
	// than consulted.
	const continuationDisabled = directorCap?.continuationDisabled === true;

	const shots: RailShotBlock[] = [];
	const seams: RailSeam[] = [];
	let cumulativeFrames = 0;

	chain.segments.forEach((segment, index) => {
		const requestedFrames = Math.round(segment.duration * fps);
		// A segment continues from its predecessor iff it resolves to the
		// 'chain' sub-type -- NOT merely "isn't 't2v'": once a non-first
		// segment can carry its own leading (or leading+trailing) media, it can
		// resolve to 'i2v'/'flf' too, and either of those is a FRESH open, the
		// same as an explicit 't2v' cut (see chainSegmentEdgeAllowances).
		const isContinue = !continuationDisabled && index > 0 && deriveChainSegmentSubType(segment, index) === 'chain';
		const h3Geometry = isH3
			? resolveH3SegmentGeometry(requestedFrames, isContinue, overlapSetting, directorCap?.continuation?.source)
			: null;
		const totalFrames = h3Geometry ? h3Geometry.frames : requestedFrames;
		const overlapInFrames = h3Geometry ? h3Geometry.overlapFrames : isContinue ? Math.min(overlapSetting, totalFrames) : 0;
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
	fps: number,
	shot: DirectorTimelineShot,
	caps: DirectorCapabilities
): Omit<RailModel, 'routing' | 'lanes' | 'freePlacementActive'> {
	const directorCap = caps.modes.director;
	const sorted = sortByStart(shot.segments);

	const contentEnd = Math.max(
		0,
		...sorted.map((s) => s.end),
		...shot.keyframes.map((k) => k.start),
		...shot.audio.map((a) => a.start + a.length)
	);
	const totalSeconds = contentEnd > 0 ? contentEnd : Math.max(shot.duration, 1);
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

	const keyframes: RailKeyframe[] = shot.keyframes.map((kf: DirectorKeyframe) => {
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

	const audio: RailAudioClip[] = shot.audio.map((a: DirectorAudioSegment) => ({
		id: a.id,
		startSeconds: a.start,
		endSeconds: a.start + a.length,
		hasMedia: a.media != null,
		role: a.role ?? null
	}));

	const icLoraEnabled = directorCap?.icLora === true;
	const firstIcLora = shot.ic_lora[0] ?? null;
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

/**
 * `timelineShotId` selects WHICH independent LTX shot (`doc.timeline.shots`)
 * this rail renders -- irrelevant (and ignored) for chain routing, where each
 * `chain.segments` entry already IS a rail shot block. Unresolvable/omitted
 * on a timeline document resolves to the first shot -- every call site that
 * cares about a specific shot (the Shot Console's expanded card) always
 * passes its own `shot.id`; this fallback exists only so a caller that has no
 * shot context yet (a bare `deriveDirectorMode`-style read) still gets a
 * valid model rather than an empty one.
 */
export function deriveRailModel(doc: VideoDirectorValue, caps: DirectorCapabilities, timelineShotId?: string): RailModel {
	const directorCap = caps.modes.director;
	const routing: RailRouting = caps.segmentRouting ? 'chain' : 'timeline';
	const timelineShot = routing === 'timeline' ? (doc.timeline.shots.find((s) => s.id === timelineShotId) ?? doc.timeline.shots[0]) : null;
	const body =
		routing === 'chain' ? deriveChainRail(doc, caps) : deriveTimelineRail(doc.timeline.fps, timelineShot ?? EMPTY_TIMELINE_SHOT, caps);

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
