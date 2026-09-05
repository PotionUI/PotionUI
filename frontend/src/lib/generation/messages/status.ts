import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import type { GenerationRoutingBackend } from '$lib/types/api';
import { directorShotIdsFor, withDirectorRunGenerating } from './directorRuns';
import { isTabsCurrentGeneration } from './ownership';

generationMessageRegistry.register('generation_status', {
	type: 'generation_status',
	handle(msg, ctx) {
		// Sent once, on the first `generation_status` message of a run (see
		// `output_serializer.py`) - carries the router's pick, in case the
		// start-generation response was missed (a page reload mid-run).
		const backend = (msg as { backend?: GenerationRoutingBackend }).backend;
		const progress = (msg as { progress?: number | null }).progress ?? null;
		const directorShotIds = directorShotIdsFor(ctx.tab, ctx.generationId);
		const isOwner = isTabsCurrentGeneration(ctx.tab, ctx.generationId);

		ctx.tabsStore.updateTab(ctx.tabId, {
			...(isOwner
				? {
						generation: {
							...ctx.tab.generation,
							currentProgress: msg,
							...(backend ? { routingBackend: backend } : {})
						}
					}
				: {}),
			...(directorShotIds
				? { directorRuns: withDirectorRunGenerating(ctx.tab, directorShotIds, progress, ctx.generationId) }
				: {})
		});
	}
});
