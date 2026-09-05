import { createRegistry, pluginOwner } from './registry';
import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { lazyEntryIdentity } from './lazyResolve';

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
	registry.register(`${pluginId}:${key}`, { pluginId, asset }, pluginOwner(pluginId));
}

export function unregisterModelView(pluginId: string, key: string): void {
	registry.unregister(`${pluginId}:${key}`, pluginOwner(pluginId));
}

/**
 * All registered sections, each resolving its component lazily via
 * componentResolver. Sections are not cached, but a resolve still outlives the
 * registration that started it, so the same publication rule as
 * `lazyResolve.ts` applies: a component whose entry was unregistered or revised
 * mid-load resolves to null rather than rendering a section the plugin no
 * longer contributes.
 */
export function listModelViewSections(): ModelViewSection[] {
	return registry.keys().map((compositeKey) => {
		const entry = registry.get(compositeKey)!;
		return {
			pluginId: entry.pluginId,
			key: compositeKey.slice(entry.pluginId.length + 1),
			component: resolveSection(compositeKey, entry)
		};
	});
}

async function resolveSection(
	compositeKey: string,
	entry: { pluginId: string; asset: string }
): Promise<any | null> {
	const lazy = { kind: 'lazy', ...entry } as const;
	const identity = lazyEntryIdentity(lazy);
	const component = await resolvePluginComponent(entry.pluginId, entry.asset);

	const current = registry.get(compositeKey);
	if (!current || lazyEntryIdentity({ kind: 'lazy', ...current }) !== identity) return null;
	return component;
}
