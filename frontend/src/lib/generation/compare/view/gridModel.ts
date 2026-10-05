import type { ActiveGrid, CompareAxis, GridCell } from '../compareStore.svelte';

export const GRIDS_BASE = '/api/generations/grids';

export const PROMPT_AXIS_FIELD = '__prompt__';

export interface ApiGridCell {
	x: number;
	y: number;
	generation_id: string | null;
	status: GridCell['status'];
	axis_values: Record<string, string>;
	seed: number | null;
	thumbnail_url: string | null;
	media_type: string | null;
	error: string | null;
	elapsed_seconds?: number | null;
}

export interface ApiGrid {
	id: string;
	preset_id: string;
	tab_id: string | null;
	x_axis: CompareAxis;
	y_axis: CompareAxis | null;
	lock_seed: boolean;
	status: 'running' | 'completed' | 'partial' | 'cancelled';
	created_at: string;
	cells: ApiGridCell[];
}

export type MoveDirection = 'left' | 'right' | 'up' | 'down';

export function emptyCell(x: number, y: number): GridCell {
	return {
		x,
		y,
		generationId: null,
		status: 'empty',
		axisValues: {},
		seed: null,
		thumbnailUrl: null,
		mediaType: null,
		error: null,
		elapsedSeconds: null,
		progress: null,
		previewUrl: null
	};
}

export function gridFromApi(source: ApiGrid): ActiveGrid {
	const cols = Math.max(1, source.x_axis.values.length);
	const rows = source.y_axis ? Math.max(1, source.y_axis.values.length) : 1;
	const cells: GridCell[] = [];
	for (let y = 0; y < rows; y += 1) {
		for (let x = 0; x < cols; x += 1) cells.push(emptyCell(x, y));
	}
	for (const cell of source.cells) {
		if (cell.x < 0 || cell.x >= cols || cell.y < 0 || cell.y >= rows) continue;
		cells[cell.y * cols + cell.x] = {
			x: cell.x,
			y: cell.y,
			generationId: cell.generation_id,
			status: cell.status,
			axisValues: cell.axis_values ?? {},
			seed: cell.seed,
			thumbnailUrl: cell.thumbnail_url,
			mediaType: cell.media_type,
			error: cell.error,
			elapsedSeconds: cell.elapsed_seconds ?? null,
			progress: null,
			previewUrl: null
		};
	}
	return {
		id: source.id,
		config: { armed: true, x: source.x_axis, y: source.y_axis, lockSeed: source.lock_seed },
		cells,
		cols,
		rows
	};
}

export function cellIndex(grid: Pick<ActiveGrid, 'cols'>, x: number, y: number): number {
	return y * grid.cols + x;
}

export function moveCell(
	grid: Pick<ActiveGrid, 'cols' | 'rows'>,
	index: number,
	direction: MoveDirection
): number {
	const x = index % grid.cols;
	const y = Math.floor(index / grid.cols);
	if (direction === 'left') return x > 0 ? index - 1 : index;
	if (direction === 'right') return x < grid.cols - 1 ? index + 1 : index;
	if (direction === 'up') return y > 0 ? index - grid.cols : index;
	return y < grid.rows - 1 ? index + grid.cols : index;
}

export function stepCell(grid: Pick<ActiveGrid, 'cols' | 'rows'>, index: number, delta: -1 | 1): number {
	const next = index + delta;
	return next < 0 || next >= grid.cols * grid.rows ? index : next;
}

export function axisValueLabel(axis: CompareAxis | null, index: number): string {
	return axis?.values[index]?.label ?? '';
}

export function gridTitle(config: Pick<ActiveGrid['config'], 'x' | 'y'>): string {
	if (config.x && config.y) return `${config.x.label} × ${config.y.label}`;
	return config.x?.label ?? '';
}

export function gridDimensions(grid: Pick<ActiveGrid, 'cols' | 'rows'>): string {
	return `${grid.cols} × ${grid.rows}`;
}

export type SegmentState = 'done' | 'running' | 'queued' | 'failed' | 'idle';

export function segmentState(status: GridCell['status']): SegmentState {
	if (status === 'completed') return 'done';
	if (status === 'running') return 'running';
	if (status === 'queued') return 'queued';
	if (status === 'failed' || status === 'deleted') return 'failed';
	return 'idle';
}

export interface GridProgress {
	total: number;
	done: number;
	failed: number;
	running: number;
	queued: number;
	active: boolean;
	segments: SegmentState[];
}

export function gridProgress(grid: Pick<ActiveGrid, 'cells'>): GridProgress {
	const segments = grid.cells.map((cell) => segmentState(cell.status));
	const count = (state: SegmentState) => segments.filter((s) => s === state).length;
	const running = count('running');
	const queued = count('queued');
	return {
		total: grid.cells.length,
		done: count('done'),
		failed: count('failed'),
		running,
		queued,
		active: running + queued > 0,
		segments
	};
}

export function queueOrdinals(grid: Pick<ActiveGrid, 'cells' | 'cols' | 'config'>): Map<number, number> {
	const modelOnX = grid.config.x?.field === 'model';
	const modelOnY = grid.config.y?.field === 'model';
	const queued = grid.cells
		.map((cell, index) => ({ cell, index }))
		.filter(({ cell }) => cell.status === 'queued');
	queued.sort((a, b) => {
		const group = (c: GridCell) => (modelOnX ? c.x : modelOnY ? c.y : 0);
		return group(a.cell) - group(b.cell) || a.index - b.index;
	});
	const ordinals = new Map<number, number>();
	queued.forEach(({ index }, position) => ordinals.set(index, position + 1));
	return ordinals;
}

export function retryableCount(grid: Pick<ActiveGrid, 'cells'>): number {
	return grid.cells.filter((cell) => cell.status === 'failed' || cell.status === 'deleted').length;
}

export function axisTag(field: string, config: Pick<ActiveGrid['config'], 'x' | 'y'>): 'X' | 'Y' | null {
	if (config.x?.field === field) return 'X';
	if (config.y?.field === field) return 'Y';
	return null;
}

export function cellAxisSummary(axisValues: Record<string, string>): string {
	return Object.entries(axisValues)
		.map(([field, value]) => `${field} = ${value}`)
		.join(', ');
}

export interface CellFormPatch {
	patch: Record<string, unknown>;
	skipped: string[];
}

function axisRawValue(axis: CompareAxis | null, index: number): unknown {
	return axis?.values[index]?.value;
}

export function cellFormPatch(grid: Pick<ActiveGrid, 'config'>, cell: Pick<GridCell, 'x' | 'y'>): CellFormPatch {
	const patch: Record<string, unknown> = {};
	const skipped: string[] = [];
	const entries: Array<[CompareAxis | null, number]> = [
		[grid.config.x, cell.x],
		[grid.config.y, cell.y]
	];
	for (const [axis, index] of entries) {
		if (!axis) continue;
		if (axis.field === PROMPT_AXIS_FIELD || axis.type === 'lora_picker') {
			skipped.push(axis.label);
			continue;
		}
		const value = axisRawValue(axis, index);
		if (value === undefined) continue;
		patch[axis.field] = value;
	}
	return { patch, skipped };
}

export interface CellSize {
	size: number;
	scrolls: boolean;
}

export const CELL_GAP = 12;
export const LABEL_COLUMN = 96;
export const MIN_CELL = 112;
export const MAX_CELL = 320;

export function computeCellSize(containerWidth: number, cols: number): CellSize {
	if (containerWidth <= 0 || cols <= 0) return { size: MIN_CELL, scrolls: false };
	const available = containerWidth - LABEL_COLUMN - CELL_GAP * cols;
	const fitted = Math.floor(available / cols);
	if (fitted < MIN_CELL) return { size: MIN_CELL, scrolls: true };
	return { size: Math.min(fitted, MAX_CELL), scrolls: false };
}

export function gridTemplateColumns(cols: number, size: number): string {
	return `${LABEL_COLUMN}px repeat(${cols}, ${size}px)`;
}

export function formatSeconds(seconds: number): string {
	return `${Math.max(0, Math.round(seconds))} s`;
}

export function cellStatusLabel(status: GridCell['status']): string {
	switch (status) {
		case 'completed':
			return 'Completed';
		case 'running':
			return 'Running';
		case 'queued':
			return 'Queued';
		case 'failed':
			return 'Failed';
		case 'cancelled':
			return 'Cancelled';
		case 'deleted':
			return 'Deleted';
		default:
			return 'Empty';
	}
}

export function cellNumber(index: number): number {
	return index + 1;
}

export interface CellTimer {
	observe(cells: GridCell[]): Map<string, number>;
}

export function createCellTimer(now: () => number = () => Date.now()): CellTimer {
	const started = new Map<string, number>();
	const durations = new Map<string, number>();
	return {
		observe(cells) {
			for (const cell of cells) {
				const id = cell.generationId;
				if (!id) continue;
				if (cell.status === 'running' && !started.has(id)) started.set(id, now());
				if (cell.status === 'completed' && started.has(id) && !durations.has(id)) {
					durations.set(id, (now() - (started.get(id) as number)) / 1000);
				}
			}
			return new Map(durations);
		}
	};
}
