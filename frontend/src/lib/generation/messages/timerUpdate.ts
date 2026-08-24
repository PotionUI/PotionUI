import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

generationMessageRegistry.register('timer_update', {
	type: 'timer_update',
	handle(message: any, ctx) {
		if (!message.timer_name) return;

		const updatedTimers = {
			...(ctx.tab.generation.pipeTimers || {}),
			[message.timer_name]: {
				time_seconds: message.timer_value || 0,
				formatted_time: message.formatted_time || `${message.timer_value || 0}${message.timer_unit || 's'}`
			}
		};

		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				pipeTimers: updatedTimers
			}
		});
	}
});
