// Dependency-ordered submission for a Video Director plan
// (`planDirectorSelection`, directorPlanner.ts). That planner only orders
// and gates a submission plan -- it "never performs the actual predecessor
// -> dependant media handoff" (its own header comment). This module is
// where that handoff actually happens: a plan whose `shotsToSubmit` contains
// a `continue_from_previous` shot together with its own not-yet-done
// predecessor (the "Generate previous + this shot" span, +page.svelte's
// `submitVideoDirectorShots`, and the ordinary Generate button's own
// multi-shot submission) must submit the predecessor first and hold the
// dependant back until the predecessor's OWN generation actually finishes --
// submitting both immediately, one after another, would build the
// dependant's wire doc before its predecessor has any output to inherit
// (`resolvePredecessorFrame`, directorContinuation.ts, would have nothing to
// resolve).
//
// Deliberately has no idea how a shot is actually submitted, how
// `directorRuns` gets updated, or how a generation's completion is
// observed -- all three are the caller's own (`+page.svelte`'s
// WebSocket-driven `directorRuns`/`handleGenerationMessage`), injected here
// as `DirectorDependencyRunnerDeps` so this module stays pure/testable
// (mock deps, no WebSocket, no store).
//
// This runner tracks a predecessor's outcome from its OWN `submit`/
// `waitForTerminal` calls for THIS run of the plan -- never by re-reading
// `getRuns()`'s current status as a proxy for "did the submission I just
// made succeed". `getRuns()` can still show an OLDER run's 'done' status
// for a shot whose fresh resubmission (in THIS plan) failed to even start
// (validation error, network failure) -- trusting that stale snapshot would
// let a dependant inherit a stale predecessor output instead of being
// blocked, and would let a waiter hang forever watching for a generation id
// that was never actually written. `waitForTerminal` is likewise called with
// the EXACT generation id this plan itself just submitted, not "whichever
// run currently occupies the shot".
import { directorPredecessorShotId, type DirectorPredecessorRef } from './directorInputIdentity';
import { resolvePredecessorFrame, type PredecessorRunLike, type PredecessorOutputLike } from './directorContinuation';
import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';

/** Outcome of submitting one shot's own generation -- `generationId` is
 *  required on success so a dependant can be told exactly which generation
 *  to wait for (never "whichever run later occupies the shot"). */
export type DirectorShotSubmitOutcome = { ok: true; generationId: string } | { ok: false; reason?: string };

/** Outcome of waiting for a specific generation id to reach a terminal
 *  state -- `'abandoned'` covers every way that generation id will now
 *  NEVER report a terminal state under this shot: the tab was closed, the
 *  shot's run entry vanished, or the shot was resubmitted/replaced under a
 *  DIFFERENT generation id before the one being waited on ever finished. A
 *  dependant treats `'abandoned'` exactly like `'failed'` -- it must never
 *  be submitted on the strength of a wait that can never resolve. */
export type DirectorShotTerminalOutcome = 'done' | 'failed' | 'abandoned';

export interface DirectorDependencyRunnerDeps {
	/** Fresh snapshot of `Tab.directorRuns`, read again before every shot --
	 *  a predecessor submitted earlier IN THIS SAME PLAN only gets an entry
	 *  once `submit` below actually queues it. Only ever consulted for a
	 *  predecessor OUTSIDE this plan (planDirectorSelection's own guarantee:
	 *  such a predecessor already reads 'done') -- a same-plan predecessor's
	 *  outcome comes from this runner's own bookkeeping instead, never from
	 *  re-reading this snapshot (see this file's header comment). */
	getRuns: () => Record<string, PredecessorRunLike> | null | undefined;
	/** Fresh snapshot of cached per-generation gallery outputs
	 *  (`generationOutputs.ts`'s module-level cache, or an equivalent the
	 *  caller keeps) -- see `resolvePredecessorFrame`'s own doc comment on
	 *  why this is tried before `PredecessorRunLike.posterUrl`. */
	getOutputs: () => Record<string, PredecessorOutputLike> | null | undefined;
	/** Submits exactly one shot's own generation and returns once it has
	 *  been queued (never waits for it to finish) -- `predecessorFrame` is
	 *  the media this shot's request should inherit (`null` for a shot that
	 *  doesn't continue, or whose predecessor was resolved to nothing, which
	 *  never happens here: this runner never calls `submit` for a shot whose
	 *  predecessor didn't resolve -- see `onBlocked` below). A validation or
	 *  start failure returns `{ ok: false }` rather than throwing -- this
	 *  runner treats that exactly like a predecessor failure for any shot
	 *  depending on it.
	 *
	 *  `predecessorRef` is the EXACT `{generationId, outputKey}`
	 *  `resolvePredecessorFrame` resolved `predecessorFrame` FROM (`null`
	 *  exactly when `predecessorFrame` is `null`) -- the caller must stamp
	 *  THIS reference on the run it records (`DirectorRunState.predecessorRef`),
	 *  never re-derive its own from a LATER, separately-timed `runs` snapshot:
	 *  that read happens after `submit`'s own async work (a network round
	 *  trip), during which the predecessor's run entry could in principle
	 *  have moved on, and the two would then disagree about which generation
	 *  this request's media actually came from. */
	submit: (
		shotId: string,
		predecessorFrame: DirectorMediaValue | null,
		predecessorRef: DirectorPredecessorRef | null
	) => Promise<DirectorShotSubmitOutcome>;
	/** Resolves once the run under `generationId` (which this runner itself
	 *  just submitted for `shotId`, in THIS SAME PLAN) reaches a terminal
	 *  state, or is abandoned -- see `DirectorShotTerminalOutcome`. Never
	 *  awaited for a generation id this plan did not itself just submit. */
	waitForTerminal: (shotId: string, generationId: string) => Promise<DirectorShotTerminalOutcome>;
	/** A shot in the plan that could not be submitted -- its predecessor
	 *  (inside this same plan) failed/was abandoned, or its own frame
	 *  couldn't be resolved for some other reason. Never called for a shot
	 *  that doesn't continue from a predecessor. */
	onBlocked: (shotId: string, reason: string) => void;
}

const PREDECESSOR_FAILED_REASON = 'Its previous shot failed to generate';
const PREDECESSOR_ABANDONED_REASON = "Its previous shot's generation was interrupted";

/**
 * Submits `shotsToSubmit` (already in dependency order --
 * `planDirectorSelection`'s own guarantee: a predecessor always precedes its
 * dependant) one at a time, holding a continuation shot back until its
 * predecessor -- if that predecessor is ALSO part of this same plan --
 * actually finishes. A predecessor OUTSIDE this plan is never waited on
 * here: `planDirectorSelection` only lets such a shot through when that
 * predecessor's run already reads 'done' (directorPlanner.ts's
 * "predecessorDone" check), so its output is already there to resolve.
 *
 * `presubmittedGenerationIds` seeds shots the CALLER already submitted
 * through some OTHER mechanism before invoking this plan (the ordinary
 * Generate button's primary-tab submission, which keeps its own
 * initialization path unchanged -- +page.svelte's `startGeneration`) --
 * mapping such a shot id to the generation id it was actually submitted
 * under. A shot present here is never (re)submitted by this runner; a
 * dependant of it waits on that generation id exactly as it would for one
 * `submit` returned in this same call.
 *
 * A predecessor (inside this plan, submitted here OR presubmitted) that
 * fails or is abandoned blocks its dependant -- reported via `onBlocked`,
 * never submitted, never silently treated as a fresh cut.
 */
export async function runDirectorDependencyPlan(
	shotsToSubmit: readonly string[],
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	deps: DirectorDependencyRunnerDeps,
	presubmittedGenerationIds?: Record<string, string> | null
): Promise<void> {
	const batch = new Set(shotsToSubmit);
	const failedInBatch = new Set<string>();
	const generationIdByShot = new Map<string, string>(Object.entries(presubmittedGenerationIds ?? {}));

	for (const shotId of shotsToSubmit) {
		// Already submitted by the caller before this plan even ran (the
		// primary-tab shot) -- nothing left to do for it here.
		if (generationIdByShot.has(shotId)) continue;

		const predecessorId = directorPredecessorShotId(doc, caps, shotId);

		// A predecessor this call is responsible for -- either it's itself in
		// `shotsToSubmit` (submitted, or about to be, by THIS loop), or it was
		// PRESUBMITTED by the caller (`generationIdByShot` is seeded with those
		// up front, line 137) -- must be waited on before its output can be
		// resolved, even when it isn't in `shotsToSubmit` itself (the "remaining
		// shots only" caller contract: the primary shot is presubmitted, never
		// part of `shotsToSubmit`, but a remaining shot continuing from it still
		// depends on ITS generation finishing). Only a predecessor genuinely
		// OUTSIDE this call's knowledge (neither in the batch nor presubmitted)
		// skips the wait -- `planDirectorSelection`'s own guarantee that such a
		// predecessor already reads 'done'.
		if (predecessorId && (batch.has(predecessorId) || generationIdByShot.has(predecessorId))) {
			if (failedInBatch.has(predecessorId)) {
				deps.onBlocked(shotId, PREDECESSOR_FAILED_REASON);
				failedInBatch.add(shotId);
				continue;
			}
			const predecessorGenerationId = generationIdByShot.get(predecessorId);
			if (!predecessorGenerationId) {
				// The predecessor is part of this plan but hasn't been resolved to
				// a generation id yet -- shouldn't happen given `shotsToSubmit`'s
				// dependency order, but never guess at readiness on an unknown
				// outcome.
				deps.onBlocked(shotId, 'Its previous shot has not been generated yet');
				failedInBatch.add(shotId);
				continue;
			}
			const outcome = await deps.waitForTerminal(predecessorId, predecessorGenerationId);
			if (outcome !== 'done') {
				failedInBatch.add(predecessorId);
				deps.onBlocked(shotId, outcome === 'abandoned' ? PREDECESSOR_ABANDONED_REASON : PREDECESSOR_FAILED_REASON);
				failedInBatch.add(shotId);
				continue;
			}
		}

		let predecessorFrame: DirectorMediaValue | null = null;
		let predecessorRef: DirectorPredecessorRef | null = null;
		if (predecessorId) {
			const resolved = resolvePredecessorFrame(doc, caps, shotId, deps.getRuns(), deps.getOutputs());
			if (!resolved.ok) {
				deps.onBlocked(shotId, resolved.reason);
				failedInBatch.add(shotId);
				continue;
			}
			predecessorFrame = resolved.media;
			predecessorRef = { generationId: resolved.predecessor.generationId, outputKey: resolved.predecessor.outputKey };
		}

		const outcome = await deps.submit(shotId, predecessorFrame, predecessorRef);
		if (!outcome.ok) {
			// The shot's own submission (validation/start failure) already
			// reports itself (toasted by the caller's `submit`) -- this runner
			// only needs to remember it failed, so a dependant is blocked
			// rather than inheriting a stale predecessor output.
			failedInBatch.add(shotId);
			continue;
		}
		generationIdByShot.set(shotId, outcome.generationId);
	}
}
