import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

/**
 * Registers a `generationMessageRegistry` handler for a plugin-declared
 * `generation.output` renderer (manifest `renderers: [{kind:
 * "generation.output", key: <message_type>, component}]`). The handler
 * stores the latest message of that type on the owning tab's generation
 * state (`pluginOutputs[messageType]`); `GenerationPanelHistory.svelte`
 * renders it via `PluginMessageRenderer` for the resolved `pluginId`/`asset`.
 */
export function registerPluginOutputHandler(messageType: string, pluginId: string, asset: string): void {
	generationMessageRegistry.register(messageType, {
		type: messageType,
		handle(msg, ctx) {
			ctx.tabsStore.updateTab(ctx.tabId, {
				generation: {
					...ctx.tab.generation,
					pluginOutputs: {
						...(ctx.tab.generation.pluginOutputs || {}),
						[messageType]: { msg, pluginId, asset }
					}
				}
			});
		}
	});
}
