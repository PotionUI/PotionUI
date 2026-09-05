// Shared ownership check for the generation message handlers in this
// directory. `dispatchGenerationMessage` routes an event to a tab via EITHER
// signal (`findTabByGenerationId`: the tab's `currentGeneration` OR an entry
// in `generation.queue`), so a tab can receive events for several
// generations at once (one running/displayed, others queued or backgrounded
// -- e.g. a Video Director multi-shot submission). Every handler that writes
// the tab's SHARED workbench display (activeGenerationId, isGenerating,
// currentGeneration, currentProgress, current media, totalTime, workbench
// indices, pipeTimers) must gate that write on this check first, or a
// non-owning generation's event clobbers what's actually being shown.
import type { Tab, QueuedGeneration } from '$lib/types/tabs';

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
