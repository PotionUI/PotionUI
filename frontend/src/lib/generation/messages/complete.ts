import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { playGenerationCompleteSound } from '$lib/utils/generationSounds';
import { directorShotIdsFor, withDirectorRunTerminal, withoutDirectorRunLink } from './directorRuns';
import { isTabsCurrentGeneration, withoutQueueEntry, nextQueueCandidate, beginGenerationOwnership } from './ownership';
import { peekGenerationOutputs, retireGeneration, leadOutputPatch } from './generationOutputs';

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
		const outputs = peekGenerationOutputs(ctx.generationId);
		const output = leadOutputPatch(outputs);

		// Video Director run tracking (PLAN.md §C W3) -- the poster is this
		// generation's own output video specifically (regardless of whether a
		// video happens to be the lead workbench item). Independent of tab
		// ownership below -- a backgrounded Director shot's own run must still
		// resolve.
		const directorShotIds = directorShotIdsFor(targetTab, ctx.generationId);
		const directorPosterUrl = (outputs.videos[0] as any)?.originalUrl ?? (outputs.videos[0] as any)?.url ?? null;

		const remainingQueue = withoutQueueEntry(targetTab.generation.queue, ctx.generationId);
		const generationPatch: Record<string, unknown> = { queue: remainingQueue };
		let nextActiveGenerationId: string | null = null;

		// The rest of this generation's state (display, progress, workbench
		// indices, batch arrays) only ever belongs to the tab if this
		// generation currently owns the shared display -- see ownership.ts. A
		// queued/backgrounded generation completing must not disturb whatever
		// the tab is actually showing (or clear its `isGenerating` state out
		// from under it). Batch arrays are restored from THIS generation's own
		// cache unconditionally here -- never inferred from whatever the tab
		// already had (e.g. from adoption, or nothing at all if this
		// generation was owner from the start and its own gallery_update(s)
		// already wrote them; either way this is authoritative).
		const totalTime = targetTab.generation.startedAt
			? Math.max(0, (Date.now() - targetTab.generation.startedAt) / 1000)
			: targetTab.generation.totalTime;

		if (isOwner) {
			Object.assign(generationPatch, {
				isGenerating: false,
				currentGeneration: output.file_type
					? {
							...targetTab.generation.currentGeneration,
							status: 'completed',
							current_image: output.current_image,
							current_video: output.current_video,
							current_audio: output.current_audio,
							current_mesh: output.current_mesh,
							file_type: output.file_type
						}
					: {
							...targetTab.generation.currentGeneration,
							status: 'completed'
						},
				currentProgress: null,
				totalTime,
				lastDurationMs: totalTime !== null ? Math.round(totalTime * 1000) : targetTab.generation.lastDurationMs,
				workbenchIndex: output.workbenchIndex,
				workbenchTotal: output.workbenchTotal,
				batchImages: output.batchImages,
				batchVideos: output.batchVideos,
				batchAudios: output.batchAudios,
				batchMeshes: output.batchMeshes
			});

			// Live adoption (see ownership.ts): the outgoing owner is gone --
			// hand the display to whichever queued generation should take over
			// next, immediately restored from its own cache. `lastDurationMs`
			// above survives this (adoption's patch never sets it).
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

		// Drop the cache entry for good (no further gallery_update follows a
		// terminal event, so nothing may recreate it) and unsubscribe.
		retireGeneration(ctx.generationId, ctx.unsubscribe);
	}
});
