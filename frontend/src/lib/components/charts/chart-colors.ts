/**
 * Maps entities (stable string keys) to one of the 8 fixed viz slots.
 * Color follows the ENTITY, never its rank/index — a filter that removes
 * entries must never repaint the survivors.
 */

export const VIZ_SLOTS = 8;

/** FNV-1a — cheap, deterministic, stable across runs and platforms. */
function hashKey(key: string): number {
	let hash = 0x811c9dc5;
	for (let i = 0; i < key.length; i++) {
		hash ^= key.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193);
	}
	return hash >>> 0;
}

/** Deterministic key -> slot index (1-based, 1..VIZ_SLOTS). */
export function slotForKey(key: string): number {
	return (hashKey(key) % VIZ_SLOTS) + 1;
}

/** Deterministic key -> CSS var reference, e.g. "rgb(var(--viz-3))". */
export function colorForKey(key: string): string {
	return `rgb(var(--viz-${slotForKey(key)}))`;
}

/**
 * Stable slot-by-first-appearance, for callers with a fixed known set
 * (e.g. model_type) who want slot 1 to always mean the same category
 * regardless of hash, as long as they register keys in the same order.
 */
export function createOrderedSlots(): {
	get(key: string): string;
	slotIndex(key: string): number;
} {
	const assigned = new Map<string, number>();
	let next = 0;

	function assign(key: string): number {
		let slot = assigned.get(key);
		if (slot === undefined) {
			slot = (next % VIZ_SLOTS) + 1;
			assigned.set(key, slot);
			next++;
		}
		return slot;
	}

	return {
		get(key: string): string {
			return `rgb(var(--viz-${assign(key)}))`;
		},
		slotIndex(key: string): number {
			return assign(key);
		}
	};
}

/** Default shared ordered-slot allocator for module-level fixed sets. */
export const ORDERED_SLOTS = createOrderedSlots();

export interface FoldableItem {
	key: string;
	label: string;
	count: number;
	[extra: string]: unknown;
}

/**
 * Keeps the top `max` items by count and folds the rest into a single
 * "Other" entry (summed count), preserving the total. Never generates
 * a 9th distinct hue — "Other" is meant to be styled as neutral/muted.
 */
export function foldTail<T extends FoldableItem>(
	items: T[],
	max = VIZ_SLOTS
): Array<T | { key: 'other'; label: 'Other'; count: number }> {
	if (items.length <= max) return [...items];

	const sorted = [...items].sort((a, b) => b.count - a.count);
	const head = sorted.slice(0, max);
	const tail = sorted.slice(max);
	const otherCount = tail.reduce((sum, item) => sum + item.count, 0);

	return [...head, { key: 'other' as const, label: 'Other' as const, count: otherCount }];
}
