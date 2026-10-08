import { describe, expect, it } from 'vitest';
import { cellChip, isLooseCell, isStackEntry, stackBadge, stackInfo } from './historyGrid';

const stack = {
	grid: { id: 'g1', cols: 4, rows: 3, cell_count: 12 },
	grid_id: 'g1',
	grid_x: 0,
	grid_y: 0,
	axis_values: { sampler: 'euler', scheduler: 'simple' }
};

const loose = { grid: null, grid_id: 'g1', grid_x: 2, grid_y: 1, axis_values: { sampler: 'dpmpp_2m' } };

describe('history grid entries', () => {
	it('tells a stack entry from a loose cell from a plain generation', () => {
		expect(isStackEntry(stack)).toBe(true);
		expect(isLooseCell(stack)).toBe(false);
		expect(isStackEntry(loose)).toBe(false);
		expect(isLooseCell(loose)).toBe(true);
		expect(isStackEntry({})).toBe(false);
		expect(isLooseCell({})).toBe(false);
	});

	it('badges the stack with columns × rows', () => {
		expect(stackBadge(stack)).toBe('4 × 3');
		expect(stackBadge(loose)).toBe('');
	});

	it('names the axes and counts the cells for the info bar', () => {
		expect(stackInfo(stack)).toEqual({ title: 'Sampler × Scheduler', count: '12 cells' });
		expect(stackInfo({ ...stack, axis_values: { sampler: 'euler' }, grid: { ...stack.grid, cell_count: 1 } })).toEqual({
			title: 'Sampler',
			count: '1 cell'
		});
		expect(stackInfo(loose)).toBeNull();
	});

	it('names the prompt axis with its full axis label and capitalises other fields', () => {
		expect(
			stackInfo({ ...stack, axis_values: { __prompt__: 'dawn', seed: '42' } })
		).toEqual({ title: 'Prompt: find and replace × Seed', count: '12 cells' });
	});

	it('numbers a loose cell row-major when the column count is known', () => {
		expect(cellChip({ ...loose, grid_cols: 4 })).toBe('cell 7');
	});

	it('falls back to the column and row when the column count is unknown', () => {
		expect(cellChip(loose)).toBe('cell 3,2');
		expect(cellChip({ grid_id: 'g', grid_x: null, grid_y: null })).toBe('cell');
	});
});
