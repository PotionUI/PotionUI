export const VIRTUAL_THRESHOLD = 150;

export interface VirtualWindow {
	start: number;
	end: number;
	padTop: number;
	padBottom: number;
}

export function shouldVirtualize(count: number, enabled: boolean, threshold = VIRTUAL_THRESHOLD): boolean {
	return enabled && count > threshold;
}

export function virtualWindow(opts: {
	count: number;
	rowHeight: number;
	scrollTop: number;
	viewport: number;
	overscan?: number;
}): VirtualWindow {
	const { count, rowHeight } = opts;
	const overscan = opts.overscan ?? 8;
	if (count <= 0 || rowHeight <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };
	const first = Math.floor(Math.max(0, opts.scrollTop) / rowHeight);
	const visible = Math.ceil(Math.max(1, opts.viewport) / rowHeight);
	const start = Math.max(0, first - overscan);
	const end = Math.min(count, first + visible + overscan);
	return { start, end, padTop: start * rowHeight, padBottom: (count - end) * rowHeight };
}

export function scrollTopToReveal(opts: {
	index: number;
	rowHeight: number;
	scrollTop: number;
	viewport: number;
	headerHeight?: number;
}): number {
	const head = opts.headerHeight ?? 0;
	const top = opts.index * opts.rowHeight;
	const bottom = top + opts.rowHeight;
	if (top < opts.scrollTop) return top;
	if (bottom > opts.scrollTop + opts.viewport - head) return bottom - opts.viewport + head;
	return opts.scrollTop;
}
