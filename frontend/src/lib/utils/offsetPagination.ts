// Offset/limit <-> 1-based page conversions for a list backed by a
// `{limit, offset}` API that reports `total` (e.g. generation history) —
// pure so the conversions are testable without mounting the `Pagination`
// primitive that consumes them.

/** 1-based page number for the given offset. */
export function currentPageFromOffset(offset: number, limit: number): number {
	return Math.floor(offset / limit) + 1;
}

/** Total page count for `total` items; at least 1 so a page indicator never
 *  reads "1 / 0" (an empty or not-yet-loaded list). */
export function totalPagesFromCount(total: number, limit: number): number {
	return Math.max(1, Math.ceil(total / limit));
}

/** Inverse of `currentPageFromOffset` — the offset to fetch for a 1-based
 *  page number, clamped to non-negative. */
export function offsetForPage(page: number, limit: number): number {
	return Math.max(0, (page - 1) * limit);
}
