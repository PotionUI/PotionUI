import { describe, expect, it } from 'vitest';
import type { ActiveGrid, CompareAxis, GridCell } from '../compareStore.svelte';
import {
	CELL_GAP,
	LABEL_COLUMN,
	MAX_CELL,
	MIN_CELL,
	axisFieldName,
	axisTag,
	axisValueLabel,
	cellAxisSummary,
	cellFormPatch,
	computeCellSize,
	createCellTimer,
	gridDimensions,
	gridProgress,
	gridTemplateColumns,
	gridTitle,
	moveCell,
	queueOrdinals,
	retryableCount,
	stepCell,
	withAxisParams
} from './gridModel';
import { emptyCell, gridFromServer } from '../serverGrid';
import type { ServerGrid } from '../types';

const sampler: CompareAxis = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: [
		{ value: 'euler', label: 'euler' },
		{ value: 'er_sde', label: 'er_sde' },
		{ value: 'dpmpp_2m', label: 'dpmpp_2m' },
		{ value: 'dpmpp_2m_sde', label: 'dpmpp_2m_sde' }
	]
};
const scheduler: CompareAxis = {
	field: 'scheduler',
	type: 'select',
	label: 'Scheduler',
	values: [
		{ value: 'simple', label: 'simple' },
		{ value: 'beta', label: 'beta' },
		{ value: 'karras', label: 'karras' }
	]
};

function cell(x: number, y: number, status: GridCell['status'], over: Partial<GridCell> = {}): GridCell {
	return { ...emptyCell(x, y), generationId: `g${y * 4 + x}`, status, ...over };
}

function grid(statuses: GridCell['status'][], x = sampler, y: CompareAxis | null = scheduler): ActiveGrid {
	const cols = x.values.length;
	const rows = y ? y.values.length : 1;
	return {
		id: 'grid-1',
		config: { armed: true, x, y, lockSeed: true },
		cols,
		rows,
		cells: statuses.map((status, i) => cell(i % cols, Math.floor(i / cols), status))
	};
}

describe('2D navigation', () => {
	const dims = { cols: 4, rows: 3 };

	it('moves across a row and down a column', () => {
		expect(moveCell(dims, 5, 'right')).toBe(6);
		expect(moveCell(dims, 5, 'left')).toBe(4);
		expect(moveCell(dims, 5, 'down')).toBe(9);
		expect(moveCell(dims, 5, 'up')).toBe(1);
	});

	it('stays put at every edge instead of wrapping', () => {
		expect(moveCell(dims, 0, 'left')).toBe(0);
		expect(moveCell(dims, 3, 'right')).toBe(3);
		expect(moveCell(dims, 1, 'up')).toBe(1);
		expect(moveCell(dims, 10, 'down')).toBe(10);
	});

	it('steps linearly in row-major order and clamps at the ends', () => {
		expect(stepCell(dims, 3, 1)).toBe(4);
		expect(stepCell(dims, 4, -1)).toBe(3);
		expect(stepCell(dims, 0, -1)).toBe(0);
		expect(stepCell(dims, 11, 1)).toBe(11);
	});

	it('treats a single-row grid as one row', () => {
		expect(moveCell({ cols: 4, rows: 1 }, 2, 'down')).toBe(2);
		expect(moveCell({ cols: 4, rows: 1 }, 2, 'up')).toBe(2);
	});
});

describe('geometry and labels', () => {
	it('fills the width with equal cells when the grid fits', () => {
		const { size, scrolls } = computeCellSize(1200, 4);
		expect(scrolls).toBe(false);
		expect(size).toBe(Math.floor((1200 - LABEL_COLUMN - CELL_GAP * 4) / 4));
	});

	it('caps the cell size on a wide, short grid', () => {
		expect(computeCellSize(3000, 2).size).toBe(MAX_CELL);
	});

	it('keeps a minimum cell and scrolls once the columns no longer fit', () => {
		expect(computeCellSize(390, 8)).toEqual({ size: MIN_CELL, scrolls: true });
	});

	it('does not scroll at the exact threshold width', () => {
		const width = LABEL_COLUMN + (MIN_CELL + CELL_GAP) * 5;
		expect(computeCellSize(width, 5)).toEqual({ size: MIN_CELL, scrolls: false });
		expect(computeCellSize(width - 1, 5).scrolls).toBe(true);
	});

	it('falls back safely before the container is measured', () => {
		expect(computeCellSize(0, 4)).toEqual({ size: MIN_CELL, scrolls: false });
	});

	it('builds the column template with the pinned label column first', () => {
		expect(gridTemplateColumns(3, 150)).toBe(`${LABEL_COLUMN}px repeat(3, 150px)`);
	});

	it('titles and sizes the grid', () => {
		const g = grid(new Array(12).fill('queued'));
		expect(gridTitle(g.config)).toBe('Sampler × Scheduler');
		expect(gridTitle({ x: sampler, y: null })).toBe('Sampler');
		expect(gridDimensions(g)).toBe('4 × 3');
		expect(axisValueLabel(sampler, 2)).toBe('dpmpp_2m');
		expect(axisValueLabel(null, 0)).toBe('');
	});

	it('tags the axis fields X and Y and nothing else', () => {
		const { config } = grid([]);
		expect(axisTag('sampler', config)).toBe('X');
		expect(axisTag('scheduler', config)).toBe('Y');
		expect(axisTag('steps', config)).toBeNull();
	});
});

describe('progress and queue order', () => {
	it('counts done, failed, running and queued, with one segment per cell', () => {
		const statuses: GridCell['status'][] = [
			'completed',
			'completed',
			'failed',
			'running',
			'queued',
			'deleted',
			'cancelled',
			'queued'
		];
		const progress = gridProgress({ cells: statuses.map((s, i) => cell(i, 0, s)) });
		expect(progress).toMatchObject({ total: 8, done: 2, failed: 2, running: 1, queued: 2, active: true });
		expect(progress.segments).toEqual(['done', 'done', 'failed', 'running', 'queued', 'failed', 'idle', 'queued']);
	});

	it('is inactive once nothing is running or queued', () => {
		expect(gridProgress({ cells: [cell(0, 0, 'completed'), cell(1, 0, 'failed')] }).active).toBe(false);
	});

	it('numbers the queued cells in row-major order', () => {
		const g = grid(['completed', 'running', 'queued', 'queued', 'queued', 'queued', 'queued', 'queued']);
		const ordinals = queueOrdinals(g);
		expect([...ordinals.entries()]).toEqual([
			[2, 1],
			[3, 2],
			[4, 3],
			[5, 4],
			[6, 5],
			[7, 6]
		]);
	});

	it('groups queued cells by the model axis before row-major order', () => {
		const model: CompareAxis = {
			field: 'model',
			type: 'model',
			label: 'Model',
			values: [
				{ value: 'a', label: 'a' },
				{ value: 'b', label: 'b' }
			]
		};
		const g = grid(['queued', 'queued', 'queued', 'queued'], model, scheduler);
		g.cols = 2;
		g.rows = 2;
		g.cells = [0, 1, 2, 3].map((i) => cell(i % 2, Math.floor(i / 2), 'queued'));
		const ordinals = queueOrdinals(g);
		expect(ordinals.get(0)).toBe(1);
		expect(ordinals.get(2)).toBe(2);
		expect(ordinals.get(1)).toBe(3);
		expect(ordinals.get(3)).toBe(4);
	});

	it('counts failed and deleted cells as retryable', () => {
		expect(
			retryableCount({ cells: [cell(0, 0, 'failed'), cell(1, 0, 'deleted'), cell(2, 0, 'cancelled')] })
		).toBe(2);
	});
});

describe('cell form patch', () => {
	it('writes the x and y axis values of the cell', () => {
		const g = grid([]);
		const { patch, skipped } = cellFormPatch(g, { x: 1, y: 2 });
		expect(patch).toEqual({ sampler: 'er_sde', scheduler: 'karras' });
		expect(skipped).toEqual([]);
	});

	it('writes only x on a single-axis grid', () => {
		const g = grid([], sampler, null);
		expect(cellFormPatch(g, { x: 3, y: 0 }).patch).toEqual({ sampler: 'dpmpp_2m_sde' });
	});

	it('skips the prompt and lora axes and reports them', () => {
		const prompt: CompareAxis = {
			field: '__prompt__',
			type: 'prompt',
			label: 'Prompt',
			values: [{ value: { find: 'dusk', replace: 'dawn' }, label: 'dawn' }]
		};
		const lora: CompareAxis = {
			field: 'loras',
			type: 'lora_picker',
			label: 'LoRA',
			values: [{ value: { lora: 'x', strength: 0.8 }, label: 'x · 0.8' }]
		};
		const g = grid([], prompt, lora);
		const result = cellFormPatch(g, { x: 0, y: 0 });
		expect(result.patch).toEqual({});
		expect(result.skipped).toEqual(['Prompt', 'LoRA']);
	});
});

describe('gridFromServer', () => {
	const source: ServerGrid = {
		id: 'g',
		preset_id: 'p',
		tab_id: 't',
		x_axis: sampler,
		y_axis: scheduler,
		lock_seed: false,
		status: 'running',
		created_at: '2026-10-05T10:00:00Z',
		cells: [
			{
				x: 1,
				y: 1,
				generation_id: 'gen',
				status: 'failed',
				axis_values: { sampler: 'er_sde', scheduler: 'beta' },
				seed: 7,
				thumbnail_url: null,
				media_type: 'image',
				error: 'Out of memory'
			}
		]
	};

	it('lays cells out row-major and fills the gaps with empty cells', () => {
		const g = gridFromServer(source);
		expect(g.cols).toBe(4);
		expect(g.rows).toBe(3);
		expect(g.cells).toHaveLength(12);
		expect(g.cells[5]).toMatchObject({ x: 1, y: 1, status: 'failed', error: 'Out of memory', seed: 7 });
		expect(g.cells[0].status).toBe('empty');
		expect(g.config).toMatchObject({ armed: true, lockSeed: false });
	});

	it('maps the server elapsed time and leaves it null when absent', () => {
		const withTime = gridFromServer({ ...source, cells: [{ ...source.cells[0], elapsed_seconds: 21.4 }] });
		expect(withTime.cells[5].elapsedSeconds).toBe(21.4);
		expect(gridFromServer(source).cells[5].elapsedSeconds).toBeNull();
		expect(gridFromServer({ ...source, cells: [{ ...source.cells[0], elapsed_seconds: null }] }).cells[5].elapsedSeconds).toBeNull();
	});

	it('treats a missing y axis as one row', () => {
		const g = gridFromServer({ ...source, y_axis: null, cells: [] });
		expect(g.rows).toBe(1);
		expect(g.cells).toHaveLength(4);
	});

	it('ignores cells outside the axes', () => {
		const g = gridFromServer({ ...source, cells: [{ ...source.cells[0], x: 9 }] });
		expect(g.cells.every((c) => c.status === 'empty')).toBe(true);
	});
});

describe('cell timer', () => {
	it('measures from the first running sighting to completion', () => {
		let now = 1000;
		const timer = createCellTimer(() => now);
		timer.observe([cell(0, 0, 'running', { generationId: 'a' })]);
		now = 19_000;
		const durations = timer.observe([cell(0, 0, 'completed', { generationId: 'a' })]);
		expect(durations.get('a')).toBe(18);
	});

	it('has no time for a cell it never saw running', () => {
		const timer = createCellTimer(() => 5000);
		expect(timer.observe([cell(0, 0, 'completed', { generationId: 'a' })]).has('a')).toBe(false);
	});

	it('keeps the first measurement', () => {
		let now = 0;
		const timer = createCellTimer(() => now);
		timer.observe([cell(0, 0, 'running', { generationId: 'a' })]);
		now = 4000;
		timer.observe([cell(0, 0, 'completed', { generationId: 'a' })]);
		now = 90_000;
		expect(timer.observe([cell(0, 0, 'completed', { generationId: 'a' })]).get('a')).toBe(4);
	});
});

describe('axis naming and details parameters', () => {
	it('names axis fields the same way everywhere', () => {
		expect(axisFieldName('aspect_ratio')).toBe('aspect ratio');
		expect(cellAxisSummary({ quality: '5', aspect_ratio: '16:9' })).toBe('quality = 5, aspect ratio = 16:9');
	});

	it('adds an axis value the parameters lack so its tag has a row, and keeps recorded ones', () => {
		const tags = { quality: 'X', aspect_ratio: 'Y' };
		const values = { quality: '5', aspect_ratio: '16:9' };
		expect(withAxisParams({ seed: 7, quality: 5 }, tags, values)).toEqual({ seed: 7, quality: 5, aspect_ratio: '16:9' });
		const recorded = { quality: 5, aspect_ratio: '16:9' };
		expect(withAxisParams(recorded, tags, values)).toBe(recorded);
		expect(withAxisParams({}, {}, values)).toEqual({});
	});
});
