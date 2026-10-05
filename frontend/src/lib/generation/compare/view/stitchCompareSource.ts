import type { MediaToolContext, MediaToolItem } from '$lib/tools/tools';
import type { ActiveGrid } from '../compareStore.svelte';
import { axisValueLabel } from './gridModel';

export interface StitchCompareSourceCell {
	itemId: string | null;
	failed: boolean;
	axisValues: Record<string, string>;
	seed: number | null;
	seconds: number | null;
}

export interface StitchCompareSource {
	cols: number;
	rows: number;
	xLabels: string[];
	yLabels: string[];
	cells: StitchCompareSourceCell[];
}

export interface CellMediaFile {
	url: string;
	filename: string;
	width?: number;
	height?: number;
	paramIndex: number;
}

export interface CompareStitchRequest {
	context: MediaToolContext;
	compare: StitchCompareSource;
}

export function buildCompareStitch(
	grid: Pick<ActiveGrid, 'cells' | 'cols' | 'rows' | 'config'>,
	files: Record<string, CellMediaFile>,
	seconds: Map<string, number> = new Map()
): CompareStitchRequest {
	const items: MediaToolItem[] = [];
	const cells: StitchCompareSourceCell[] = grid.cells.map((cell) => {
		const file = cell.generationId ? files[cell.generationId] : undefined;
		const usable = cell.status === 'completed' && !!file && cell.generationId !== null;
		let itemId: string | null = null;
		if (usable && file && cell.generationId) {
			itemId = `${cell.generationId}:${file.paramIndex}`;
			items.push({
				id: itemId,
				kind: 'image',
				url: file.url,
				filename: file.filename,
				width: file.width,
				height: file.height,
				generationId: cell.generationId,
				paramIndex: file.paramIndex
			});
		}
		return {
			itemId,
			failed: cell.status === 'failed' || cell.status === 'deleted' || (cell.status === 'completed' && !usable),
			axisValues: cell.axisValues,
			seed: cell.seed,
			seconds: cell.elapsedSeconds ?? (cell.generationId ? (seconds.get(cell.generationId) ?? null) : null)
		};
	});

	const xLabels: string[] = [];
	for (let x = 0; x < grid.cols; x += 1) xLabels.push(axisValueLabel(grid.config.x, x));
	const yLabels: string[] = [];
	for (let y = 0; y < grid.rows; y += 1) yLabels.push(grid.config.y ? axisValueLabel(grid.config.y, y) : '');

	const generationIds = items.map((item) => item.generationId as string);
	return {
		context: {
			scope: 'history',
			items,
			kinds: new Set(['image']),
			collectionId: null,
			generations: [],
			generationIds,
			files: []
		},
		compare: { cols: grid.cols, rows: grid.rows, xLabels, yLabels, cells }
	};
}
