// Dependency-ordered submission for a Video Director plan
// (`planDirectorSelection`, directorPlanner.ts). That planner only orders
// and gates a submission plan -- it "never performs the actual predecessor
// -> dependant media handoff" (its own header comment). This module is
// where that handoff actually happens: a plan whose `shotsToSubmit` contains
// a `continue_from_previous` shot together with its own not-yet-done
// predecessor (the "Generate previous + this shot" span, +page.svelte's
// `submitVideoDirectorShots`) must submit the predecessor first and hold the
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
import { directorPredecessorShotId } from './directorInputIdentity';
import { resolvePredecessorFrame, type PredecessorRunLike, type PredecessorOutputLike } from './directorContinuation';
import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';

export interface DirectorDependencyRunnerDeps {
	/** Fresh snapshot of `Tab.directorRuns`, read again before every shot --
	 *  a predecessor submitted earlier IN THIS SAME PLAN only gets an entry
	 *  once `submit` below actually queues it. */
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
	 *  predecessor didn't resolve -- see `onBlocked` below). */
	submit: (shotId: string, predecessorFrame: DirectorMediaValue | null) => Promise<void>;
	/** Resolves once `shotId`'s OWN run reaches a terminal state -- driven by
	 *  the same `generation_complete`/`generation_error` handling that
	 *  writes `directorRuns` (complete.ts/error.ts). Only ever awaited for a
	 *  shot this runner itself just submitted earlier in the SAME plan. */
	waitForTerminal: (shotId: string) => Promise<'done' | 'failed'>;
	/** A shot in the plan that could not be submitted -- its predecessor
	 *  (inside this same plan) failed, or its own frame couldn't be
	 *  resolved for some other reason. Never called for a shot that doesn't
	 *  continue from a predecessor. */
	onBlocked: (shotId: string, reason: string) => void;
}

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
 * A predecessor (inside this plan) that fails blocks its dependant --
 * reported via `onBlocked`, never submitted, never silently treated as a
 * fresh cut.
 */
export async function runDirectorDependencyPlan(
	shotsToSubmit: readonly string[],
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	deps: DirectorDependencyRunnerDeps
): Promise<void> {
	const batch = new Set(shotsToSubmit);
	const failedInBatch = new Set<string>();

	for (const shotId of shotsToSubmit) {
		const predecessorId = directorPredecessorShotId(doc, caps, shotId);

		if (predecessorId && batch.has(predecessorId)) {
			if (failedInBatch.has(predecessorId)) {
				deps.onBlocked(shotId, 'Its previous shot failed to generate');
				failedInBatch.add(shotId);
				continue;
			}
			// Only wait when the predecessor isn't ALREADY done (e.g. a mixed
			// plan where the predecessor was done before this plan even ran,
			// but happens to be included in `shotsToSubmit` for some other
			// reason -- nothing to wait for).
			if (deps.getRuns()?.[predecessorId]?.status !== 'done') {
				const outcome = await deps.waitForTerminal(predecessorId);
				if (outcome === 'failed') {
					failedInBatch.add(predecessorId);
					deps.onBlocked(shotId, 'Its previous shot failed to generate');
					failedInBatch.add(shotId);
					continue;
				}
			}
		}

		let predecessorFrame: DirectorMediaValue | null = null;
		if (predecessorId) {
			const resolved = resolvePredecessorFrame(doc, caps, shotId, deps.getRuns(), deps.getOutputs());
			if (!resolved.ok) {
				deps.onBlocked(shotId, resolved.reason);
				failedInBatch.add(shotId);
				continue;
			}
			predecessorFrame = resolved.media;
		}

		await deps.submit(shotId, predecessorFrame);
	}
}
