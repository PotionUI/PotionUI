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
// - `fpsLocked` is always false and no "24 fps" header chip is ever emitted:
//   there is no `fps_locked` capability on `DirectorModeCapability` yet (W2
//   adds one to MiniMax-H3's preset block) -- without it there is no signal
//   to derive "fixed" from, so this omits the chip entirely rather than
//   guess (see PLAN.md's open item D5 and the W1-BRIEF's own hedge on this).
//
// ─── W3 additions (PLAN.md §C W3) ──────────────────────────────────────────
// - `deriveConsoleModel` takes two new optional trailing params: `runs`
//   (`Tab.directorRuns`, keyed by shot id) and `checked` (the console's own
//   transient row-checkbox selection, ShotConsole component state). Both
//   default to empty, which reproduces W1/W2's behaviour exactly (`run`
//   always null, badges never 'needs-previous'/'input-ready'/'stale', joins
//   never 'missing').
// - `run` now reflects the shot's own entry in `runs` (queued/generating %/
//   done · time/failed).
// - The dependency badge for a shot that DEPENDS on its predecessor (a
//   'continue'/'native' seam, or a timeline shot's `continue_from_previous`)
//   is derived from `runs` via `dependentBadge` below: no predecessor run (or
//   not 'done') is 'needs-previous'; predecessor done and this shot never
//   itself rendered is 'input-ready'; predecessor done and this shot's OWN
//   last render is stale relative to it (the predecessor's live document
//   changed since ITS OWN run, or the predecessor was regenerated under a
//   different generation since this run's `predecessorRef` was stamped --
//   see utils/directorInputIdentity.ts) is 'stale'; either run missing a
//   complete identity (an old stored run, or `predecessorRef` absent) is
//   'unverified' rather than a guessed 'continuous'; otherwise 'continuous'
//   (W1/W2's steady state). A shot with no such dependency keeps its plain
//   'independent'/'continuous' read, unchanged.
// - A join whose kind would be 'native'/'continue' (i.e. NOT a hard cut)
//   becomes `kind: 'missing'` -- rendering the warning block instead of the
//   normal chip/toggle -- only when its downstream shot is CHECKED and the
//   predecessor has no 'done' run (PLAN.md: the console has no ambient
//   Generate control, so this warning is only shown once the user has
//   actually scoped a generation that would hit it). `control.spanShotIds`
//   carries the contiguous run back to the nearest fresh cut, inclusive of
//   the checked shot, for the block's "Generate previous + this shot" action.
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
	ChainSegment,
	DirectorTimelineShot
} from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';
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
import {
	directorShotInputIdentity,
	hasVersionedShotIdentity,
	type DirectorShotIdentityContext
} from '$lib/utils/directorInputIdentity';

// ─── Public types (verbatim from W1-BRIEF.md's contract) ───────────────────

export type ConsoleBadge = 'independent' | 'needs-previous' | 'input-ready' | 'stale' | 'continuous' | 'unverified';
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
	control:
		| { kind: 'chip'; text: string }
		| { kind: 'toggle'; value: 'continue' | 'cut' }
		/** `kind: 'missing'` only -- the contiguous run of shot ids back to the
		 * nearest fresh cut, inclusive of the checked (downstream) shot, for
		 * the warning block's "Generate previous + this shot" action. */
		| { kind: 'missing'; spanShotIds: string[] };
	overlapFrames: number | null;
}

export interface ConsoleHeader {
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
		// Free keyframes across shots (PLAN.md's cap-strip note): a chain
		// document has one shared `chain.keyframes` list, a timeline film's
		// count is summed across every independent shot's own list.
		const cap = dc.maxKeyframes ?? DEFAULT_MAX_KEYFRAMES;
		const placed = caps.segmentRouting
			? doc.chain.keyframes.length
			: doc.timeline.shots.flatMap((s) => s.keyframes).filter((k) => k.role === 'free').length;
		chips.push({ icon: 'layers', text: `Free keyframes · ${placed}/${cap}` });
	}

	if (dc.audio) chips.push({ text: 'Audio' });
	if (dc.icLora) chips.push({ text: 'IC-LoRA' });
	if (dc.perSegmentLoras) chips.push({ text: 'Per-shot LoRAs' });
	if (dc.fpsLocked) chips.push({ text: `${rail.fps} fps` });

	return chips;
}

function buildHeader(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	rail: RailModel,
	shotCount: number,
	totalSeconds: number,
	runs: Record<string, DirectorRunState> | null | undefined
): ConsoleHeader {
	const result = validateDirector(doc, caps, runs);
	return {
		shotCount,
		totalSeconds,
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

// ─── Run state / dependency badges (W3) ────────────────────────────────────

function consoleRunState(run: DirectorRunState | undefined | null): ConsoleShot['run'] {
	if (!run) return null;
	switch (run.status) {
		case 'queued':
			return { kind: 'queued' };
		case 'generating':
			return { kind: 'generating', percent: Math.round((run.progress ?? 0) * 100) };
		case 'done':
			return { kind: 'done', time: formatRunFinishedAt(run.finishedAt) };
		case 'failed':
			return { kind: 'failed' };
	}
}

/** `HH:MM`, local time, zero-padded -- a pure function of the given
 * timestamp (never reads the live clock itself, so this stays
 * byte-deterministic for a given `finishedAt`), matching the console.html
 * mock's `Done · 12:03` reading. `null` (no timestamp recorded) reads as
 * '--:--' rather than throwing. */
function formatRunFinishedAt(finishedAt: number | null): string {
	if (finishedAt == null) return '--:--';
	const d = new Date(finishedAt);
	return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Dependency badge for a shot that DEPENDS on `predecessorId`'s output (a
 * continue-style seam/join) -- see this file's W3 header note for the state
 * machine. `runs` absent/empty degrades to 'needs-previous' throughout,
 * matching W1/W2 (no runs map existed yet).
 *
 * Once both rows are 'done', freshness needs BOTH runs to carry a complete
 * identity (a versioned `inputsHash` on the predecessor, a `predecessorRef`
 * on this shot's own run) -- either missing (an old stored run, or one from
 * before this shot had a predecessor at all) can't be trusted to answer "did
 * the predecessor change since", so that reads 'unverified' rather than
 * risking a false 'continuous'. With both present, 'stale' fires on EITHER
 * signal: the predecessor's live document has drifted from what its own run
 * captured, or the predecessor was regenerated under a different generation
 * since this run's `predecessorRef` was stamped (catches a same-input
 * regenerate producing a different output, which a content diff alone can't
 * see, and is exact where the retired `finishedAt`-ordering heuristic was
 * only a guess). */
function dependentBadge(
	doc: VideoDirectorValue,
	predecessorId: string,
	shotId: string,
	runs: Record<string, DirectorRunState> | null | undefined,
	identityCtx: DirectorShotIdentityContext
): ConsoleBadge {
	const predecessorRun = runs?.[predecessorId];
	if (!predecessorRun || predecessorRun.status !== 'done') return 'needs-previous';
	const ownRun = runs?.[shotId];
	if (!ownRun || ownRun.status !== 'done') return 'input-ready';
	if (!hasVersionedShotIdentity(predecessorRun.inputsHash) || !ownRun.predecessorRef) return 'unverified';
	const predecessorLiveIdentity = directorShotInputIdentity(doc, predecessorId, identityCtx);
	const predecessorEditedSinceItsRun = predecessorLiveIdentity != null && predecessorLiveIdentity !== predecessorRun.inputsHash;
	const predecessorResultChanged =
		ownRun.predecessorRef.generationId !== predecessorRun.generationId || ownRun.predecessorRef.outputKey !== predecessorId;
	return predecessorEditedSinceItsRun || predecessorResultChanged ? 'stale' : 'continuous';
}

// ─── Chain shots/joins ──────────────────────────────────────────────────────

function chainShotBadge(
	doc: VideoDirectorValue,
	rail: RailModel,
	index: number,
	runs: Record<string, DirectorRunState> | null | undefined,
	identityCtx: DirectorShotIdentityContext
): ConsoleBadge {
	const incoming = index > 0 ? rail.seams[index - 1] : null;
	const outgoing = index < rail.shots.length - 1 ? rail.seams[index] : null;
	if (incoming && incoming.kind === 'continue') {
		return dependentBadge(doc, doc.chain.segments[index - 1].id, doc.chain.segments[index].id, runs, identityCtx);
	}
	const continuous = outgoing && outgoing.kind === 'continue';
	return continuous ? 'continuous' : 'independent';
}

/** The contiguous run of chain segment ids back to the nearest fresh cut
 * (inclusive), ending at `uptoIndex` -- the span `compile_shot_plan` accepts
 * for a "Generate previous + this shot" submission (PLAN.md's "never filter
 * segments" discipline: a broken continuation chain of 3+ shots resolves to
 * the WHOLE dependent run, never just the two nearest cards). */
function chainSpanFromFreshCut(doc: VideoDirectorValue, rail: RailModel, uptoIndex: number): string[] {
	let start = uptoIndex;
	while (start > 0 && rail.seams[start - 1].kind === 'continue') start -= 1;
	return doc.chain.segments.slice(start, uptoIndex + 1).map((s) => s.id);
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

/** Once a shot's run resolves to 'done' with an output poster, that
 * REPLACES the shot's own keyframe/slate thumb (PLAN.md: "Row thumb = the
 * shot's output poster once done") -- a shot whose run is still queued/
 * generating/failed, or has none, keeps reading its own editor-side thumb. */
function withRunPoster(thumb: ConsoleThumb, run: DirectorRunState | null | undefined): ConsoleThumb {
	if (run?.status === 'done' && run.posterUrl) return { url: run.posterUrl, source: 'output' };
	return thumb;
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
	formData: Record<string, unknown> | null | undefined,
	runs: Record<string, DirectorRunState> | null | undefined
): ConsoleShot[] {
	const dc = caps.modes.director;
	const identityCtx: DirectorShotIdentityContext = { caps, formData };
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
			fpsLocked: dc?.fpsLocked === true,
			thumb: withRunPoster(chainShotThumb(doc, rail, segment, block, formData), runs?.[segment.id]),
			badge: chainShotBadge(doc, rail, index, runs, identityCtx),
			hasIcLora: false,
			icLoraCount: 0,
			run: consoleRunState(runs?.[segment.id]),
			tabs,
			canRemove: rail.shots.length > 1,
			canDuplicate: dc?.maxSegments == null || rail.shots.length < dc.maxSegments
		};
	});
}

function buildChainJoins(
	doc: VideoDirectorValue,
	rail: RailModel,
	caps: DirectorCapabilities,
	runs: Record<string, DirectorRunState> | null | undefined,
	checked: Set<string> | null | undefined
): ConsoleJoin[] {
	const continuationAvailable = caps.modes.director?.continuationDisabled !== true;
	return rail.seams.map((seam: RailSeam) => {
		const fromShot = rail.shots[seam.beforeShotIndex];
		const toShot = rail.shots[seam.beforeShotIndex + 1];
		const isCut = seam.kind === 'cut';
		// Only checking the downstream shot can surface the warning -- the
		// console has no ambient Generate control, so this is only ever shown
		// once the user has scoped a generation that would actually hit it.
		const missing = !isCut && checked?.has(toShot.id) && runs?.[fromShot.id]?.status !== 'done';
		if (missing) {
			return {
				afterShotId: fromShot.id,
				beforeShotId: toShot.id,
				kind: 'missing',
				label: 'MISSING PREDECESSOR',
				sentence: `Shot ${shotNumber(seam.beforeShotIndex)} has no output yet.`,
				control: { kind: 'missing', spanShotIds: chainSpanFromFreshCut(doc, rail, seam.beforeShotIndex + 1) },
				overlapFrames: null
			};
		}
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

// ─── Timeline (LTX) shots ───────────────────────────────────────────────────
// W2: `DirectorTimelineDoc` is `{fps, shots[]}` -- each shot renders exactly
// like a chain segment does (one ConsoleShot, one generation), except a
// timeline shot's own rail geometry comes from `deriveRailModel(doc, caps,
// shot.id)` (per-shot, no cross-shot concatenation -- see that function's
// doc comment) rather than a single document-wide `RailModel`.

function timelineShotTitle(doc: VideoDirectorValue, shot: DirectorTimelineShot, index: number): string {
	if (shot.title) return shot.title;
	const withText = shot.segments.find((s) => s.text.trim() !== '');
	if (withText) return deriveShotLabel(withText.text, index);
	if (doc.global_prompt.trim() !== '') return deriveShotLabel(doc.global_prompt, index);
	return deriveShotLabel('', index);
}

function timelineShotThumb(shot: DirectorTimelineShot, formData: Record<string, unknown> | null | undefined): ConsoleThumb {
	const sorted = [...shot.keyframes].sort((a, b) => a.start - b.start);
	const free = sorted.find((k) => k.role === 'free' && k.media != null);
	if (free) return { url: thumbUrlFor(free.media, formData), source: 'keyframe' };
	const start = sorted.find((k) => k.role === 'first' && k.media != null);
	if (start) return { url: thumbUrlFor(start.media, formData), source: 'start' };
	const end = sorted.find((k) => k.role === 'last' && k.media != null);
	if (end) return { url: thumbUrlFor(end.media, formData), source: 'end' };
	return { url: null, source: 'slate' };
}

function buildTimelineShots(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	formData: Record<string, unknown> | null | undefined,
	runs: Record<string, DirectorRunState> | null | undefined
): ConsoleShot[] {
	const dc = caps.modes.director;
	const shots = doc.timeline.shots;
	const identityCtx: DirectorShotIdentityContext = { caps, formData };
	return shots.map((shot, index) => {
		const rail = deriveRailModel(doc, caps, shot.id);
		// Every row the user added counts, picked LoRA or not — the tab and the
		// row chip answer "does this shot carry IC-LoRA entries", the submission
		// decides what is complete enough to send.
		const icLoraEntries = shot.ic_lora;
		const hasIcLora = dc?.icLora === true && icLoraEntries.length > 0;

		const tabs: ConsoleShot['tabs'] = [{ id: 'selection', label: 'Selection' }];
		if (caps.references === 'per_shot') {
			const references = shot.segments[0]?.references;
			tabs.push({ id: 'references', label: referencesTabLabel(caps, formData, references) });
		} else if (caps.references === 'whole') {
			tabs.push({ id: 'references', label: referencesWholeTabLabel(caps, formData) });
		}
		if (dc?.icLora) {
			tabs.push({ id: 'ic_lora', label: `IC-LoRA · ${icLoraEntries.length}` });
		}

		return {
			id: shot.id,
			index,
			number: shotNumber(index),
			title: timelineShotTitle(doc, shot, index),
			durationSeconds: rail.totalSeconds,
			startSeconds: 0,
			frames: rail.totalFrames,
			capFrames: rail.maxFrames,
			newFrames: null,
			fps: rail.fps,
			fpsLocked: dc?.fpsLocked === true,
			thumb: withRunPoster(timelineShotThumb(shot, formData), runs?.[shot.id]),
			// A timeline shot has no native continuation (unlike a chain's
			// `continue` seam) -- `continue_from_previous` is purely an
			// editor/compile-time join, so its badge reads the SAME
			// runs-dependent state a chain 'continue' shot does (W3) even
			// though the join control below renders a toggle, not a chip.
			badge:
				shot.continue_from_previous && index > 0
					? dependentBadge(doc, shots[index - 1].id, shot.id, runs, identityCtx)
					: 'independent',
			hasIcLora,
			icLoraCount: icLoraEntries.length,
			run: consoleRunState(runs?.[shot.id]),
			tabs,
			canRemove: shots.length > 1,
			canDuplicate: true
		};
	});
}

/** Consecutive-shot joins for a timeline film -- LTX has no native
 * multi-shot continuation, but the console still offers the Continue | Fresh
 * cut toggle (it sets `shots[i].continue_from_previous`, compiled at
 * generation time in a later wave -- see PLAN.md §B/W2's join ruling). */
/** The contiguous run of timeline shot ids back to the nearest fresh cut
 * (inclusive), ending at `uptoIndex` -- mirrors `chainSpanFromFreshCut` for
 * the timeline routing (each shot in the span still submits as its OWN
 * independent generation; see `buildDirectorSubmission`'s `checkedShotIds`,
 * there is no server-side compile step for a timeline document). */
function timelineSpanFromFreshCut(shots: DirectorTimelineShot[], uptoIndex: number): string[] {
	let start = uptoIndex;
	while (start > 0 && shots[start].continue_from_previous) start -= 1;
	return shots.slice(start, uptoIndex + 1).map((s) => s.id);
}

function buildTimelineJoins(
	shots: DirectorTimelineShot[],
	runs: Record<string, DirectorRunState> | null | undefined,
	checked: Set<string> | null | undefined
): ConsoleJoin[] {
	const joins: ConsoleJoin[] = [];
	for (let i = 0; i < shots.length - 1; i++) {
		const from = shots[i];
		const to = shots[i + 1];
		const isContinue = to.continue_from_previous;
		const missing = isContinue && checked?.has(to.id) && runs?.[from.id]?.status !== 'done';
		if (missing) {
			joins.push({
				afterShotId: from.id,
				beforeShotId: to.id,
				kind: 'missing',
				label: 'MISSING PREDECESSOR',
				sentence: `Shot ${shotNumber(i)} has no output yet.`,
				control: { kind: 'missing', spanShotIds: timelineSpanFromFreshCut(shots, i + 1) },
				overlapFrames: null
			});
			continue;
		}
		joins.push({
			afterShotId: from.id,
			beforeShotId: to.id,
			kind: isContinue ? 'continue' : 'cut',
			label: isContinue ? 'CONTINUES' : 'HARD CUT',
			sentence: isContinue ? `Inherits Shot ${shotNumber(i)}'s last frame.` : 'Starts fresh — nothing shared.',
			control: { kind: 'toggle', value: isContinue ? 'continue' : 'cut' },
			overlapFrames: null
		});
	}
	return joins;
}

// ─── Entry point ────────────────────────────────────────────────────────────

export function deriveConsoleModel(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	ui: { activeShotId: string | null },
	formData?: Record<string, unknown> | null,
	/** `Tab.directorRuns` (PLAN.md §C W3) -- absent/empty reproduces W1/W2's
	 * behaviour exactly (see this file's header note). */
	runs?: Record<string, DirectorRunState> | null,
	/** The console's own transient row-checkbox selection (ShotConsole
	 * component state) -- only ever used to decide whether a broken
	 * continuation's warning block should show (see this file's header note);
	 * never affects which shots/joins/badges exist otherwise. */
	checked?: Set<string> | null
): ConsoleModel {
	const rail = deriveRailModel(doc, caps);

	if (rail.routing === 'chain') {
		const shots = buildChainShots(doc, caps, rail, formData, runs);
		const joins = buildChainJoins(doc, rail, caps, runs, checked);
		return {
			header: buildHeader(doc, caps, rail, shots.length, rail.totalSeconds, runs),
			filmRows: buildFilmRows(doc),
			shots,
			joins,
			canAddShot: rail.canAddShot,
			addShotDisabledReason: rail.canAddShot
				? null
				: `Maximum of ${caps.modes.director?.maxSegments ?? shots.length} shots reached`
		};
	}

	const shots = buildTimelineShots(doc, caps, formData, runs);
	const joins = buildTimelineJoins(doc.timeline.shots, runs, checked);
	const totalSeconds = shots.reduce((sum, s) => sum + s.durationSeconds, 0);
	return {
		header: buildHeader(doc, caps, rail, shots.length, totalSeconds, runs),
		filmRows: buildFilmRows(doc),
		shots,
		joins,
		canAddShot: true,
		addShotDisabledReason: null
	};
}
