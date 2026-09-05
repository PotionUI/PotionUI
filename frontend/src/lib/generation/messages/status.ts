import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import type { GenerationRoutingBackend } from '$lib/types/api';
import { directorShotIdsFor, withDirectorRunGenerating } from './directorRuns';
import { resolveOwnership } from './ownership';

generationMessageRegistry.register('generation_status', {
	type: 'generation_status',
	handle(msg, ctx) {
		// Sent once, on the first `generation_status` message of a run (see
		// `output_serializer.py`) - carries the router's pick, in case the
		// start-generation response was missed (a page reload mid-run).
		const backend = (msg as { backend?: GenerationRoutingBackend }).backend;
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
