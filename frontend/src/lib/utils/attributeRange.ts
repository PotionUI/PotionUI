import { formatStrength } from './loraStrength';

/**
 * Pure helpers for the `range` attribute field type - a LoRA author's
 * recommended strength published as a [lo, hi] pair rather than a single
 * number. On the WIRE (`AttributeDefinition` with `field_type: 'range'`) a
 * set value is a 2-element array `[lo, hi]` with `lo <= hi`; the backend
 * coerces a bare number `x` or a 1-element array `[x]` into a degenerate
 * `[x, x]` range before it ever reaches the frontend, but `normalizeRange` is
 * defensive about all three shapes anyway since it is the single reader of
 * this shape throughout the frontend.
 */

/** Reads the wire shape of a `range` attribute value: a bare number, a
 * 1-element array, or a 2-element `[lo, hi]` array (order not required -
 * the returned tuple is always sorted). Anything else (`null`, `undefined`,
 * `NaN`, an empty/oversized array, non-numeric entries) is "not set". */
export function normalizeRange(value: unknown): [number, number] | null {
	if (typeof value === 'number') {
		return Number.isFinite(value) ? [value, value] : null;
	}
	if (!Array.isArray(value) || value.length === 0 || value.length > 2) return null;
	const nums = value.map((entry) => (typeof entry === 'number' ? entry : NaN));
	if (nums.some((n) => !Number.isFinite(n))) return null;
	if (nums.length === 1) return [nums[0], nums[0]];
	const [a, b] = nums;
	return a <= b ? [a, b] : [b, a];
}

/** `[0.7, 1] -> "0.70–1.00"` (EN DASH, not a hyphen); a degenerate range
 * (`lo === hi`) collapses to a single number: `[1, 1] -> "1"`. Number
 * formatting is delegated to `formatStrength` so a range's endpoints are
 * never displayed with different precision rules than a plain strength. */
export function formatRange(range: [number, number]): string {
	const [lo, hi] = range;
	if (lo === hi) return formatStrength(lo);
	return `${formatStrength(lo)}–${formatStrength(hi)}`;
}

/** The default-weight rule for seeding a newly-added LoRA row's strength from
 * its recommended range: the preset's own configured default wins whenever
 * it already falls inside the range (an author's range is a recommendation,
 * not a mandate); otherwise the range's upper bound - the author's "full
 * effect" setting - is used. No range at all (`range === null`) leaves
 * `defaultStrength` untouched, same as before ranges existed. */
export function strengthWithinRange(defaultStrength: number, range: [number, number] | null): number {
	if (!range) return defaultStrength;
	const [lo, hi] = range;
	if (defaultStrength >= lo && defaultStrength <= hi) return defaultStrength;
	return hi;
}
