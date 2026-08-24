/**
 * Pure helper for DynamicForm.svelte: decides whether reaction-computed
 * `set_value` corrections (see `$lib/form/reactions`' `valueChanges`) need to
 * be merged into `formData`, and produces the merged result.
 *
 * Extracted so the merge decision - previously an inline block gated by a
 * "did a trigger field actually change" heuristic - can be unit tested
 * without mounting the component, and so the value-equality check can be
 * made value-type-aware (see `valuesEqual` below) instead of a bare `!==`.
 */

/**
 * Structural equality for reaction `set_value` results. A plain `!==` is
 * correct for scalars but wrong for arrays/objects: `processSchemaWithReactions`
 * deep-clones the whole schema (`JSON.parse(JSON.stringify(schema))`) on every
 * call, so an object/array `set_value` is a fresh reference on every
 * reprocess even when its contents haven't changed. Falling back to
 * reference equality there would mark the field "changed" forever and never
 * let the caller's reactive statement settle.
 */
export function valuesEqual(a: unknown, b: unknown): boolean {
	if (a === b) return true;
	if (a && b && typeof a === 'object' && typeof b === 'object') {
		return JSON.stringify(a) === JSON.stringify(b);
	}
	return false;
}

/**
 * Merges reaction-computed `valueChanges` into `formData`.
 *
 * Returns the original `formData` reference (unchanged) when every proposed
 * value already matches what's there - this is the loop guard: a caller that
 * only reassigns its `formData` state when `changed` is true will not
 * re-trigger a reactive recompute when nothing actually moved.
 */
export function applyReactionValueChanges(
	formData: Record<string, any>,
	valueChanges: Record<string, any>
): { data: Record<string, any>; changed: boolean } {
	let changed = false;
	const updated = { ...formData };

	for (const [fieldName, newValue] of Object.entries(valueChanges)) {
		if (!valuesEqual(updated[fieldName], newValue)) {
			updated[fieldName] = newValue;
			changed = true;
		}
	}

	return changed ? { data: updated, changed: true } : { data: formData, changed: false };
}
