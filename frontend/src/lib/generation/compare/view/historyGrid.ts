import type { GenerationHistoryItem } from '$lib/types/history';

type GridFields = Pick<GenerationHistoryItem, 'grid' | 'grid_id' | 'grid_x' | 'grid_y' | 'axis_values'>;

function titleCase(field: string): string {
	const spaced = field.replace(/_/g, ' ');
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function isStackEntry(item: GridFields): boolean {
	return !!item.grid;
}

export function isLooseCell(item: GridFields): boolean {
	return !!item.grid_id && !item.grid;
}

export function stackBadge(item: GridFields): string {
	return item.grid ? `${item.grid.cols} × ${item.grid.rows}` : '';
}

export interface StackInfo {
	title: string;
	count: string;
}

export function stackInfo(item: GridFields): StackInfo | null {
	if (!item.grid) return null;
	const fields = Object.keys(item.axis_values ?? {});
	const total = item.grid.cell_count;
	return {
		title: fields.map(titleCase).join(' × '),
		count: `${total} ${total === 1 ? 'cell' : 'cells'}`
	};
}

export function cellChip(item: GridFields & { grid_cols?: number | null }): string {
	const x = item.grid_x;
	const y = item.grid_y;
	if (x === null || x === undefined || y === null || y === undefined) return 'cell';
	const cols = item.grid_cols ?? 0;
	return cols > 0 ? `cell ${y * cols + x + 1}` : `cell ${x + 1},${y + 1}`;
}
