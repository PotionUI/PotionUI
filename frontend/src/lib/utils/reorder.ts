/**
 * Generic array-reorder primitives for drag-handle list UIs (see
 * LoraPickerField.svelte). Pure and side-effect free - a reorder only
 * permutes array positions, it never touches an item's own contents, so
 * whatever extra keys an item carries (e.g. LoraPickerItem's saved_strength)
 * ride along untouched.
 */

/** Moves the item at `from` to `to`, clamping `to` into [0, items.length - 1].
 * Returns the same array reference when there is nothing to do (out-of-range
 * `from`, or `to` resolves to the same slot `from` is already in) so callers
 * can cheaply skip re-emitting a no-op. */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
	if (items.length === 0 || from < 0 || from >= items.length) return items;
	const target = Math.min(Math.max(to, 0), items.length - 1);
	if (from === target) return items;

	const next = items.slice();
	const [moved] = next.splice(from, 1);
	next.splice(target, 0, moved);
	return next;
}

/** Converts a "dropped before/after this row" drag gesture into the `to`
 * index `moveItem` expects. `target` is the row the user dropped on,
 * expressed in the array's indices BEFORE the move (removing `from` shifts
 * everything after it left by one, which this accounts for). */
export function dropIndexFor(from: number, target: number, position: 'before' | 'after'): number {
	let to = target;
	if (from < target) to -= 1;
	if (position === 'after') to += 1;
	return to;
}
