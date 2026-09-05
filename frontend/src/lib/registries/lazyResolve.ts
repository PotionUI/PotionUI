/**
 * The shared resolve path for every registry whose entries are either a
 * `static` core component or a `lazy` plugin-hosted one.
 *
 * PUBLICATION RULE. A lazy resolve is an await, and a plugin can be
 * unregistered or revised while it is in flight. Whatever started the resolve
 * therefore has to still be the registered entry before its component may be
 * cached or returned: the identity checked is the entry's owner, its declared
 * asset and the plugin's current revision. Without that check a load begun
 * before a plugin was removed still populates `resolvedCache` when it lands,
 * so `has(key)` reports false while the next `resolve(key)` keeps handing back
 * the removed plugin's component. When the identity has moved, the resolve
 * restarts against whatever is registered now; when the key is gone entirely,
 * it resolves to null.
 *
 * TRANSIENT NULLS. A resolve that fails - the asset was briefly unreachable,
 * the module had no default export - is never cached. `resolvePluginComponent`
 * drops its own failed entry for the same reason, so the next caller retries
 * rather than inheriting a permanent null.
 */
import { resolvePluginComponent, getPluginRevision } from '$lib/plugin-api/componentResolver';
import type { Registry } from './registry';

export type LazyComponentEntry =
	| { kind: 'static'; component: any }
	| { kind: 'lazy'; pluginId: string; asset: string };

/** What must still hold when a resolved component is published. */
export function lazyEntryIdentity(entry: LazyComponentEntry): string {
	return entry.kind === 'static'
		? 'static'
		: `${entry.pluginId}|${entry.asset}|${getPluginRevision(entry.pluginId)}`;
}

export async function resolveLazyEntry(
	registry: Registry<LazyComponentEntry>,
	resolvedCache: Map<string, any | null>,
	key: string
): Promise<any | null> {
	for (;;) {
		if (resolvedCache.has(key)) return resolvedCache.get(key) ?? null;

		const entry = registry.get(key);
		if (!entry) return null;

		if (entry.kind === 'static') {
			resolvedCache.set(key, entry.component);
			return entry.component;
		}

		const identity = lazyEntryIdentity(entry);
		const component = await resolvePluginComponent(entry.pluginId, entry.asset);

		const current = registry.get(key);
		if (!current) return null;
		if (lazyEntryIdentity(current) !== identity) continue;
		if (component === null) return null;

		resolvedCache.set(key, component);
		return component;
	}
}
