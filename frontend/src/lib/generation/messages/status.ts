import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

generationMessageRegistry.register('generation_status', {
	type: 'generation_status',
	handle(msg, ctx) {
		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				currentProgress: msg
			}
		});
	}
});
