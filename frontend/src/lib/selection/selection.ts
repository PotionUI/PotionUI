/**
 * Pure selection math shared by the History and Library grids: shift-click
 * ranges, ctrl/cmd toggles, and marquee (rubber-band) drag selection. No DOM,
 * no store - a grid supplies row-major order and measured rects, this
 * returns the next selection. DOM wiring lives in `./marquee`.
 */

export interface SelectionRect {
	left: number;
	top: number;
	right: number;
	bottom: number;
}

/** Normalizes two drag corners (given in either order) into a rect. */
export function rectFromPoints(x1: number, y1: number, x2: number, y2: number): SelectionRect {
	return {
		left: Math.min(x1, x2),
		top: Math.min(y1, y2),
		right: Math.max(x1, x2),
		bottom: Math.max(y1, y2)
	};
}

/** Open-interval overlap: rects that only touch at an edge do not intersect. */
export function rectsIntersect(a: SelectionRect, b: SelectionRect): boolean {
	return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/** Ids (in `order`) whose rect intersects the marquee. Ids with no rect (not yet measured) are skipped. */
export function idsInMarquee(
	order: string[],
	rects: Map<string, SelectionRect>,
	marquee: SelectionRect
): string[] {
	return order.filter((id) => {
		const rect = rects.get(id);
		return rect !== undefined && rectsIntersect(rect, marquee);
	});
}

/** Plain click: toggle `id` in the current selection. */
export function toggleSelection(current: string[], id: string): string[] {
	return current.includes(id) ? current.filter((existing) => existing !== id) : [...current, id];
}

/**
 * Shift-click: the row-major range between `anchorId` and `targetId`
 * (inclusive of both), unioned with the current selection. Falls back to a
 * plain toggle when there is no anchor, or the anchor is no longer in
 * `order` (e.g. a filter change dropped it from the loaded page).
 */
export function rangeSelection(
	order: string[],
	anchorId: string | null,
	targetId: string,
	current: string[]
): string[] {
	if (anchorId === null) return toggleSelection(current, targetId);

	const anchorIndex = order.indexOf(anchorId);
	const targetIndex = order.indexOf(targetId);
	if (anchorIndex === -1 || targetIndex === -1) return toggleSelection(current, targetId);

	const [start, end] =
		anchorIndex <= targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex];
	const union = new Set(current);
	for (const id of order.slice(start, end + 1)) union.add(id);
	return [...union];
}

/**
 * Result of a marquee drag: `replace` for a plain drag (the marquee's ids
 * become the whole selection), `add` for a shift/ctrl-held drag (unioned
 * onto the selection as it stood before the drag started).
 */
export function applyMarquee(
	preDragSelection: string[],
	marqueeIds: string[],
	mode: 'replace' | 'add'
): string[] {
	if (mode === 'replace') return [...new Set(marqueeIds)];
	const union = new Set(preDragSelection);
	for (const id of marqueeIds) union.add(id);
	return [...union];
}
