export function pageCount(total: number, pageSize: number): number {
	if (pageSize <= 0) return 1;
	return Math.max(1, Math.ceil(total / pageSize));
}

export function clampPage(page: number, count: number): number {
	const max = Math.max(1, count);
	return Math.min(Math.max(1, page), max);
}
