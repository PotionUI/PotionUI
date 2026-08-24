import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import type { QueuedGeneration } from '$lib/types/tabs';

// Backend queue position/status change for a generation this tab has enqueued
// (subscribed exactly like a running generation via `subscribe_generation`).
// `dispatchGenerationMessage` resolves the owning tab via
// `findTabByGenerationId`, which — in addition to `currentGeneration` — checks
// each tab's `generation.queue[]`, since a queued generation that isn't the
// tab's "current" one still needs to receive these.
generationMessageRegistry.register('queue_update', {
	type: 'queue_update',
	handle(message: any, ctx) {
		const generationId: string | undefined = message.generation_id ?? ctx.generationId;
		if (!generationId) return;

		const status: QueuedGeneration['status'] = message.status === 'running' ? 'running' : 'pending';
		const queuePosition: number | null = message.queue_position ?? null;

		const existingQueue: QueuedGeneration[] = ctx.tab.generation.queue || [];
		const entryIndex = existingQueue.findIndex((q) => q.generation_id === generationId);

		const updatedEntry: QueuedGeneration = {
			generation_id: generationId,
			queue_position: queuePosition,
			status
		};

		const nextQueue =
			entryIndex === -1
				? [...existingQueue, updatedEntry]
				: existingQueue.map((q, i) => (i === entryIndex ? updatedEntry : q));

		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				queue: nextQueue
			}
		});
	}
});
