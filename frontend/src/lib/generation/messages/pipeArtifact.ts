import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

generationMessageRegistry.register('pipe_artifact', {
	type: 'pipe_artifact',
	handle(message: any, ctx) {
		const artifact = {
			index: message.index ?? -1,
			artifact_type: message.artifact_type,
			artifact_data: message.artifact_data,
			pipe_id: message.pipe_id,
			pipe_name: message.pipe_name,
			timestamp: new Date().toISOString()
		};

		ctx.tabsStore.updateTab(ctx.tabId, {
			generation: {
				...ctx.tab.generation,
				artifacts: [...(ctx.tab.generation.artifacts || []), artifact]
			}
		});
	}
});
