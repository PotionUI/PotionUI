export type PageSlot = number | 'ellipsis';

export const PAGE_WINDOW_SLOTS = 7;

export function pageWindow(
	currentPage: number,
	totalPages: number,
	slots: number = PAGE_WINDOW_SLOTS
): PageSlot[] {
	const total = Math.max(0, Math.floor(totalPages));
	if (total <= 0) return [];

	// Below 7 the "both ends + current and its neighbours" layout cannot stay width-stable.
	const width = Math.max(PAGE_WINDOW_SLOTS, Math.floor(slots));
	if (total <= width) {
		return Array.from({ length: total }, (_, i) => i + 1);
	}

	const current = Math.min(Math.max(1, Math.floor(currentPage)), total);
	const run = width - 2;

	if (current <= run - 1) {
		return [...Array.from({ length: run }, (_, i) => i + 1), 'ellipsis', total];
	}

	if (current >= total - (run - 2)) {
		return [1, 'ellipsis', ...Array.from({ length: run }, (_, i) => total - run + 1 + i)];
	}

	const before = Math.floor((run - 3) / 2);
	const middle = Array.from({ length: run - 2 }, (_, i) => current - before + i);
	return [1, 'ellipsis', ...middle, 'ellipsis', total];
}
