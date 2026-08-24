/**
 * type -> Svelte field component registry, resolved by `FormField.svelte`
 * instead of the old hardcoded if-chain. An entry is either a `static`
 * component (core field, registered eagerly by `builtin.ts`) or a `lazy`
 * plugin-hosted component (registered by `stores/fieldTypes.ts` once
 * `/api/fields/types` responds), resolved on demand via
 * `plugin-api/componentResolver`.
 */
import { createRegistry } from '$lib/registries/registry';
import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

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
		registry.register(type, { kind: 'static', component: entry.component });
	} else {
		registry.register(type, { kind: 'lazy', pluginId: entry.pluginId, asset: entry.asset });
		resolvedCache.delete(type);
	}
}

export function unregisterFieldComponent(type: string): void {
	registry.unregister(type);
	resolvedCache.delete(type);
}

/**
 * Resolve a field `type` to its component. Static entries resolve
 * synchronously (wrapped in a resolved Promise); lazy plugin entries import
 * their compiled ES module on first use and cache the result. Unknown types
 * resolve to `null` - callers render the "unsupported field type" fallback.
 */
export async function resolveFieldComponent(type: string): Promise<any | null> {
	if (resolvedCache.has(type)) {
		return resolvedCache.get(type) ?? null;
	}

	const entry = registry.get(type);
	if (!entry) return null;

	if (entry.kind === 'static') {
		resolvedCache.set(type, entry.component);
		return entry.component;
	}

	const component = await resolvePluginComponent(entry.pluginId, entry.asset);
	resolvedCache.set(type, component);
	return component;
}

export function hasFieldComponent(type: string): boolean {
	return registry.has(type);
}

export function listFieldTypes(): string[] {
	return registry.keys();
}
