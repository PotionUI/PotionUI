import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { leadIndex } from '$lib/generation/leadFile';
import { playGenerationCompleteSound } from '$lib/utils/generationSounds';

generationMessageRegistry.register('generation_complete', {
	type: 'generation_complete',
	handle(_message, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;

		// Transition to gallery mode: preserve batch images/videos/audios, set
		// workbench to the lead item — the newest derived item (e.g. an enhance
		// pass) when one exists, otherwise the first item.
		const images = targetTab.generation.batchImages || [];
		const videos = targetTab.generation.batchVideos || [];
		const audios = targetTab.generation.batchAudios || [];
		const allItems = [...images, ...videos, ...audios];
		const totalItems = allItems.length;

		const workbenchIndex = leadIndex(allItems);
		const leadItem = allItems[workbenchIndex];
		const totalTime = targetTab.generation.startedAt
			? Math.max(0, (Date.now() - targetTab.generation.startedAt) / 1000)
			: targetTab.generation.totalTime;

		// Determine file type from where the lead index lands in the batch arrays
		let fileType = 'image';
		if (workbenchIndex >= images.length + videos.length) {
			fileType = 'audio';
		} else if (workbenchIndex >= images.length) {
			fileType = 'video';
		}

		const updatePayload = {
			generation: {
				...targetTab.generation,
				isGenerating: false,
				currentGeneration: leadItem
					? {
							...targetTab.generation.currentGeneration,
							status: 'completed',
							current_image:
								fileType === 'image' ? (leadItem as any)?.url || (leadItem as any)?.originalUrl : null,
							current_video:
								fileType === 'video' ? (leadItem as any)?.url || (leadItem as any)?.originalUrl : null,
							current_audio: fileType === 'audio' ? leadItem : null,
							file_type: fileType
						}
					: {
							...targetTab.generation.currentGeneration,
							status: 'completed'
						},
				currentProgress: null,
				totalTime,
				lastDurationMs: totalTime !== null ? Math.round(totalTime * 1000) : targetTab.generation.lastDurationMs,
				workbenchIndex,
				workbenchTotal: totalItems,
				queue: (targetTab.generation.queue || []).filter(
					(q: { generation_id: string }) => q.generation_id !== ctx.generationId
				)
			}
		};

		ctx.tabsStore.updateTab(targetTabId, { ...updatePayload, activeGenerationId: null });

		if (targetTab.soundOnComplete) {
			playGenerationCompleteSound();
		}

		// Unsubscribe from WebSocket updates
		if (ctx.generationId) {
			ctx.unsubscribe(ctx.generationId);
		}
	}
});
