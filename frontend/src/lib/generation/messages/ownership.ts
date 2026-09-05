// Shared ownership check for the generation message handlers in this
// directory. `dispatchGenerationMessage` routes an event to a tab via EITHER
// signal (`findTabByGenerationId`: the tab's `currentGeneration` OR an entry
// in `generation.queue`), so a tab can receive events for several
// generations at once (one running/displayed, others queued or backgrounded
// -- e.g. a Video Director multi-shot submission, or a second Generate click
// while the first is still running). Every handler that writes the tab's
// SHARED workbench display (activeGenerationId, isGenerating,
// currentGeneration, currentProgress, current media, totalTime, workbench
// indices, pipeTimers) must gate that write on ownership first, or a
// non-owning generation's event clobbers what's actually being shown.
//
// PRECEDENCE / live adoption: `activeGenerationId` always names the tab's one
// live owner, and nothing here ever displaces it while it stays live -- that
// mirrors the Generate call site (routes/generate/+page.svelte's
// `startGeneration`), where the NEWEST submission always claims
// `activeGenerationId` even while an older run continues in the background
// via `generation.queue`. This module only ever acts on an ORPHANED tab (no
// live owner): when the owner terminates and another generation is still
// queued/running (an older run a newer one's cancellation left behind, or
// simply next in line), or when a queued generation's own event proves it is
// now running while nobody owns the display, that generation is adopted as
// the new owner. It never inherits the OUTGOING owner's progress/media/
// timers -- those reset -- but it DOES immediately show whatever it already
// produced while backgrounded (its own `generationOutputs` cache), since
// that media is real and already known; a generation with nothing cached yet
// starts genuinely blank. The events that follow it (progress, gallery
// output, ...) fill it in exactly as they would for any other owner.
import type { Tab, GenerationState, QueuedGeneration } from '$lib/types/tabs';
import { peekGenerationOutputs, leadOutputPatch } from './generationOutputs';

/** Every generation id `tab` currently claims: the one it owns as its shared
 *  display (`activeGenerationId`), every entry in its backend-enqueued
 *  `generation.queue`, and every id `directorRunLinks` still maps to a shot
 *  (a Director run can be routed to a tab through its queue entry alone,
 *  before/after it also owns the display -- see `directorShotIdsFor`).
 *  This is the full set that loses `tab` as a consumer the moment it closes
 *  -- see `tabsStore.removeTab`'s resource retirement. */
export function tabClaimedGenerationIds(
	tab: Pick<Tab, 'activeGenerationId' | 'generation' | 'directorRunLinks'>
): Set<string> {
	const ids = new Set<string>();
	if (tab.activeGenerationId) ids.add(tab.activeGenerationId);
	for (const queued of tab.generation.queue || []) ids.add(queued.generation_id);
	for (const linkedId of Object.keys(tab.directorRunLinks || {})) ids.add(linkedId);
	return ids;
}

export function isTabsCurrentGeneration(
	tab: Pick<Tab, 'activeGenerationId' | 'generation'>,
	generationId: string | undefined
): boolean {
	if (!generationId) return false;
	const current = tab.generation.currentGeneration;
	const currentId = tab.activeGenerationId ?? current?.generation_id ?? current?.id ?? null;
	return currentId === generationId;
}

/** Removes one entry from a tab's generation queue by id -- every
 *  queue-affecting event resolves its OWN entry regardless of whether it
 *  owns the tab's shared display. */
export function withoutQueueEntry(
	queue: QueuedGeneration[] | undefined,
	generationId: string | undefined
): QueuedGeneration[] {
	const existing = queue || [];
	if (!generationId) return existing;
	return existing.filter((q) => q.generation_id !== generationId);
}

/** A tab with no live owner: nothing currently occupies `activeGenerationId`,
 *  and whatever `currentGeneration` is left over (if any) is already
 *  terminal -- a stale 'completed'/'failed' snapshot, not a run in flight. */
export function isOrphanedTab(tab: Pick<Tab, 'activeGenerationId' | 'generation'>): boolean {
	if (tab.activeGenerationId) return false;
	const status = tab.generation.currentGeneration?.status;
	return !status || status === 'completed' || status === 'failed';
}

/** Picks which queued generation should take over as owner when the current
 *  one terminates: one the backend has already promoted to 'running' wins
 *  outright (this is how an older run that kept going through a newer one's
 *  cancellation re-becomes the visible owner); failing that, the lowest
 *  `queue_position`; failing that, simple queue order. */
export function nextQueueCandidate(queue: QueuedGeneration[] | undefined): QueuedGeneration | null {
	const entries = queue || [];
	if (entries.length === 0) return null;
	const running = entries.find((q) => q.status === 'running');
	if (running) return running;
	const positioned = entries.filter((q) => q.queue_position !== null);
	if (positioned.length > 0) {
		return positioned.reduce((a, b) => (a.queue_position! <= b.queue_position! ? a : b));
	}
	return entries[0];
}

/** The patch that makes `generationId` the tab's new owner. Never carries
 *  over the OUTGOING owner's progress, media or timers -- those always
 *  reset -- but immediately restores whatever `generationId` itself already
 *  produced while backgrounded, from its own `generationOutputs` cache
 *  (never another run's, and never blanked back to nothing just because the
 *  outgoing owner's display was). A generation with nothing cached yet
 *  starts genuinely blank, exactly as before. */
export function beginGenerationOwnership(
	generationId: string,
	now: () => number = Date.now
): { activeGenerationId: string; generation: Partial<GenerationState> } {
	const output = leadOutputPatch(peekGenerationOutputs(generationId));
	const currentGeneration = output.file_type
		? {
				id: generationId,
				generation_id: generationId,
				status: 'running',
				current_image: output.current_image,
				current_video: output.current_video,
				current_audio: output.current_audio,
				current_mesh: output.current_mesh,
				file_type: output.file_type
			}
		: { id: generationId, generation_id: generationId, status: 'running' };

	return {
		activeGenerationId: generationId,
		generation: {
			isGenerating: true,
			currentGeneration,
			currentProgress: null,
			routingBackend: null,
			startedAt: now(),
			totalTime: null,
			batchImages: output.batchImages,
			batchVideos: output.batchVideos,
			batchAudios: output.batchAudios,
			batchMeshes: output.batchMeshes,
			workbenchIndex: output.workbenchIndex,
			workbenchTotal: output.workbenchTotal,
			pipeTimers: {}
		}
	};
}

/** Resolves ownership for an event whose generation id might not (yet) be
 *  `activeGenerationId`: adopts it on the spot when the tab is orphaned and
 *  this id is one it has enqueued (proof the backend is treating it as a
 *  live run) -- for handlers whose message type is only ever sent for a
 *  pending/running generation (`generation_status`, `workbench_update`,
 *  `gallery_update`, `timer_update`). `queue_update` gates this itself on the
 *  message's own reported status instead, since a 'pending' queue_update
 *  must never adopt (see `queueUpdate.ts`). */
export function resolveOwnership(
	tab: Pick<Tab, 'activeGenerationId' | 'generation'>,
	generationId: string | undefined
): { isOwner: boolean; adopted: ReturnType<typeof beginGenerationOwnership> | null } {
	if (!generationId) return { isOwner: false, adopted: null };
	if (isTabsCurrentGeneration(tab, generationId)) return { isOwner: true, adopted: null };
	if (!isOrphanedTab(tab)) return { isOwner: false, adopted: null };
	const inQueue = (tab.generation.queue || []).some((q) => q.generation_id === generationId);
	if (!inQueue) return { isOwner: false, adopted: null };
	return { isOwner: true, adopted: beginGenerationOwnership(generationId) };
}
