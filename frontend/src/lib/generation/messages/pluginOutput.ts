import { get } from 'svelte/store';
import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { pluginOwner } from '$lib/registries/registry';
import { tabsStore } from '$lib/stores/tabs';

/**
 * Registers a `generationMessageRegistry` handler for a plugin-declared
 * `generation.output` renderer (manifest `renderers: [{kind:
 * "generation.output", key: <message_type>, component}]`). The handler
 * stores the latest message of that type on the owning tab's generation
 * state (`pluginOutputs[messageType]`); `PluginOutputs.svelte` renders it
 * via `PluginMessageRenderer` for the resolved `pluginId`/`asset`.
 */
export function registerPluginOutputHandler(messageType: string, pluginId: string, asset: string): void {
	generationMessageRegistry.register(
		messageType,
		{
			type: messageType,
			handle(msg, ctx) {
				ctx.tabsStore.updateTab(ctx.tabId, {
					generation: {
						...ctx.tab.generation,
						pluginOutputs: {
							...(ctx.tab.generation.pluginOutputs || {}),
							[messageType]: { msg, pluginId, asset, generationId: ctx.generationId }
						}
					}
				});
			}
		},
		pluginOwner(pluginId)
	);
}

export function unregisterPluginOutputHandler(messageType: string, pluginId: string): void {
	generationMessageRegistry.unregister(messageType, pluginOwner(pluginId));
	dropStoredOutputs(messageType, pluginId);
}

/**
 * A stored output outlives its registration: the message sits on the tab, not
 * in the registry, so a disabled or reloaded plugin would keep rendering from
 * the last snapshot until the tab's next run. Dropping it here is what makes
 * `extensionRefresh`'s disposer a real teardown.
 */
function dropStoredOutputs(messageType: string, pluginId: string): void {
	for (const tab of get(tabsStore).tabs) {
		const outputs = tab.generation.pluginOutputs;
		if (outputs?.[messageType]?.pluginId !== pluginId) continue;

		const remaining = { ...outputs };
		delete remaining[messageType];
		tabsStore.updateTab(tab.id, {
			generation: { ...tab.generation, pluginOutputs: remaining }
		});
	}
}
