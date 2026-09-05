import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { resolveOwnership } from './ownership';

generationMessageRegistry.register('timer_update', {
	type: 'timer_update',
	handle(message: any, ctx) {
		if (!message.timer_name) return;
		// Per-pipe timers are the tab's shared display -- a background/queued
		// generation's timers have nowhere to render, UNLESS the tab has no
		// live owner at all, in which case this generation adopts it (see
		// ownership.ts). An adopted owner starts its timers fresh.
		const { isOwner, adopted } = resolveOwnership(ctx.tab, ctx.generationId);
		if (!isOwner) return;

		const updatedTimers = {
			...(adopted ? {} : ctx.tab.generation.pipeTimers || {}),
			[message.timer_name]: {
				time_seconds: message.timer_value || 0,
				formatted_time: message.formatted_time || `${message.timer_value || 0}${message.timer_unit || 's'}`
			}
		};

		ctx.tabsStore.updateTab(ctx.tabId, {
			...(adopted ? { activeGenerationId: adopted.activeGenerationId } : {}),
			generation: {
				...ctx.tab.generation,
				...(adopted ? adopted.generation : {}),
				pipeTimers: updatedTimers
			}
		});
	}
});
