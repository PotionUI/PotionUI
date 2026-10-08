import type { ActiveGrid, CompareAxis, GridCell } from '../compareStore.svelte';
import type { Tab } from '$lib/types/tabs';
import { applyPromptReplacement } from '../axisValues';
import { PROMPT_AXIS_FIELD, PROMPT_AXIS_LABEL } from '../types';

export type MoveDirection = 'left' | 'right' | 'up' | 'down';

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

export function cellProgressLabel(progress: NonNullable<GridCell['progress']>): string {
	return progress.percent ? `${progress.step}%` : `${progress.step} / ${progress.total}`;
}

export function gridFraction(grid: Pick<ActiveGrid, 'cells'>): number {
	if (grid.cells.length === 0) return 0;
	let sum = 0;
	for (const cell of grid.cells) {
		const state = segmentState(cell.status);
		if (state === 'done' || state === 'failed') sum += 1;
		else if (state === 'running' && cell.progress && cell.progress.total > 0) {
			sum += Math.min(1, Math.max(0, cell.progress.step / cell.progress.total));
		}
	}
	return sum / grid.cells.length;
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

export function axisFieldName(field: string): string {
	return field.replace(/_/g, ' ');
}

export function axisFieldLabel(field: string): string {
	if (field === PROMPT_AXIS_FIELD) return PROMPT_AXIS_LABEL;
	const spaced = axisFieldName(field).trim();
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function withAxisParams(
	params: Record<string, unknown>,
	tags: Record<string, string>,
	values: Record<string, string>
): Record<string, unknown> {
	const missing = Object.keys(tags).filter((field) => !(field in params) && values[field] !== undefined);
	if (missing.length === 0) return params;
	return { ...params, ...Object.fromEntries(missing.map((field) => [field, values[field]])) };
}

export function cellAxisSummary(axisValues: Record<string, string>): string {
	return Object.entries(axisValues)
		.map(([field, value]) => `${axisFieldName(field)} = ${value}`)
		.join(', ');
}

export interface PromptReplacement {
	find: string;
	replace: string;
}

export interface CellFormPatch {
	patch: Record<string, unknown>;
	prompt: PromptReplacement | null;
	skipped: string[];
}

function axisRawValue(axis: CompareAxis | null, index: number): unknown {
	return axis?.values[index]?.value;
}

export function cellFormPatch(grid: Pick<ActiveGrid, 'config'>, cell: Pick<GridCell, 'x' | 'y'>): CellFormPatch {
	const patch: Record<string, unknown> = {};
	const skipped: string[] = [];
	let prompt: PromptReplacement | null = null;
	const entries: Array<[CompareAxis | null, number]> = [
		[grid.config.x, cell.x],
		[grid.config.y, cell.y]
	];
	for (const [axis, index] of entries) {
		if (!axis) continue;
		if (axis.field === PROMPT_AXIS_FIELD) {
			const value = axisRawValue(axis, index) as { find?: string; replace?: string | null } | undefined;
			if (value?.find && typeof value.replace === 'string') prompt = { find: value.find, replace: value.replace };
			continue;
		}
		if (axis.type === 'lora_picker') {
			skipped.push(axis.label);
			continue;
		}
		const value = axisRawValue(axis, index);
		if (value === undefined) continue;
		patch[axis.field] = value;
	}
	return { patch, prompt, skipped };
}

export function cellTabUpdates(
	tab: Pick<Tab, 'formData' | 'prompt' | 'promptSegments'>,
	result: CellFormPatch
): Partial<Tab> {
	const updates: Partial<Tab> = { formData: { ...(tab.formData ?? {}), ...result.patch } };
	if (result.prompt) {
		updates.prompt = applyPromptReplacement(tab.prompt ?? '', result.prompt);
		updates.promptSegments = (tab.promptSegments ?? []).map((segment) =>
			segment.enabled === false || segment.isDisabled
				? segment
				: { ...segment, content: applyPromptReplacement(segment.content, result.prompt) }
		);
	}
	return updates;
}

export function cellFormPatchSummary(
	result: CellFormPatch,
	axisValues: Record<string, string>
): string[] {
	const parts = Object.entries(result.patch).map(
		([field, value]) => `${axisFieldName(field)} = ${typeof value === 'object' ? axisValues[field] : value}`
	);
	if (result.prompt) parts.push(`prompt "${result.prompt.find}" replaced with "${result.prompt.replace}"`);
	return parts;
}

export interface CellSize {
	size: number;
	scrolls: boolean;
}

export const CELL_GAP = 12;
export const LABEL_COLUMN = 96;
export const MIN_CELL = 112;
export const MAX_CELL = 320;

export const HEADER_HEIGHT = 44;
export const SCROLLER_PADDING = 12;

export interface HeightFit {
	height: number;
	rows: number;
	aspect: number;
	header?: number;
}

export function computeCellSize(containerWidth: number, cols: number, heightFit?: HeightFit): CellSize {
	if (containerWidth <= 0 || cols <= 0) return { size: MIN_CELL, scrolls: false };
	const available = containerWidth - LABEL_COLUMN - CELL_GAP * cols;
	const fitted = Math.floor(available / cols);
	if (fitted < MIN_CELL) return { size: MIN_CELL, scrolls: true };
	let size = Math.min(fitted, MAX_CELL);
	if (heightFit && heightFit.height > 0 && heightFit.rows > 0 && heightFit.aspect > 0) {
		const header = heightFit.header && heightFit.header > 0 ? heightFit.header : HEADER_HEIGHT;
		const rowHeight = (heightFit.height - header - CELL_GAP * heightFit.rows) / heightFit.rows;
		size = Math.max(MIN_CELL, Math.min(size, Math.floor(rowHeight * heightFit.aspect)));
	}
	return { size, scrolls: false };
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
