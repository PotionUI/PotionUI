// Justified-rows gallery layout (Flickr/Lightroom style): every row is packed
// edge-to-edge at a uniform height, and every item keeps its native aspect ratio.
// Pure function so it can be unit-tested and re-run cheaply on resize.

export interface JustifiedBox<T> {
	item: T;
	width: number;
	height: number;
}

export type JustifiedRow<T> = JustifiedBox<T>[];

// Extreme panoramas / strips would hog or sliver a row; clamp them and let
// object-contain letterbox the difference.
const MIN_ASPECT = 0.45;
const MAX_ASPECT = 2.6;

export function clampAspect(aspect: number): number {
	if (!Number.isFinite(aspect) || aspect <= 0) return 1;
	return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, aspect));
}

/**
 * Pack items into justified rows.
 * @param items        items with a precomputed aspect ratio (width / height)
 * @param containerWidth available width in px
 * @param targetHeight   preferred row height in px
 * @param gap            horizontal gap between tiles in px
 */
export function layoutJustifiedRows<T>(
	items: Array<{ item: T; aspect: number }>,
	containerWidth: number,
	targetHeight: number,
	gap: number
): JustifiedRow<T>[] {
	if (containerWidth <= 0 || items.length === 0) return [];

	const rows: JustifiedRow<T>[] = [];
	let row: Array<{ item: T; aspect: number }> = [];
	let rowAspectSum = 0;

	const flush = (justify: boolean) => {
		if (row.length === 0) return;
		const gaps = gap * (row.length - 1);
		let height = (containerWidth - gaps) / rowAspectSum;
		if (!justify) {
			// Last row: keep the preferred height unless it would overflow.
			height = Math.min(height, targetHeight);
		}
		rows.push(
			row.map(({ item, aspect }) => ({
				item,
				width: aspect * height,
				height
			}))
		);
		row = [];
		rowAspectSum = 0;
	};

	for (const entry of items) {
		const aspect = clampAspect(entry.aspect);
		row.push({ item: entry.item, aspect });
		rowAspectSum += aspect;

		const gaps = gap * (row.length - 1);
		if (rowAspectSum * targetHeight + gaps >= containerWidth) {
			flush(true);
		}
	}
	flush(false);

	return rows;
}
