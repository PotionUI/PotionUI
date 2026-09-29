import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { isTabsCurrentGeneration } from './ownership';

generationMessageRegistry.register('content_blocked', {
	type: 'content_blocked',
	handle(message: any, ctx) {
		if (!isTabsCurrentGeneration(ctx.tab, ctx.generationId)) return;
		const blockedCount = Number(message.blocked_count ?? 0);
		const total = Number(message.total ?? 0);
		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				currentGeneration: {
					...ctx.tab.generation.currentGeneration,
					content_blocked: { blocked_count: blockedCount, total }
				}
			}
		});
	}
});
