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

export interface FlatJustifiedBox<T> extends JustifiedBox<T> {
	/** Last box of its packer row. */
	rowEnd: boolean;
}

/**
 * Flattens the packer's rows into one row-major list, integer-width and
 * row-end-flagged so a `display:flex; flex-wrap:wrap` container can render
 * every box from a single flat keyed `{#each}` (one card, one stable DOM
 * identity for its whole lifetime, however a reflow reshuffles rows) while
 * still breaking onto exactly the rows the packer chose.
 *
 * A row's float widths sum to *exactly* `containerWidth - gaps` by
 * construction (see `flush` above) - but flex-wrap decides a line break on
 * each item's own hypothetical width before any shrink/grow, and the
 * browser's internal layout units aren't bit-identical to this module's
 * doubles (Chromium's `LayoutUnit` rounds to 1/64px). Even a
 * fraction-of-a-pixel rounding difference in that comparison can push a row
 * over the container's width and wrap its last card onto a line of its own.
 * Flooring every width to a whole pixel guarantees each box the browser lays
 * out is never wider than our math promised; giving only the last box of
 * each row `flex-grow` (the caller's job - this just flags which box that
 * is) lets that one box alone absorb the sub-pixel remainder the floor left
 * behind, invisible on a single card and never enough to tip the row's total
 * past the container width.
 */
export function flattenJustifiedRows<T>(rows: JustifiedRow<T>[]): FlatJustifiedBox<T>[] {
	const flat: FlatJustifiedBox<T>[] = [];
	for (const row of rows) {
		row.forEach((box, i) => {
			flat.push({
				item: box.item,
				width: Math.floor(box.width),
				height: box.height,
				rowEnd: i === row.length - 1
			});
		});
	}
	return flat;
}
