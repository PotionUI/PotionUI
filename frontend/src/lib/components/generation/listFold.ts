export interface ListFold<T> {
	visible: T[];
	foldable: boolean;
	total: number;
	hidden: number;
}

export function foldList<T>(items: T[], above: number, show: number, expanded: boolean): ListFold<T> {
	const foldable = items.length > above;
	const visible = foldable && !expanded ? items.slice(0, show) : items;
	return { visible, foldable, total: items.length, hidden: items.length - visible.length };
}
