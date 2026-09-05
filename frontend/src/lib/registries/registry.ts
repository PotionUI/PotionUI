/**
 * Generic last-wins registration map used by the generation message registry,
 * artifact renderer registry, and the plugin-facing renderer/field registries.
 *
 * Registrations for a key are layered by OWNER rather than flattened into a
 * single slot. Core registers under `CORE_OWNER`; each plugin registers under
 * its own `pluginOwner(id)`. Within a key the layers are ordered by recency
 * and the most recent one is what `get`/`has`/`list` see, so the visible
 * behaviour is still last-wins. What layering buys is reversibility:
 * `unregister(key, owner)` removes only that owner's layer and reveals the
 * next most recent one, so tearing down one plugin can neither drop the core
 * registration it shadowed nor another plugin's live one. Re-registering an
 * owner replaces its layer and moves it to the top. `unregister(key)` with no
 * owner drops the key outright, layers and all.
 */
export const CORE_OWNER = 'core';

/** Ownership token for anything a plugin registers. */
export function pluginOwner(pluginId: string): string {
	return `plugin:${pluginId}`;
}

export interface Registry<T> {
	register(key: string, value: T, owner?: string): void;
	unregister(key: string, owner?: string): void;
	get(key: string): T | undefined;
	has(key: string): boolean;
	list(): T[];
	keys(): string[];
}

export function createRegistry<T>(kind: string): Registry<T> {
	const layers = new Map<string, { owner: string; value: T }[]>();

	function top(key: string): { owner: string; value: T } | undefined {
		const stack = layers.get(key);
		return stack?.length ? stack[stack.length - 1] : undefined;
	}

	return {
		register(key: string, value: T, owner: string = CORE_OWNER) {
			const stack = layers.get(key) ?? [];
			if (import.meta.env?.DEV && stack.length) {
				console.warn(`[registry:${kind}] Overriding existing registration for "${key}"`);
			}
			const existing = stack.findIndex((layer) => layer.owner === owner);
			if (existing !== -1) stack.splice(existing, 1);
			stack.push({ owner, value });
			layers.set(key, stack);
		},
		unregister(key: string, owner?: string) {
			if (owner === undefined) {
				layers.delete(key);
				return;
			}
			const stack = layers.get(key);
			if (!stack) return;
			const index = stack.findIndex((layer) => layer.owner === owner);
			if (index === -1) return;
			stack.splice(index, 1);
			if (!stack.length) layers.delete(key);
		},
		get(key: string) {
			return top(key)?.value;
		},
		has(key: string) {
			return layers.has(key);
		},
		list() {
			return Array.from(layers.keys(), (key) => top(key)!.value);
		},
		keys() {
			return Array.from(layers.keys());
		}
	};
}
