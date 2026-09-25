import type { Snippet } from 'svelte';

export type ColumnPriority = 0 | 1 | 2;
export type ColumnAlign = 'left' | 'right' | 'center';

export interface DataTableColumn<Row> {
	key: string;
	label: string;
	width: string;
	sortable?: boolean;
	align?: ColumnAlign;
	priority?: ColumnPriority;
	mono?: boolean;
	cell?: Snippet<[Row]>;
	accessor?: (row: Row) => string | number | null | undefined;
}

export function visibleColumns<Row>(
	columns: readonly DataTableColumn<Row>[],
	opts: { detail: boolean }
): DataTableColumn<Row>[] {
	return columns.filter((column) => (column.priority ?? 0) !== 2 || opts.detail);
}

export function gridTemplateColumns<Row>(
	columns: readonly DataTableColumn<Row>[],
	opts: { narrow: boolean; selectable?: boolean; trailing?: boolean }
): string {
	const tracks = columns
		.filter((column) => !opts.narrow || (column.priority ?? 0) === 0)
		.map((column) => column.width);
	if (opts.selectable) tracks.unshift('36px');
	if (opts.trailing) tracks.push('40px');
	return tracks.join(' ');
}

export function alignClass(align: ColumnAlign | undefined): string {
	if (align === 'right') return 'text-right justify-end';
	if (align === 'center') return 'text-center justify-center';
	return 'text-left justify-start';
}
