import { generationMessageRegistry, type GenerationMessageHandler } from '$lib/registries/generationMessageRegistry';
import { playGenerationErrorSound } from '$lib/utils/generationSounds';
import { directorShotIdsFor, withDirectorRunTerminal, withoutDirectorRunLink } from './directorRuns';
import { isTabsCurrentGeneration, withoutQueueEntry, nextQueueCandidate, beginGenerationOwnership } from './ownership';
import { peekGenerationOutputs, retireGeneration } from './generationOutputs';

// Handles both 'generation_error' and 'generation_cancelled' - moved verbatim
// from the shared switch-case in generate/+page.svelte.
const handler: GenerationMessageHandler = {
	type: 'generation_error',
	handle(message: any, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;
		const isOwner = isTabsCurrentGeneration(targetTab, ctx.generationId);

		const error = message.error ?? message.data?.message ?? message.data?.error ?? 'Generation failed';
		const detail = message.detail ?? message.data?.detail ?? null;

		// Cleared regardless of ownership -- a failed/cancelled generation gets
		// no further gallery_update, so its cache entry would otherwise never
		// be reclaimed.
		const { images, videos, audios } = peekGenerationOutputs(ctx.generationId);

		// Video Director run tracking (PLAN.md §C W3) -- a cancellation resolves
		// to 'failed' the same as a real error: Retry is the right recovery
		// either way, and DirectorRunState has no separate 'cancelled' state.
		// Independent of tab ownership below -- a backgrounded Director shot's
		// own run must still resolve.
		const directorShotIds = directorShotIdsFor(targetTab, ctx.generationId);

		const remainingQueue = withoutQueueEntry(targetTab.generation.queue, ctx.generationId);
		const generationPatch: Record<string, unknown> = { queue: remainingQueue };
		let nextActiveGenerationId: string | null = null;

		// The rest of this generation's state (display, progress, timers) only
		// ever belongs to the tab if this generation currently owns the shared
		// display -- see ownership.ts. A queued/backgrounded generation
		// terminating must not disturb whatever the tab is actually showing.
		if (isOwner) {
			// For errors, preserve any partial results (images/videos/audios)
			// this generation produced before it failed/was cancelled.
			const totalItems = images.length + videos.length + audios.length;
			const totalTime = targetTab.generation.startedAt
				? Math.max(0, (Date.now() - targetTab.generation.startedAt) / 1000)
				: targetTab.generation.totalTime;

			Object.assign(generationPatch, {
				isGenerating: false,
				currentGeneration:
					message.type === 'generation_error'
						? {
								...targetTab.generation.currentGeneration,
								status: 'failed',
								message: error,
								errorDetail: detail
							}
						: null,
				currentProgress: null,
				totalTime,
				workbenchIndex: totalItems > 0 ? 0 : targetTab.generation.workbenchIndex,
				workbenchTotal: totalItems
			});

			// Live adoption (see ownership.ts): the outgoing owner is gone --
			// hand the display to whichever queued generation should take over
			// next, cold (an older run a newer one's cancellation left running
			// takes precedence -- see nextQueueCandidate).
			const next = nextQueueCandidate(remainingQueue);
			if (next) {
				const adopted = beginGenerationOwnership(next.generation_id);
				Object.assign(generationPatch, adopted.generation);
				nextActiveGenerationId = adopted.activeGenerationId;
			}
		}

		ctx.tabsStore.updateTab(targetTabId, {
			...(isOwner ? { activeGenerationId: nextActiveGenerationId } : {}),
			generation: {
				...targetTab.generation,
				...generationPatch
			},
			...(directorShotIds
				? {
						directorRuns: withDirectorRunTerminal(targetTab, directorShotIds, 'failed', null, Date.now(), ctx.generationId),
						directorRunLinks: withoutDirectorRunLink(targetTab, ctx.generationId)
					}
				: {})
		});

		// Cancellation is a deliberate user action, not an outcome to alert on.
		if (message.type === 'generation_error' && targetTab.soundOnError) {
			playGenerationErrorSound();
		}

		// Drop the cache entry for good (no further gallery_update follows a
		// terminal event, so nothing may recreate it) and unsubscribe.
		retireGeneration(ctx.generationId, ctx.unsubscribe);
	}
};

generationMessageRegistry.register('generation_error', handler);
generationMessageRegistry.register('generation_cancelled', { ...handler, type: 'generation_cancelled' });
