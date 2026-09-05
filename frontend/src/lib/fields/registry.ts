/**
 * type -> Svelte field component registry, resolved by `FormField.svelte`
 * instead of the old hardcoded if-chain. An entry is either a `static`
 * component (core field, registered eagerly by `builtin.ts`) or a `lazy`
 * plugin-hosted component (registered by
 * `plugin-api/extensionRefresh.ts` from `/api/fields/types`), resolved on demand via
 * `plugin-api/componentResolver`.
 */
import { createRegistry, CORE_OWNER, pluginOwner } from '$lib/registries/registry';
import { resolveLazyEntry } from '$lib/registries/lazyResolve';

export type FieldComponentEntry =
	| { kind: 'static'; component: any }
	| { kind: 'lazy'; pluginId: string; asset: string };

const registry = createRegistry<FieldComponentEntry>('field-component');

/** Cache of resolved plugin components, keyed by field type name. */
const resolvedCache = new Map<string, any | null>();

export function registerFieldComponent(
	type: string,
	entry: { component: any } | { pluginId: string; asset: string }
): void {
	if ('component' in entry) {
		registry.register(type, { kind: 'static', component: entry.component }, CORE_OWNER);
	} else {
		registry.register(type, { kind: 'lazy', pluginId: entry.pluginId, asset: entry.asset }, pluginOwner(entry.pluginId));
		resolvedCache.delete(type);
	}
}

export function unregisterFieldComponent(type: string, owner?: string): void {
	registry.unregister(type, owner);
	resolvedCache.delete(type);
}

/**
 * Resolve a field `type` to its component. Static entries resolve
 * synchronously (wrapped in a resolved Promise); lazy plugin entries import
 * their compiled ES module on first use and cache the result. Unknown types
 * resolve to `null` - callers render the "unsupported field type" fallback.
 * Publication rules in `registries/lazyResolve.ts`.
 */
export function resolveFieldComponent(type: string): Promise<any | null> {
	return resolveLazyEntry(registry, resolvedCache, type);
}

export function hasFieldComponent(type: string): boolean {
	return registry.has(type);
}

export function listFieldTypes(): string[] {
	return registry.keys();
}
