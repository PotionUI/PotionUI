import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import type { GenerationRoutingBackend } from '$lib/types/api';

generationMessageRegistry.register('generation_status', {
	type: 'generation_status',
	handle(msg, ctx) {
		// Sent once, on the first `generation_status` message of a run (see
		// `output_serializer.py`) - carries the router's pick, in case the
		// start-generation response was missed (a page reload mid-run).
		const backend = (msg as { backend?: GenerationRoutingBackend }).backend;
		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				currentProgress: msg,
				...(backend ? { routingBackend: backend } : {})
			}
		});
	}
});
