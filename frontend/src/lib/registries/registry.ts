/**
 * Generic last-wins registration map used by the generation message registry,
 * artifact renderer registry, and (later) plugin-facing renderer/field registries.
 */
export interface Registry<T> {
	register(key: string, value: T): void;
	unregister(key: string): void;
	get(key: string): T | undefined;
	has(key: string): boolean;
	list(): T[];
	keys(): string[];
}

export function createRegistry<T>(kind: string): Registry<T> {
	const map = new Map<string, T>();

	return {
		register(key: string, value: T) {
			if (import.meta.env?.DEV && map.has(key)) {
				console.warn(`[registry:${kind}] Overriding existing registration for "${key}"`);
			}
			map.set(key, value);
		},
		unregister(key: string) {
			map.delete(key);
		},
		get(key: string) {
			return map.get(key);
		},
		has(key: string) {
			return map.has(key);
		},
		list() {
			return Array.from(map.values());
		},
		keys() {
			return Array.from(map.keys());
		}
	};
}
