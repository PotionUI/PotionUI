/** Case-insensitive substring match; a blank/whitespace-only query matches everything. */
export function matchesSearch(label: string, query: string): boolean {
	const needle = query.trim().toLowerCase();
	if (!needle) return true;
	return label.toLowerCase().includes(needle);
}

export function filterBySearch<T>(items: T[], query: string, getLabel: (item: T) => string): T[] {
	return items.filter((item) => matchesSearch(getLabel(item), query));
}
