import { createRegistry } from './registry';
import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

/**
 * Plugin-provided model detail sections (`renderers: [{kind: "model.view",
 * key, component}]`). Unlike `history.artifact`/`workbench.file` (dispatch
 * by a single type key), `model.view` entries are additive - every
 * registered section renders alongside the core model detail page, keyed by
 * `pluginId:key` so multiple plugins (or one plugin with several sections)
 * can register independently.
 */
export interface ModelViewSection {
	pluginId: string;
	key: string;
	component: Promise<any | null>;
}

const registry = createRegistry<{ pluginId: string; asset: string }>('model-view');

export function registerModelView(pluginId: string, key: string, asset: string): void {
	registry.register(`${pluginId}:${key}`, { pluginId, asset });
}

export function unregisterModelView(pluginId: string, key: string): void {
	registry.unregister(`${pluginId}:${key}`);
}

/** All registered sections, each resolving its component lazily via componentResolver. */
export function listModelViewSections(): ModelViewSection[] {
	return registry.keys().map((compositeKey) => {
		const entry = registry.get(compositeKey)!;
		return {
			pluginId: entry.pluginId,
			key: compositeKey.slice(entry.pluginId.length + 1),
			component: resolvePluginComponent(entry.pluginId, entry.asset)
		};
	});
}
