import { generationMessageRegistry, type GenerationMessageHandler } from '$lib/registries/generationMessageRegistry';
import { playGenerationErrorSound } from '$lib/utils/generationSounds';

// Handles both 'generation_error' and 'generation_cancelled' - moved verbatim
// from the shared switch-case in generate/+page.svelte.
const handler: GenerationMessageHandler = {
	type: 'generation_error',
	handle(message: any, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;

		const error = message.error ?? message.data?.message ?? message.data?.error ?? 'Generation failed';
		const detail = message.detail ?? message.data?.detail ?? null;

		// For errors, preserve any partial results (images/videos/audios) that were generated
		const totalItems =
			(targetTab.generation.batchImages?.length || 0) +
			(targetTab.generation.batchVideos?.length || 0) +
			(targetTab.generation.batchAudios?.length || 0);
		const totalTime = targetTab.generation.startedAt
			? Math.max(0, (Date.now() - targetTab.generation.startedAt) / 1000)
			: targetTab.generation.totalTime;

		ctx.tabsStore.updateTab(targetTabId, {
			activeGenerationId: null,
			generation: {
				...targetTab.generation,
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
				workbenchTotal: totalItems,
				queue: (targetTab.generation.queue || []).filter(
					(q: { generation_id: string }) => q.generation_id !== ctx.generationId
				)
			}
		});

		// Cancellation is a deliberate user action, not an outcome to alert on.
		if (message.type === 'generation_error' && targetTab.soundOnError) {
			playGenerationErrorSound();
		}

		// Unsubscribe from WebSocket updates
		if (ctx.generationId) {
			ctx.unsubscribe(ctx.generationId);
		}
	}
};

generationMessageRegistry.register('generation_error', handler);
generationMessageRegistry.register('generation_cancelled', { ...handler, type: 'generation_cancelled' });
