// Pure derivation + reducer layer for the Stage & Rail rework's STAGE. Turns a
// VideoDirectorValue + DirectorCapabilities + the rail's current selection into
// the single object the stage renders large -- a shot, a join, a keyframe, an
// audio clip, the IC-LoRA head, or nothing. No Svelte imports, no Date.now/
// Math.random; byte-deterministic for a given (doc, caps, selection) triple.
//
// Builds ON railModel.ts's RailModel rather than re-deriving frame/seam math:
// deriveStageModel calls deriveRailModel internally and cross-references its
// shots/seams/keyframes, exactly as railSelection intends ("a later Stage
// phase reads this store"). Never forks videoDirector.ts's document shapes --
// every mutation below is either an existing applyDirectorOperations op or a
// small immutable `withXxx` setter mirroring the idiom railModel.ts itself
// uses for rail-local mutations (withChainKeyframeAt etc.) and the one
// ChainSegmentCard.svelte already uses inline (`patch({ keyframe: v })`).
//
// ─── Known mock/schema mismatches (legibility over mock) ──────────────────
// The design mock draws a few things the current
// document schema has no field for. Rather than mint new VideoDirectorValue
// schema (out of this file's remit) these are resolved by following the real
// data model and reporting the gap:
//  - A chain segment carries only a LEADING keyframe (ChainSegment.keyframe,
//    first-only). There is no trailing/"end frame" field on a chain segment
//    at all -- a single-shot flf-capable chain document stores its trailing
//    edge in `simple.last_frame` instead (the one field the schema gap
//    leaves it in; see toModelessDirectorValue's header note). A multi-shot
//    chain still has nowhere legal to put one, so its trailing gate always
//    falls through to the inherited/statement rules.
//  - Per-shot seed/steps/cfg (footer cells in the mock) have no field on
//    ChainSegment or DirectorPromptSegment at all -- they stay `null` here
//    rather than fabricate a value or invent schema.
//  - `chain.continuation.overlap_frames` is a single document-wide number,
//    not per-join as the mock's per-join slider suggests -- StageJoinModel
//    exposes it honestly as the same global value stitch already is.
//  - A chain segment has no separate "name" field (deriveShotLabel derives
//    one from the prompt), so the mock's "rename" affordance is dropped.

import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorModeCapability,
	ChainSegment,
	DirectorPromptSegment,
	DirectorKeyframe,
	DirectorAudioSegment,
	DirectorAudioRole,
	DirectorIcLoraEntry,
	DirectorLoraStacks,
	DirectorLoraRef,
	DirectorMediaValue,
	SegmentSubType,
	SegmentReference
} from '$lib/types/videoDirector';
import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import {
	deriveRailModel,
	deriveShotLabel,
	SNAP_EPSILON_SECONDS,
	type RailModel,
	type RailRouting,
	type RailShotBlock,
	type RailSeam,
	type RailSnapTarget,
	type RailSelectionId
} from './railModel';
import {
	deriveChainSegmentSubType,
	applyDirectorOperations,
	chainEdgeKeyframeId,
	chainKeyframeWindow,
	chainSegmentEdgeAllowances,
	collectFormMediaOptions,
	isChainEdgeKeyframeId,
	parseChainEdgeKeyframeId,
	resolveDirectorEdgeAllowances,
	type DirectorEdgeAllowances
} from '$lib/utils/videoDirector';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { mintId, clamp } from '../timelineCore';

// ─── Gates ────────────────────────────────────────────────────────────────
// A shot/block always has two gates (leading, trailing); which of the four
// kinds below a given edge renders is the precedence chain from the mock's
// binding rule on artboard B, evaluated in this order:
//   1. well        -- this shot may carry its OWN frame at this edge
//   2. inherited    -- the edge is fed by a continue join from the neighbour
//   3. statement    -- flat fact: hard cut / start / end / adjacent block
//   4. keyframe     -- (timeline only) a timed keyframe already sits here

export interface StageGateWell {
	kind: 'well';
	media: DirectorMediaValue | null;
	strength: number | null;
	emptyLabel: string;
	filledLabel: string;
	helpText: string;
	fileLabel: string | null;
	/** Stable name for the underlying media-loader field/slot. */
	slotName: string;
}

export interface StageGateInherited {
	kind: 'inherited';
	seamId: string;
	overlapFrames: number;
	overlapSeconds: number;
	label: string;
	helpText: string;
}

export interface StageGateStatement {
	kind: 'statement';
	label: string;
	helpText: string;
	/** Present when clicking the statement should select the adjoining seam. */
	seamId: string | null;
}

export interface StageGateKeyframe {
	kind: 'keyframe';
	keyframeId: string;
	media: DirectorMediaValue | null;
	label: string;
	helpText: string;
}

export type StageGate = StageGateWell | StageGateInherited | StageGateStatement | StageGateKeyframe;

// ─── Shot ─────────────────────────────────────────────────────────────────

const SUBTYPE_LABEL: Record<SegmentSubType, string> = {
	t2v: 'Text only',
	i2v: 'From start frame',
	flf: 'Start → end frames',
	chain: 'Continued'
};

export interface StageShotFooter {
	shotTypeLabel: string;
	durationSeconds: number;
	/** The shot's own render length in frames (chain: totalFrames; timeline: span). */
	frames: number;
	/** Frames this shot actually contributes to the output once overlap is
	 * deducted -- only meaningful (non-null) for a chain shot with an incoming
	 * continue join. */
	newFrames: number | null;
	capFrames: number | null;
	overCapBy: number;
	/** Timeline only. */
	startSeconds: number | null;
	endSeconds: number | null;
	/** No document field carries these yet (see file header) -- always null. */
	seed: number | null;
	steps: number | null;
	cfg: number | null;
	showLoras: boolean;
	loraSummary: string;
	/** Chain only: what the outgoing seam does. */
	joinOutLabel: string | null;
	/** Null whenever the mode's `references` capability is null -- the
	 * References footer cell only ever renders when this is non-null, same
	 * existence rule as every other capability-gated lane/cell. */
	references: StageShotReferencesInfo | null;
}

export interface StageShotReferencesInfo {
	capability: 'whole' | 'per_shot';
	/** Size of the whole-form reference pool right now (every item sitting on
	 * one of the mode's `reference_fields`), independent of any per-shot
	 * selection. */
	poolCount: number;
	/** null means "All" -- either because capability is 'whole' (there is no
	 * per-shot concept), or because this shot has no explicit selection yet
	 * (absent/empty always reads as the whole pool, never a hidden zero). */
	selectedCount: number | null;
}

export interface StageShotModel {
	kind: 'shot';
	routing: RailRouting;
	id: string;
	index: number;
	total: number;
	isFirst: boolean;
	isLast: boolean;
	label: string;
	promptSegments: Segment[];
	isPromptEmpty: boolean;
	showTeachingCopy: boolean;
	leadingGate: StageGate;
	trailingGate: StageGate;
	footer: StageShotFooter;
	overCap: boolean;
	/** Duration (seconds) that lands this shot exactly on its per-shot cap. */
	trimToSeconds: number | null;
	canDuplicate: boolean;
}

// ─── Join (seam) ────────────────────────────────────────────────────────────

export interface StageChainTotals {
	fps: number;
	generatedFrames: number[];
	deductions: Array<{ seamLabel: string; frames: number; isCut: boolean }>;
	totalFrames: number;
	totalSeconds: number;
}

export interface StageJoinModel {
	kind: 'seam';
	id: string;
	beforeShotIndex: number;
	fromLabel: string;
	toLabel: string;
	isCut: boolean;
	overlapFrames: number;
	overlapSeconds: number;
	maxOverlapFrames: number;
	tailFrameLabel: string;
	headFrameLabel: string;
	sentence: string;
	addsFrames: number;
	stitch: boolean;
	chainTotals: StageChainTotals;
	/** False when the mode's capability disables continuation entirely
	 * (MiniMax-H3 refs mode: `continuationDisabled`) -- every join is then
	 * permanently a hard cut, and the Continue/Cut toggle must not render at
	 * all (not merely default to cut), same as the overlap/stitch controls it
	 * already hides via `!isCut`. */
	continuationAvailable: boolean;
}

// ─── Keyframe ────────────────────────────────────────────────────────────────

export interface StageKeyframeLanding {
	shotIndex: number;
	shotLabel: string;
	localFrame: number;
	localTotalFrames: number;
}

export interface StageKeyframeModel {
	kind: 'keyframe';
	id: string;
	label: string;
	media: DirectorMediaValue | null;
	strength: number;
	atSeconds: number;
	atFrame: number;
	totalFrames: number;
	snapped: boolean;
	snappedToLabel: string | null;
	snapTargets: RailSnapTarget[];
	/** Chain routing only -- which shot the time falls in and the local frame
	 * inside that shot's own render (offset by any incoming overlap). */
	landing: StageKeyframeLanding | null;
	maxKeyframes: number;
	countOfKeyframes: number;
	role: 'first' | 'last' | 'free' | 'keyframe';
}

// ─── Audio ────────────────────────────────────────────────────────────────

export interface StageAudioModel {
	kind: 'audio';
	id: string;
	media: DirectorMediaValue | null;
	fileLabel: string;
	startSeconds: number;
	trimStartSeconds: number;
	lengthSeconds: number;
	role: DirectorAudioRole;
	showConditionWarning: boolean;
}

// ─── IC-LoRA ────────────────────────────────────────────────────────────────

export interface StageIcLoraModel {
	kind: 'ic_lora';
	id: string;
	lora: DirectorLoraRef | null;
	refMedia: DirectorMediaValue | null;
	strength: number;
}

export interface StageEmptyModel {
	kind: 'empty';
}

export type StageSelected =
	| StageEmptyModel
	| StageShotModel
	| StageJoinModel
	| StageKeyframeModel
	| StageAudioModel
	| StageIcLoraModel;

export interface StageModel {
	routing: RailRouting;
	globalPrompt: string;
	globalPromptSegments: Segment[];
	negativePrompt: string;
	negativePromptSegments: Segment[];
	selected: StageSelected;
}

// ─── Small formatting helpers ────────────────────────────────────────────────

function mediaFileLabel(media: DirectorMediaValue | null): string | null {
	if (!media) return null;
	if ('form_ref' in media) return null;
	const ref = media as MediaRef;
	if (ref.label) return ref.label;
	if (ref.name) return ref.name;
	const path = ref.path ?? '';
	const base = path.split('/').pop() ?? path;
	return base || null;
}

function loraSummary(stacks: DirectorLoraStacks | null): string {
	if (!stacks || (stacks.high.length === 0 && stacks.low.length === 0)) return 'None';
	const parts: string[] = [];
	if (stacks.high[0]) parts.push(`${stacks.high[0].model} · H ${stacks.high[0].strength.toFixed(2)}`);
	if (stacks.low[0]) parts.push(`L ${stacks.low[0].strength.toFixed(2)}`);
	const shown = (stacks.high[0] ? 1 : 0) + (stacks.low[0] ? 1 : 0);
	const extra = stacks.high.length + stacks.low.length - shown;
	const base = parts.join(' · ') || 'None';
	return extra > 0 ? `${base} +${extra}` : base;
}

// ─── Chain gate precedence ────────────────────────────────────────────────────
// Which segment(s) may offer a leading/trailing WELL is join-aware, not
// index-pinned -- `chainSegmentEdgeAllowances` (utils/videoDirector.ts) is the
// single source of truth both edges below defer to; this file only turns its
// per-segment booleans into the rendered gate.

function chainLeadingGate(
	segments: ChainSegment[],
	leadingAllowed: boolean,
	seams: RailSeam[],
	shotIndex: number,
	fps: number
): StageGate {
	if (leadingAllowed) {
		const segment = segments[shotIndex];
		return {
			kind: 'well',
			media: segment.keyframe,
			strength: segment.keyframe_strength,
			emptyLabel: 'Add start frame',
			filledLabel: 'Leading frame',
			helpText: segment.keyframe ? '' : 'Attach one and this shot begins from it.',
			fileLabel: mediaFileLabel(segment.keyframe),
			slotName: `${segment.id}-leading`
		};
	}
	const incoming = shotIndex > 0 ? seams[shotIndex - 1] : null;
	if (incoming && incoming.kind === 'continue') {
		return {
			kind: 'inherited',
			seamId: incoming.id,
			overlapFrames: incoming.overlapFrames,
			overlapSeconds: fps > 0 ? incoming.overlapFrames / fps : 0,
			label: 'Inherited',
			helpText: `Shot ${shotIndex}'s tail — ${incoming.overlapFrames} f re-generated here.`
		};
	}
	if (shotIndex === 0) {
		return { kind: 'statement', label: 'Start of video', helpText: '', seamId: null };
	}
	return {
		kind: 'statement',
		label: 'Hard cut',
		helpText: 'This shot starts clean.',
		seamId: incoming?.id ?? null
	};
}

/** A segment's trailing well is only ever offered where it's actually honoured:
 * the generator reads a trailing frame paired with a leading one on the SAME
 * segment (that combination resolves to the `flf` sub-type) -- so
 * `trailingAllowed` here already folds in "this segment also opens fresh"
 * (`chainSegmentEdgeAllowances`), not just "the join after it is a cut". A
 * segment whose incoming join still continues always falls through to the
 * seam statements below, regardless of the outgoing join. */
function chainTrailingGate(
	segments: ChainSegment[],
	trailingAllowed: boolean,
	seams: RailSeam[],
	shotIndex: number,
	shotCount: number
): StageGate {
	if (trailingAllowed) {
		const segment = segments[shotIndex];
		const trailing = segment.last_keyframe;
		return {
			kind: 'well',
			media: trailing,
			strength: segment.last_keyframe_strength,
			emptyLabel: 'Add end frame',
			filledLabel: 'Trailing frame',
			helpText: trailing ? '' : 'Attach one and this shot ends on it.',
			fileLabel: mediaFileLabel(trailing),
			slotName: `${segment.id}-trailing`
		};
	}
	if (shotIndex === shotCount - 1) {
		return { kind: 'statement', label: 'End of video', helpText: '', seamId: null };
	}
	const outgoing = seams[shotIndex];
	if (outgoing.kind === 'continue') {
		return {
			kind: 'statement',
			label: `Continues into shot ${shotIndex + 2}`,
			helpText: `${outgoing.overlapFrames} f re-generated at the start of the next shot.`,
			seamId: outgoing.id
		};
	}
	return {
		kind: 'statement',
		label: 'Nothing carried forward',
		helpText: `Shot ${shotIndex + 2} begins on a hard cut.`,
		seamId: outgoing.id
	};
}

// ─── Timeline gate precedence ────────────────────────────────────────────────

// Timeline style has no `keyframes: 'first_only'|'anywhere'` gate of its own
// (that vocabulary is chain-style, docs/video-director.md) -- whether an edge
// offers a well is `resolveDirectorEdgeAllowances`'s leading/trailing read
// instead: `i2v`/`flf` declared, or free placement (a real LTX preset
// declares `max_keyframes` with no `keyframes` field at all, which parses to
// 'none' -- free placement there comes from `director` simply being
// declared, never from `keyframes === 'anywhere'`; see that function's own
// doc comment for the regression this guards). An edge a document already
// has a keyframe landing on always shows it -- disallowing an edge only hides
// the EMPTY well, it never un-displays an existing (now stale) one.
function timelineEdgeGate(
	doc: VideoDirectorValue,
	rail: RailModel,
	block: RailShotBlock,
	edge: 'leading' | 'trailing',
	allowances: DirectorEdgeAllowances
): StageGate {
	const atSeconds = edge === 'leading' ? block.startSeconds : block.startSeconds + block.contributedSeconds;
	const landing = rail.keyframes.find((k) => Math.abs(k.atSeconds - atSeconds) <= SNAP_EPSILON_SECONDS);
	if (landing) {
		const kf = doc.timeline.keyframes.find((k) => k.id === landing.id);
		return {
			kind: 'keyframe',
			keyframeId: landing.id,
			media: kf?.media ?? null,
			label: `Keyframe · ${landing.atSeconds.toFixed(2)}s`,
			helpText:
				atSeconds <= SNAP_EPSILON_SECONDS || atSeconds >= rail.totalSeconds - SNAP_EPSILON_SECONDS
					? ''
					: `A keyframe sits exactly on ${atSeconds.toFixed(2)}s.`
		};
	}
	const allowed = edge === 'leading' ? allowances.leadingEdgeAllowed : allowances.trailingEdgeAllowed;
	if (!allowed) {
		return {
			kind: 'statement',
			label: edge === 'leading' ? 'No start frame' : 'No end frame',
			helpText: 'This mode has no slot here.',
			seamId: null
		};
	}
	return {
		kind: 'well',
		media: null,
		strength: null,
		emptyLabel: 'Place a keyframe here',
		filledLabel: '',
		helpText: `Nothing at ${atSeconds.toFixed(2)}s — it would land on the keyframes lane.`,
		fileLabel: null,
		slotName: `${block.id}-${edge}`
	};
}

function timelineKeyframeRoleForEdge(rail: RailModel, atSeconds: number): DirectorKeyframe['role'] {
	if (atSeconds <= SNAP_EPSILON_SECONDS) return 'first';
	if (atSeconds >= rail.totalSeconds - SNAP_EPSILON_SECONDS) return 'last';
	return 'free';
}

// ─── Shot type / footer ────────────────────────────────────────────────────

// MiniMax-H3 refs mode's `continuationDisabled` coerces a DERIVED (un-
// overridden) 'chain' sub-type to 't2v' for display, matching what
// normalize.py's `derive_segment_routing` does to the wire value -- every
// shot really is an independent hard cut in that mode, never "Continued".
function chainShotType(segment: ChainSegment, index: number, directorCap: DirectorModeCapability | undefined): string {
	const subType = deriveChainSegmentSubType(segment, index);
	const effective = directorCap?.continuationDisabled && subType === 'chain' ? 't2v' : subType;
	return SUBTYPE_LABEL[effective];
}

function chainJoinOutLabel(seams: RailSeam[], shotIndex: number, shotCount: number): string {
	if (shotIndex === shotCount - 1) return 'No next shot';
	const seam = seams[shotIndex];
	return seam.kind === 'continue' ? `Continue · ${seam.overlapFrames} f` : 'Hard cut';
}

/** Duration (seconds) that lands `shot`'s own frame count exactly on its cap;
 * null when the shot is within cap or the mode declares no cap. */
export function overCapTrimSeconds(shot: Pick<RailShotBlock, 'overCapBy' | 'capFrames'>, fps: number): number | null {
	if (shot.overCapBy <= 0 || shot.capFrames == null || fps <= 0) return null;
	return shot.capFrames / fps;
}

/** The footer's References cell -- null (cell doesn't render) whenever the
 * mode has no reference pool at all. `poolCount` reads the live form (so it
 * tracks pool edits made on the form's own References tab); `selectedCount`
 * is null for "All", matching the wire's absent-means-whole-pool rule. */
function shotReferencesInfo(
	caps: DirectorCapabilities,
	formData: Record<string, unknown> | null | undefined,
	references: SegmentReference[] | undefined
): StageShotReferencesInfo | null {
	const capability = caps.references;
	if (capability == null) return null;
	const poolCount = collectFormMediaOptions(formData).filter((o) => caps.referenceFields.includes(o.field)).length;
	const selectedCount = capability === 'per_shot' && references && references.length > 0 ? references.length : null;
	return { capability, poolCount, selectedCount };
}

function buildShotModel(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel,
	shotId: string,
	formData: Record<string, unknown> | null | undefined
): StageShotModel | null {
	const idx = rail.shots.findIndex((s) => s.id === shotId);
	if (idx === -1) return null;
	const block = rail.shots[idx];
	const directorCap = caps.modes.director;
	const routing = rail.routing;
	const allowances = resolveDirectorEdgeAllowances(caps);

	if (routing === 'chain') {
		const segments = doc.chain.segments;
		const segment = segments.find((s) => s.id === shotId);
		if (!segment) return null;
		const edgeAllowances = chainSegmentEdgeAllowances(segments, directorCap?.continuationDisabled === true, allowances);
		const prompt = segment.prompt.trim();
		const footer: StageShotFooter = {
			shotTypeLabel: chainShotType(segment, block.index, directorCap),
			durationSeconds: segment.duration,
			frames: block.totalFrames,
			newFrames: block.hasOverlapIn ? block.contributedFrames : null,
			capFrames: block.capFrames,
			overCapBy: block.overCapBy,
			startSeconds: null,
			endSeconds: null,
			seed: null,
			steps: null,
			cfg: null,
			showLoras: directorCap?.perSegmentLoras === true,
			loraSummary: loraSummary(segment.loras),
			joinOutLabel: chainJoinOutLabel(rail.seams, block.index, rail.shots.length),
			references: shotReferencesInfo(caps, formData, segment.references)
		};
		return {
			kind: 'shot',
			routing,
			id: shotId,
			index: block.index,
			total: rail.shots.length,
			isFirst: block.index === 0,
			isLast: block.index === rail.shots.length - 1,
			label: deriveShotLabel(segment.prompt, block.index),
			promptSegments: segment.prompt_segments,
			isPromptEmpty: prompt === '',
			showTeachingCopy: rail.shots.length === 1 && prompt === '',
			leadingGate: chainLeadingGate(segments, edgeAllowances.leading[block.index], rail.seams, block.index, rail.fps),
			trailingGate: chainTrailingGate(segments, edgeAllowances.trailing[block.index], rail.seams, block.index, rail.shots.length),
			footer,
			overCap: block.overCapBy > 0,
			trimToSeconds: overCapTrimSeconds(block, rail.fps),
			canDuplicate: caps.modes.director?.maxSegments == null || rail.shots.length < caps.modes.director.maxSegments
		};
	}

	// timeline
	const segment = doc.timeline.segments.find((s) => s.id === shotId);
	if (!segment) return null;
	const text = segment.text.trim();
	const footer: StageShotFooter = {
		shotTypeLabel: 'Timed prompt',
		durationSeconds: block.contributedSeconds,
		frames: block.totalFrames,
		newFrames: null,
		capFrames: null,
		overCapBy: 0,
		startSeconds: segment.start,
		endSeconds: segment.end,
		seed: null,
		steps: null,
		cfg: null,
		showLoras: false,
		loraSummary: 'None',
		joinOutLabel: null,
		references: shotReferencesInfo(caps, formData, segment.references)
	};
	return {
		kind: 'shot',
		routing,
		id: shotId,
		index: block.index,
		total: rail.shots.length,
		isFirst: block.index === 0,
		isLast: block.index === rail.shots.length - 1,
		label: deriveShotLabel(segment.text, block.index),
		promptSegments: segment.prompt_segments,
		isPromptEmpty: text === '',
		showTeachingCopy: rail.shots.length === 1 && text === '',
		leadingGate: timelineEdgeGate(doc, rail, block, 'leading', allowances),
		trailingGate: timelineEdgeGate(doc, rail, block, 'trailing', allowances),
		footer,
		overCap: false,
		trimToSeconds: null,
		canDuplicate: false
	};
}

// ─── Join model ────────────────────────────────────────────────────────────

function buildChainTotals(rail: RailModel): StageChainTotals {
	const generatedFrames = rail.shots.map((s) => s.totalFrames);
	const deductions = rail.seams.map((seam, i) => ({
		seamLabel: `${i + 1}→${i + 2}`,
		frames: seam.overlapFrames,
		isCut: seam.kind === 'cut'
	}));
	return {
		fps: rail.fps,
		generatedFrames,
		deductions,
		totalFrames: rail.totalFrames,
		totalSeconds: rail.totalSeconds
	};
}

function buildJoinModel(doc: VideoDirectorValue, caps: DirectorCapabilities, rail: RailModel, seamId: string): StageJoinModel | null {
	const seam = rail.seams.find((s) => s.id === seamId);
	if (!seam) return null;
	const fromShot = rail.shots[seam.beforeShotIndex];
	const toShot = rail.shots[seam.beforeShotIndex + 1];
	if (!fromShot || !toShot) return null;

	const continuationAvailable = caps.modes.director?.continuationDisabled !== true;
	const isCut = seam.kind === 'cut';
	const addsFrames = isCut ? toShot.totalFrames : toShot.totalFrames - seam.overlapFrames;
	const sentence = !continuationAvailable
		? `${toShot.label} is an independent cut. Shots under references can't share frames with their neighbour, so nothing is re-generated and the cut lands exactly on frame ${toShot.startFrame + 1}.`
		: isCut
			? `${toShot.label} starts clean. No frames are shared, nothing is re-generated, and the cut lands exactly on frame ${toShot.startFrame + 1}.`
			: `${toShot.label} re-generates the last ${seam.overlapFrames} frames of ${fromShot.label} — ${(seam.overlapFrames / rail.fps).toFixed(2)} s — and continues from them. Those frames are emitted once, so ${toShot.label} adds ${addsFrames} new frames to the video, not ${toShot.totalFrames}.`;

	return {
		kind: 'seam',
		id: seam.id,
		beforeShotIndex: seam.beforeShotIndex,
		fromLabel: `Shot ${seam.beforeShotIndex + 1}`,
		toLabel: `Shot ${seam.beforeShotIndex + 2}`,
		isCut,
		overlapFrames: seam.overlapFrames,
		overlapSeconds: seam.overlapFrames / rail.fps,
		maxOverlapFrames: caps.modes.director?.maxOverlapFrames ?? toShot.totalFrames - 1,
		tailFrameLabel: `${fromShot.label} · frame ${fromShot.totalFrames}`,
		headFrameLabel: `${toShot.label} · frame 1`,
		sentence,
		addsFrames,
		stitch: doc.chain.continuation.stitch,
		chainTotals: buildChainTotals(rail),
		continuationAvailable
	};
}

// ─── Keyframe model ────────────────────────────────────────────────────────

function chainLandingWindow(rail: RailModel, atSeconds: number): StageKeyframeLanding | null {
	let target = rail.shots[rail.shots.length - 1] ?? null;
	for (const shot of rail.shots) {
		if (atSeconds < shot.startSeconds + shot.contributedSeconds || shot === rail.shots[rail.shots.length - 1]) {
			target = shot;
			break;
		}
	}
	if (!target) return null;
	const localFromContributed = Math.round((atSeconds - target.startSeconds) * rail.fps);
	const localFrame = Math.max(0, localFromContributed) + target.overlapInFrames;
	return {
		shotIndex: target.index,
		shotLabel: target.label,
		localFrame,
		localTotalFrames: target.totalFrames
	};
}

/** Fills/clears the chain-edge mirror's real storage -- the same
 * `withChainLeadingMedia`/`withChainTrailingMedia` the shot-edit well itself
 * calls, so editing from the rail lane and editing from the well are the
 * same write, never a second copy. */
export function withChainEdgeKeyframeMedia(doc: VideoDirectorValue, id: string, media: DirectorMediaValue | null): VideoDirectorValue {
	const parsed = parseChainEdgeKeyframeId(id);
	if (!parsed) return doc;
	return parsed.edge === 'first'
		? withChainLeadingMedia(doc, parsed.segmentId, media)
		: withChainTrailingMedia(doc, parsed.segmentId, media);
}

export function withChainEdgeKeyframeStrength(doc: VideoDirectorValue, id: string, strength: number): VideoDirectorValue {
	const parsed = parseChainEdgeKeyframeId(id);
	if (!parsed) return doc;
	const segment = doc.chain.segments.find((s) => s.id === parsed.segmentId);
	if (!segment) return doc;
	return parsed.edge === 'first'
		? withChainLeadingMedia(doc, parsed.segmentId, segment.keyframe, strength)
		: withChainTrailingMedia(doc, parsed.segmentId, segment.last_keyframe, strength);
}

function buildKeyframeModel(doc: VideoDirectorValue, caps: DirectorCapabilities, rail: RailModel, keyframeId: string): StageKeyframeModel | null {
	const railKf = rail.keyframes.find((k) => k.id === keyframeId);
	if (!railKf) return null;
	const directorCap = caps.modes.director;
	if (rail.routing === 'chain') {
		const parsedEdge = parseChainEdgeKeyframeId(keyframeId);
		if (parsedEdge) {
			const isFirst = parsedEdge.edge === 'first';
			const segment = doc.chain.segments.find((s) => s.id === parsedEdge.segmentId);
			const media = isFirst ? (segment?.keyframe ?? null) : (segment?.last_keyframe ?? null);
			return {
				kind: 'keyframe',
				id: keyframeId,
				label: mediaFileLabel(media) ?? (isFirst ? 'Leading frame' : 'Trailing frame'),
				media,
				strength: (isFirst ? segment?.keyframe_strength : segment?.last_keyframe_strength) ?? 1,
				atSeconds: railKf.atSeconds,
				atFrame: railKf.atFrame,
				totalFrames: rail.totalFrames,
				snapped: true,
				snappedToLabel: isFirst ? 'Start' : 'End',
				snapTargets: rail.snapTargets,
				landing: null,
				maxKeyframes: rail.maxKeyframes ?? directorCap?.maxKeyframes ?? 8,
				countOfKeyframes: rail.keyframes.length,
				role: isFirst ? 'first' : 'last'
			};
		}
		const kf = doc.chain.keyframes.find((k) => k.id === keyframeId);
		if (!kf) return null;
		return {
			kind: 'keyframe',
			id: kf.id,
			label: mediaFileLabel(kf.media) ?? 'Keyframe',
			media: kf.media,
			strength: kf.strength,
			atSeconds: railKf.atSeconds,
			atFrame: railKf.atFrame,
			totalFrames: rail.totalFrames,
			snapped: railKf.snapped,
			snappedToLabel: railKf.snappedToLabel,
			snapTargets: rail.snapTargets,
			landing: chainLandingWindow(rail, railKf.atSeconds),
			maxKeyframes: rail.maxKeyframes ?? directorCap?.maxKeyframes ?? 8,
			countOfKeyframes: rail.keyframes.length,
			role: 'keyframe'
		};
	}
	const kf = doc.timeline.keyframes.find((k) => k.id === keyframeId);
	if (!kf) return null;
	return {
		kind: 'keyframe',
		id: kf.id,
		label: mediaFileLabel(kf.media) ?? 'Keyframe',
		media: kf.media,
		strength: kf.strength,
		atSeconds: railKf.atSeconds,
		atFrame: railKf.atFrame,
		totalFrames: rail.totalFrames,
		snapped: railKf.snapped,
		snappedToLabel: railKf.snappedToLabel,
		snapTargets: rail.snapTargets,
		landing: null,
		maxKeyframes: rail.maxKeyframes ?? directorCap?.maxKeyframes ?? 8,
		countOfKeyframes: rail.keyframes.length,
		role: kf.role
	};
}

// ─── Audio / IC-LoRA / empty ─────────────────────────────────────────────────

function buildAudioModel(rail: RailModel, list: DirectorAudioSegment[], audioId: string): StageAudioModel | null {
	const railClip = rail.audio.find((a) => a.id === audioId);
	const entry = list.find((a) => a.id === audioId);
	if (!railClip || !entry) return null;
	const role = entry.role ?? 'condition';
	return {
		kind: 'audio',
		id: entry.id,
		media: entry.media,
		fileLabel: mediaFileLabel(entry.media) ?? 'Untitled audio',
		startSeconds: entry.start,
		trimStartSeconds: entry.trim_start,
		lengthSeconds: entry.length,
		role,
		showConditionWarning: role === 'condition'
	};
}

function buildIcLoraModel(doc: VideoDirectorValue, icLoraId: string): StageIcLoraModel | null {
	const entry = doc.timeline.ic_lora.find((e) => e.id === icLoraId) ?? doc.timeline.ic_lora[0] ?? null;
	if (!entry) return null;
	return { kind: 'ic_lora', id: entry.id, lora: entry.lora, refMedia: entry.ref_media, strength: entry.strength };
}

// ─── Entry point ────────────────────────────────────────────────────────────

export function deriveStageModel(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	selection: RailSelectionId | null,
	formData?: Record<string, unknown> | null
): StageModel {
	const rail = deriveRailModel(doc, caps);
	let selected: StageSelected | null = null;

	if (selection) {
		switch (selection.kind) {
			case 'shot':
				selected = buildShotModel(doc, caps, rail, selection.id, formData);
				break;
			case 'seam':
				selected = buildJoinModel(doc, caps, rail, selection.id);
				break;
			case 'keyframe':
				selected = buildKeyframeModel(doc, caps, rail, selection.id);
				break;
			case 'audio':
				selected = buildAudioModel(rail, rail.routing === 'chain' ? doc.chain.audio : doc.timeline.audio, selection.id);
				break;
			case 'ic_lora':
				selected = buildIcLoraModel(doc, selection.id);
				break;
		}
	}

	return {
		routing: rail.routing,
		globalPrompt: doc.global_prompt,
		globalPromptSegments: doc.global_prompt_segments,
		negativePrompt: doc.negative_prompt,
		negativePromptSegments: doc.negative_prompt_segments,
		selected: selected ?? { kind: 'empty' }
	};
}

// ─── Pure stage-local reducers ───────────────────────────────────────────────
// Everything an existing videoDirector.ts op already models routes through
// applyDirectorOperations (removal, prompt-as-plain-string ops, continuation,
// audio path upserts). What follows fills the gaps: rich prompt-segment
// writes (the ops only take a flattened string), per-shot LoRA stacks and
// duplication (no op carries either), and gate/keyframe media sets (the
// upsert_media op's wire shape always requires a resolved `path` -- it exists
// for the chat tool, not for a raw picker value that may be a bare
// `form_ref`). Each mirrors railModel.ts's own `withXxx` idiom.

export function withShotPromptSegments(doc: VideoDirectorValue, caps: DirectorCapabilities, id: string, promptSegments: Segment[]): VideoDirectorValue {
	const prompt = resolvePromptSegments(promptSegments);
	if (caps.segmentRouting) {
		return {
			...doc,
			chain: { ...doc.chain, segments: doc.chain.segments.map((s) => (s.id === id ? { ...s, prompt_segments: promptSegments, prompt } : s)) }
		};
	}
	return {
		...doc,
		timeline: { ...doc.timeline, segments: doc.timeline.segments.map((s) => (s.id === id ? { ...s, prompt_segments: promptSegments, text: prompt } : s)) }
	};
}

export function withShotLoras(doc: VideoDirectorValue, caps: DirectorCapabilities, id: string, loras: DirectorLoraStacks | null): VideoDirectorValue {
	if (!caps.segmentRouting) return doc;
	return { ...doc, chain: { ...doc.chain, segments: doc.chain.segments.map((s) => (s.id === id ? { ...s, loras } : s)) } };
}

/**
 * Writes the References picker's checkbox selection onto one shot. An empty
 * selection is never stored as `[]` -- the picker cannot leave a shot with an
 * explicit "none" state, so a deselect-to-zero click falls back to "All"
 * (the field is cleared, same as the shot never having a selection at all).
 * Mirrors `parseOpSegmentReferences` in videoDirector.ts, the chat-op path
 * onto the same field.
 */
export function withShotReferences(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	id: string,
	references: SegmentReference[]
): VideoDirectorValue {
	const next = references.length > 0 ? references : undefined;
	if (caps.segmentRouting) {
		return {
			...doc,
			chain: { ...doc.chain, segments: doc.chain.segments.map((s) => (s.id === id ? { ...s, references: next } : s)) }
		};
	}
	return {
		...doc,
		timeline: { ...doc.timeline, segments: doc.timeline.segments.map((s) => (s.id === id ? { ...s, references: next } : s)) }
	};
}

export function withDuplicatedShot(doc: VideoDirectorValue, caps: DirectorCapabilities, id: string): VideoDirectorValue {
	if (!caps.segmentRouting) return doc;
	const segments = doc.chain.segments;
	const idx = segments.findIndex((s) => s.id === id);
	if (idx === -1) return doc;
	const source = segments[idx];
	const copy: ChainSegment = {
		...source,
		id: mintId('chain', segments),
		prompt_segments: source.prompt_segments.map((seg) => ({ ...seg })),
		// A duplicate never inherits its source's own edge frames (an image
		// makes no sense copied onto a second shot) -- whether the COPY is
		// itself eligible for its own leading/trailing well is then a normal
		// join-aware question (`chainSegmentEdgeAllowances`), same as any
		// other segment.
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1
	};
	return { ...doc, chain: { ...doc.chain, segments: [...segments.slice(0, idx + 1), copy, ...segments.slice(idx + 1)] } };
}

/** Sets or clears one segment's own leading keyframe -- legal on any segment
 * once it opens fresh (`chainSegmentEdgeAllowances`), not just segment 0. */
export function withChainLeadingMedia(
	doc: VideoDirectorValue,
	segmentId: string,
	media: DirectorMediaValue | null,
	strength = 1
): VideoDirectorValue {
	return {
		...doc,
		chain: {
			...doc.chain,
			segments: doc.chain.segments.map((s) =>
				s.id === segmentId ? { ...s, keyframe: media, keyframe_strength: media ? strength : s.keyframe_strength } : s
			)
		}
	};
}

/** Sets or clears one segment's own trailing keyframe -- legal on any segment
 * once it both opens AND closes fresh (`chainSegmentEdgeAllowances`); paired
 * with `keyframe` on the SAME segment this is what resolves it to `flf`. */
export function withChainTrailingMedia(
	doc: VideoDirectorValue,
	segmentId: string,
	media: DirectorMediaValue | null,
	strength = 1
): VideoDirectorValue {
	return {
		...doc,
		chain: {
			...doc.chain,
			segments: doc.chain.segments.map((s) =>
				s.id === segmentId
					? { ...s, last_keyframe: media, last_keyframe_strength: media ? strength : s.last_keyframe_strength }
					: s
			)
		}
	};
}

/** Upserts or clears (media=null) one timeline keyframe by id -- the mint path
 * for a gate's empty "place a keyframe here" well as well as an ordinary edit
 * of an existing one. */
export function withTimelineKeyframeMedia(
	doc: VideoDirectorValue,
	id: string,
	role: DirectorKeyframe['role'],
	start: number,
	media: DirectorMediaValue | null
): VideoDirectorValue {
	const list = doc.timeline.keyframes;
	if (media == null) {
		return { ...doc, timeline: { ...doc.timeline, keyframes: list.filter((k) => k.id !== id) } };
	}
	const idx = list.findIndex((k) => k.id === id);
	const existing = idx === -1 ? null : list[idx];
	const kf: DirectorKeyframe = { id, start, role, strength: existing?.strength ?? 1, media };
	const next = idx === -1 ? [...list, kf] : list.map((k, i) => (i === idx ? kf : k));
	return { ...doc, timeline: { ...doc.timeline, keyframes: next } };
}

/** Upserts or clears (media=null) one chain 'anywhere' keyframe's media,
 * preserving its position/strength -- the media-editing half of the pair
 * railModel.ts's own `withChainKeyframeAt` (position) already covers. */
export function withChainKeyframeMedia(doc: VideoDirectorValue, id: string, media: DirectorMediaValue | null): VideoDirectorValue {
	const list = doc.chain.keyframes;
	if (media == null) {
		return { ...doc, chain: { ...doc.chain, keyframes: list.filter((k) => k.id !== id) } };
	}
	const idx = list.findIndex((k) => k.id === id);
	const existing = idx === -1 ? null : list[idx];
	const kf = { id, at: existing?.at ?? 0, strength: existing?.strength ?? 1, media };
	const next = idx === -1 ? [...list, kf] : list.map((k, i) => (i === idx ? kf : k));
	return { ...doc, chain: { ...doc.chain, keyframes: next } };
}

export function withKeyframeStrength(doc: VideoDirectorValue, caps: DirectorCapabilities, id: string, strength: number): VideoDirectorValue {
	if (caps.segmentRouting) {
		return { ...doc, chain: { ...doc.chain, keyframes: doc.chain.keyframes.map((k) => (k.id === id ? { ...k, strength } : k)) } };
	}
	return { ...doc, timeline: { ...doc.timeline, keyframes: doc.timeline.keyframes.map((k) => (k.id === id ? { ...k, strength } : k)) } };
}

/** Patches an existing audio clip in whichever sub-tree the routing reads
 * (chain.audio / timeline.audio) -- unlike the 'upsert_audio' op this takes a
 * raw DirectorMediaValue (a bare FormMediaRef included), not a resolved
 * `path` string, so it fits a direct DirectorMediaSlot onChange. */
export function withAudioPatch(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	id: string,
	patch: Partial<Pick<DirectorAudioSegment, 'media' | 'role' | 'start' | 'trim_start' | 'length'>>
): VideoDirectorValue {
	const onChain = caps.segmentRouting;
	const list = onChain ? doc.chain.audio : doc.timeline.audio;
	const idx = list.findIndex((a) => a.id === id);
	if (idx === -1) return doc;
	const nextList = list.map((a, i) => (i === idx ? { ...a, ...patch } : a));
	return onChain ? { ...doc, chain: { ...doc.chain, audio: nextList } } : { ...doc, timeline: { ...doc.timeline, audio: nextList } };
}

export function withIcLoraPatch(doc: VideoDirectorValue, id: string, patch: Partial<Pick<DirectorIcLoraEntry, 'lora' | 'ref_media' | 'strength'>>): VideoDirectorValue {
	const list = doc.timeline.ic_lora;
	const idx = list.findIndex((e) => e.id === id);
	const existing: DirectorIcLoraEntry = idx === -1 ? { id, lora: null, ref_media: null, strength: 1 } : list[idx];
	const next: DirectorIcLoraEntry = { ...existing, ...patch };
	const nextList = idx === -1 ? [...list, next] : list.map((e, i) => (i === idx ? next : e));
	return { ...doc, timeline: { ...doc.timeline, ic_lora: nextList } };
}

/** The role a gate's "place a keyframe here" well should mint on fill. */
export function timelineGateKeyframeRole(rail: RailModel, atSeconds: number): DirectorKeyframe['role'] {
	return timelineKeyframeRoleForEdge(rail, atSeconds);
}

/** Removes the currently selected object's media/entry via the shared ops
 * path where one exists. Exposed so Stage.svelte doesn't need to know which
 * document sub-tree an audio track lives in. */
export function withRemoveAudio(doc: VideoDirectorValue, caps: DirectorCapabilities, id: string): VideoDirectorValue {
	return applyDirectorOperations(doc, [{ op: 'remove_audio', id }], caps);
}

export function withStitch(doc: VideoDirectorValue, caps: DirectorCapabilities, stitch: boolean): VideoDirectorValue {
	return applyDirectorOperations(doc, [{ op: 'set_continuation', continuation: { stitch } }], caps);
}

export function withOverlapFrames(doc: VideoDirectorValue, caps: DirectorCapabilities, overlapFrames: number): VideoDirectorValue {
	return applyDirectorOperations(doc, [{ op: 'set_continuation', continuation: { overlap_frames: overlapFrames } }], caps);
}

/** Flips a join between continue/cut by setting (or clearing) the next shot's
 * sub_type_override -- the same mechanism `chainSegmentIsAmbiguous` already
 * models; there is no separate "seam kind" field. */
export function withSeamKind(doc: VideoDirectorValue, caps: DirectorCapabilities, seamId: string, kind: 'continue' | 'cut'): VideoDirectorValue {
	// A mode with continuation disabled (MiniMax-H3 refs) has no 'continue'
	// side to flip to -- every seam is already forced to a cut in railModel's
	// own derivation, so honouring a 'continue' request here would silently
	// disagree with what the rail actually renders.
	if (kind === 'continue' && caps.modes.director?.continuationDisabled) return doc;
	const rail = deriveRailModel(doc, caps);
	const seam = rail.seams.find((s) => s.id === seamId);
	if (!seam) return doc;
	const nextShot = rail.shots[seam.beforeShotIndex + 1];
	if (!nextShot) return doc;
	return applyDirectorOperations(
		doc,
		[{ op: 'upsert_segment', segment: { id: nextShot.id, sub_type_override: kind === 'cut' ? 't2v' : null } }],
		caps
	);
}

export function withTrimShotToCap(doc: VideoDirectorValue, caps: DirectorCapabilities, shotId: string, trimToSeconds: number): VideoDirectorValue {
	return applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: shotId, duration: trimToSeconds } }], caps);
}

// ─── Add affordances ─────────────────────────────────────────────────────────
// The rail's "+" controls (shot, keyframe, audio) mint a blank entry at a
// reasonable default position/duration and append it -- selecting the new
// object is the caller's job (it renders on the rail immediately; the user
// clicks it, same as any other rail object).

/** Appends a blank shot: a new chain segment after the last one (chain
 * routing) or a new timed prompt block starting where the last one ends
 * (timeline routing). Respects `maxSegments` on chain routing -- callers
 * should also gate the control on `RailModel.canAddShot`. */
export function withAddedShot(doc: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	const defaultDuration = caps.modes.director?.defaultSegmentDuration ?? caps.defaultDuration;
	if (caps.segmentRouting) {
		const segments = doc.chain.segments;
		const seg: ChainSegment = {
			id: mintId('chain', segments),
			prompt: '',
			prompt_segments: [],
			duration: defaultDuration,
			loras: null,
			keyframe: null,
			keyframe_strength: 1,
			last_keyframe: null,
			last_keyframe_strength: 1,
			sub_type_override: null
		};
		return { ...doc, chain: { ...doc.chain, segments: [...segments, seg] } };
	}
	const segments = doc.timeline.segments;
	const start = segments.reduce((max, s) => Math.max(max, s.end), 0);
	const end = Math.max(start + defaultDuration, start + 0.1);
	const seg: DirectorPromptSegment = { id: mintId('seg', segments), start, end, text: '', prompt_segments: [] };
	return { ...doc, timeline: { ...doc.timeline, segments: [...segments, seg] } };
}

/** Appends a blank keyframe: a chain 'anywhere' keyframe at the midpoint of
 * the chain's current window, or a 'free' timeline keyframe at the midpoint
 * of the timeline's duration. Callers should gate on the mode's
 * `keyframes`/`maxKeyframes` capability. */
export function withAddedKeyframe(doc: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	if (caps.segmentRouting) {
		const window = chainKeyframeWindow(doc.chain);
		const kf = { id: mintId('ckf', doc.chain.keyframes), at: clamp(window / 2, 0, window), strength: 1, media: null };
		return { ...doc, chain: { ...doc.chain, keyframes: [...doc.chain.keyframes, kf] } };
	}
	const duration = doc.timeline.duration;
	const kf: DirectorKeyframe = {
		id: mintId('kf', doc.timeline.keyframes),
		start: clamp(duration / 2, 0, duration),
		role: 'free',
		strength: 1,
		media: null
	};
	return { ...doc, timeline: { ...doc.timeline, keyframes: [...doc.timeline.keyframes, kf] } };
}

/** Appends a blank audio track spanning the current window (chain) or
 * duration (timeline). Callers should gate on the mode's `audio` capability. */
export function withAddedAudio(doc: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	if (caps.segmentRouting) {
		const window = chainKeyframeWindow(doc.chain);
		const track: DirectorAudioSegment = {
			id: mintId('caud', doc.chain.audio),
			role: 'condition',
			start: 0,
			trim_start: 0,
			length: window > 0 ? window : 1,
			media: null
		};
		return { ...doc, chain: { ...doc.chain, audio: [...doc.chain.audio, track] } };
	}
	const track: DirectorAudioSegment = {
		id: mintId('aud', doc.timeline.audio),
		start: 0,
		trim_start: 0,
		length: doc.timeline.duration > 0 ? doc.timeline.duration : 1,
		media: null
	};
	return { ...doc, timeline: { ...doc.timeline, audio: [...doc.timeline.audio, track] } };
}
