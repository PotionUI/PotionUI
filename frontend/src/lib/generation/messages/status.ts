import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { directorShotIdsFor, withDirectorRunGenerating } from './directorRuns';
import { resolveOwnership } from './ownership';

generationMessageRegistry.register('generation_status', {
	type: 'generation_status',
	handle(msg, ctx) {
		const progress = (msg as { progress?: number | null }).progress ?? null;
		const directorShotIds = directorShotIdsFor(ctx.tab, ctx.generationId);
		// A queued generation reporting progress while the tab has no live
		// owner adopts it on the spot (see ownership.ts) -- this message type
		// is only ever sent for a pending/running generation, so adoption here
		// needs no extra gate.
		const { isOwner, adopted } = resolveOwnership(ctx.tab, ctx.generationId);

		ctx.tabsStore.updateTab(ctx.tabId, {
			...(adopted ? { activeGenerationId: adopted.activeGenerationId } : {}),
			...(isOwner
				? {
						generation: {
							...ctx.tab.generation,
							...(adopted ? adopted.generation : {}),
							currentProgress: msg
						}
					}
				: {}),
			...(directorShotIds
				? { directorRuns: withDirectorRunGenerating(ctx.tab, directorShotIds, progress, ctx.generationId) }
				: {})
		});
	}
});
