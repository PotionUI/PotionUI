import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { leadIndex } from '$lib/generation/leadFile';
import { playGenerationCompleteSound } from '$lib/utils/generationSounds';
import { directorShotIdsFor, withDirectorRunTerminal, withoutDirectorRunLink } from './directorRuns';
import { isTabsCurrentGeneration, withoutQueueEntry, nextQueueCandidate, beginGenerationOwnership } from './ownership';
import { takeGenerationOutputs } from './generationOutputs';

generationMessageRegistry.register('generation_complete', {
	type: 'generation_complete',
	handle(_message, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;
		const isOwner = isTabsCurrentGeneration(targetTab, ctx.generationId);

		// `generation_complete` carries no output of its own (the backend
		// broadcasts only `status.model_dump()`) -- read what THIS generation's
		// own `gallery_update`(s) produced, and forget it: no further
		// gallery_update follows a terminal event.
		const { images, videos, audios } = takeGenerationOutputs(ctx.generationId);

		// Video Director run tracking (PLAN.md §C W3) -- the poster is this
		// generation's own output video. Independent of tab ownership below --
		// a backgrounded Director shot's own run must still resolve.
		const directorShotIds = directorShotIdsFor(targetTab, ctx.generationId);
		const directorPosterUrl = (videos[0] as any)?.originalUrl ?? (videos[0] as any)?.url ?? null;

		const remainingQueue = withoutQueueEntry(targetTab.generation.queue, ctx.generationId);
		const generationPatch: Record<string, unknown> = { queue: remainingQueue };
		let nextActiveGenerationId: string | null = null;

		// The rest of this generation's state (display, progress, workbench
		// indices) only ever belongs to the tab if this generation currently
		// owns the shared display -- see ownership.ts. A queued/backgrounded
		// generation completing must not disturb whatever the tab is actually
		// showing (or clear its `isGenerating` state out from under it).
		if (isOwner) {
			// Transition to gallery mode: preserve batch images/videos/audios, set
			// workbench to the lead item — the newest derived item (e.g. an enhance
			// pass) when one exists, otherwise the first item.
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

			Object.assign(generationPatch, {
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
				workbenchTotal: totalItems
			});

			// Live adoption (see ownership.ts): the outgoing owner is gone --
			// hand the display to whichever queued generation should take over
			// next, cold. `lastDurationMs` above survives this (adoption's patch
			// never sets it).
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
						directorRuns: withDirectorRunTerminal(
							targetTab,
							directorShotIds,
							'done',
							directorPosterUrl,
							Date.now(),
							ctx.generationId
						),
						directorRunLinks: withoutDirectorRunLink(targetTab, ctx.generationId)
					}
				: {})
		});

		if (targetTab.soundOnComplete) {
			playGenerationCompleteSound();
		}

		// Unsubscribe from WebSocket updates
		if (ctx.generationId) {
			ctx.unsubscribe(ctx.generationId);
		}
	}
});
