import { describe, expect, it } from 'vitest';
import { createStack, apply, beginEdit, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { runSteps } from '../payload';
import { cropDraft, resizeDraft, sliceDraft } from '../steps';
import { analysePixels, type Analysis } from './detect';
import {
	applyPattern,
	deleteCell,
	deriveLines,
	detectCells,
	dragLine,
	emptySlice,
	gridCells,
	mergeCells,
	moveLine,
	numbering,
	outputNames,
	outputSize,
	record,
	redoSlice,
	renameCell,
	reorderKept,
	sanitizeName,
	sliceRects,
	sliceStepParams,
	sliceSummary,
	snapBand,
	splitCell,
	toggleSkip,
	undoSlice,
	type Cell
} from './model';
import { activeSlice, canMoveWithSlice, sliceLast } from './stack';

function sheet(): Analysis {
	const w = 300;
	const h = 200;
	const data = new Uint8Array(w * h * 4);
	for (let p = 0; p < w * h; p++) data.set([235, 230, 220, 255], p * 4);
	const panel = (x0: number, y0: number, x1: number, y1: number) => {
		for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) data.set([40, 90, 140, 255], (y * w + x) * 4);
	};
	panel(10, 10, 95, 95);
	panel(105, 10, 190, 95);
	panel(204, 10, 290, 95);
	panel(10, 105, 190, 190);
	panel(204, 105, 290, 190);
	return analysePixels(data, w, h);
}

const cell = (id: string, x0: number, y0: number, x1: number, y1: number, extra: Partial<Cell> = {}): Cell => ({ id, x0, y0, x1, y1, ...extra });

describe('grid and lines', () => {
	const an = sheet();

	it('drops a grid on the content box and snaps each line into its gutter', () => {
		const cells = gridCells(an, 3, 2);
		expect(cells).toHaveLength(6);
		expect(cells[0].x0).toBeCloseTo(10 / 300, 3);
		expect(cells[0].x1).toBeCloseTo(95 / 300, 3);
		expect(cells[1].x0).toBeCloseTo(105 / 300, 3);
		expect(cells[0].y1).toBeCloseTo(95 / 200, 3);
		expect(cells[3].y0).toBeCloseTo(105 / 200, 3);
	});

	it('leaves lines at equal spacing when snapping is off', () => {
		const cells = gridCells(an, 2, 1, false);
		expect(cells[0].x1).toBeCloseTo(cells[1].x0, 6);
		expect(cells[0].x1).toBeCloseTo((10 + 280 / 2) / 300, 3);
	});

	it('derives one line per shared edge, each draggable on its own', () => {
		const lines = deriveLines(gridCells(an, 3, 2), an);
		expect(lines.filter((line) => line.axis === 'v')).toHaveLength(2);
		expect(lines.filter((line) => line.axis === 'h')).toHaveLength(1);
		const vertical = lines.find((line) => line.axis === 'v')!;
		expect(vertical.pairs).toHaveLength(2);
		expect(vertical.snapped).toBe(true);
	});

	it('marks a segment that crosses content as cutting a panel', () => {
		const lines = deriveLines(gridCells(an, 3, 2), an);
		const first = lines.filter((line) => line.axis === 'v').sort((a, b) => a.a - b.a)[0];
		expect(first.segments.map((segment) => segment.clean)).toEqual([true, false]);
	});

	it('moves only the cells on the dragged line', () => {
		const cells = gridCells(an, 3, 2);
		const lines = deriveLines(cells, an);
		const second = lines.filter((line) => line.axis === 'v').sort((a, b) => a.a - b.a)[1];
		const moved = moveLine(cells, second, 0.6, 0.62);
		expect(moved[1].x1).toBe(0.6);
		expect(moved[2].x0).toBe(0.62);
		expect(moved[0]).toEqual(cells[0]);
		expect(moved[1].x0).toBe(cells[1].x0);
	});

	it('snaps a drag to the nearest gutter band and frees it with snapping off', () => {
		const cells = gridCells(an, 3, 2, false);
		const line = deriveLines(cells, an).filter((l) => l.axis === 'v').sort((a, b) => a.a - b.a)[1];
		const snapped = dragLine(an, cells, line, 0.66, 0.05, true);
		expect(snapped.snapped).toBe(true);
		expect(snapped.a).toBeCloseTo(190 / 300, 3);
		expect(snapped.b).toBeCloseTo(204 / 300, 3);
		const free = dragLine(an, cells, line, 0.66, 0.05, false);
		expect(free.snapped).toBe(false);
		expect(free.a).toBe(0.66);
	});

	it('does not snap beyond the radius', () => {
		expect(snapBand(an, 'v', 0.5, [{ lo: 0, hi: 1 }], 0.01)).toBeNull();
		expect(snapBand(an, 'h', 0.5, [{ lo: 0, hi: 1 }], 0.05)).not.toBeNull();
	});

	it('reports detection against an existing grid', () => {
		const result = detectCells(an, gridCells(an, 3, 2));
		expect(result.cells).toHaveLength(5);
		expect(result.cells.filter((c) => c.merged)).toHaveLength(1);
		expect(result.summary).toMatch(/^5 panels found\. 04 spans two grid cells\.$/);
	});
});

describe('cell edits', () => {
	const cells = [cell('a', 0, 0, 0.5, 0.5), cell('b', 0.5, 0, 1, 0.5), cell('c', 0, 0.5, 1, 1)];

	it('merges the selection into its bounding box under the first number', () => {
		const merged = mergeCells(cells, ['b', 'a']);
		expect(merged.map((c) => c.id)).toEqual(['a', 'c']);
		expect(merged[0]).toMatchObject({ x0: 0, x1: 1, y0: 0, y1: 0.5, merged: true });
		expect(mergeCells(cells, ['a'])).toEqual(cells);
	});

	it('splits along the long side in the middle without an analysis', () => {
		const split = splitCell(cells, 'c', null);
		expect(split).toHaveLength(4);
		expect(split[2]).toMatchObject({ x0: 0, x1: 0.5, y0: 0.5, y1: 1 });
		expect(split[3]).toMatchObject({ x0: 0.5, x1: 1 });
	});

	it('splits at the inner gutter when there is one', () => {
		const an = sheet();
		const split = splitCell([cell('row', 0, 0, 1, 0.5)], 'row', an);
		expect(split).toHaveLength(2);
		expect(split[0].x1).toBeCloseTo(190 / 300, 2);
		expect(split[1].x0).toBeCloseTo(204 / 300, 2);
	});

	it('excludes a cell from numbering and output, and keeps it again', () => {
		const skipped = toggleSkip(cells, 'b');
		expect([...numbering(skipped).entries()]).toEqual([
			['a', '01'],
			['c', '02']
		]);
		expect(numbering(toggleSkip(skipped, 'b')).get('b')).toBe('02');
	});

	it('deletes and reorders kept cells, skipped ones trail', () => {
		expect(deleteCell(cells, 'a').map((c) => c.id)).toEqual(['b', 'c']);
		const list = toggleSkip(cells, 'a');
		expect(reorderKept(list, 'c', 0).map((c) => c.id)).toEqual(['c', 'b', 'a']);
	});
});

describe('naming', () => {
	it('fills the pattern tokens', () => {
		expect(applyPattern('{sheet}-{n}', { sheet: 'mira', n: 3, row: 1, col: 3 })).toBe('mira-03');
		expect(applyPattern('{sheet}_r{row}c{col}', { sheet: 'mira', n: 4, row: 2, col: 1 })).toBe('mira_r2c1');
		expect(applyPattern('', { sheet: 'mira', n: 1, row: 1, col: 1 })).toBe('mira-01');
	});

	it('strips separators and control characters', () => {
		expect(sanitizeName(' a/b\\c\u0007d ')).toBe('abcd');
		expect(sanitizeName('x'.repeat(200))).toHaveLength(120);
	});

	it('numbers kept cells and keeps typed names', () => {
		const cells = renameCell([cell('a', 0, 0, 0.5, 0.5), cell('b', 0.5, 0, 1, 0.5, { skip: true }), cell('c', 0, 0.6, 1, 1)], 'c', 'hero');
		const names = outputNames(cells, '{sheet}-{row}-{col}', 'mira');
		expect([...names.entries()]).toEqual([
			['a', 'mira-1-1'],
			['c', 'hero']
		]);
		expect(renameCell(cells, 'c', '  ').find((c) => c.id === 'c')?.name).toBeUndefined();
	});
});

describe('output sizes', () => {
	it('pads and applies the aspect without cropping', () => {
		const c = cell('a', 0, 0, 0.5, 0.25);
		const source = { width: 400, height: 400 };
		expect(outputSize(null, c, { trim: false, tolerance: 12, padding: 10, aspect: 'keep' }, source)).toMatchObject({ width: 220, height: 120 });
		expect(outputSize(null, c, { trim: false, tolerance: 12, padding: 0, aspect: 'square' }, source)).toMatchObject({ width: 200, height: 200 });
		expect(outputSize(null, c, { trim: false, tolerance: 12, padding: 0, aspect: 'portrait' }, source)).toMatchObject({ width: 200, height: 300 });
	});
});

describe('steps payload', () => {
	const params = {
		...emptySlice('mira-sheet'),
		cells: [cell('a', 0.0123456789, 0, 0.5, 0.5), cell('b', 0.5, 0, 1, 0.5, { skip: true }), cell('c', 0, 0.5, 1.0000001, 1, { name: 'wide' })],
		output: { trim: true, tolerance: 20, padding: 8, aspect: 'square' as const }
	};

	it('sends kept rects as fractions in output order with names', () => {
		expect(sliceRects(params)).toEqual([
			{ x: 0.012346, y: 0, w: 0.487654, h: 0.5, name: 'mira-sheet-01' },
			{ x: 0, y: 0.5, w: 1, h: 0.5, name: 'wide' }
		]);
		expect(sliceStepParams(params)).toMatchObject({ trim: true, tolerance: 20, padding: 8, aspect: 'square', fill: 'auto' });
	});

	it('keeps Slice the last step when another edit lands after it', () => {
		let stack: EditStackState<unknown> = createStack();
		stack = apply(beginEdit(stack, sliceDraft(params)));
		stack = sliceLast(apply(beginEdit(stack, resizeDraft({ width: 300, height: 200, lock: true }))));
		expect(stack.steps.map((s) => s.tool)).toEqual(['resize', 'slice']);
		const payload = runSteps(stack.steps, { source: { width: 600, height: 400 } });
		expect(payload.map((s) => s.tool)).toEqual(['resize', 'slice']);
		expect(payload[1].params.rects).toHaveLength(2);
	});

	it('forbids moving Slice or moving a step below it', () => {
		let stack: EditStackState<unknown> = createStack();
		stack = apply(beginEdit(stack, cropDraft({ plan: { rect: { x: 0, y: 0, width: 1, height: 1 }, rotation: 0, flipHorizontal: false, flipVertical: false, targetLongestSide: null }, aspect: 'free' } as never, 'Free', { width: 10, height: 10 })));
		stack = apply(beginEdit(stack, sliceDraft(params)));
		expect(canMoveWithSlice(stack, stack.steps[1].id, 0)).toBe(false);
		expect(canMoveWithSlice(stack, stack.steps[0].id, 1)).toBe(false);
		expect(activeSlice(stack)?.pending).toBe(false);
	});

	it('summarises the slice for the stack row', () => {
		expect(sliceSummary(params)).toBe('3 cells · 2 kept');
		expect(sliceSummary({ ...params, cells: params.cells.map((c) => ({ ...c, skip: false })), layout: { ...params.layout, preset: '3x1', cols: 3, rows: 1 } })).toBe('3×1 grid · 3 cells');
	});
});

describe('slice history', () => {
	it('undoes and redoes snapshots in order', () => {
		const a = emptySlice('s');
		const b = { ...a, pattern: 'b' };
		const c = { ...a, pattern: 'c' };
		let history = record(record({ past: [], future: [], labels: [], futureLabels: [] }, a, 'preset'), b, 'merge');
		const back = undoSlice(history, c)!;
		expect(back.params.pattern).toBe('b');
		history = back.history;
		const forward = redoSlice(history, back.params)!;
		expect(forward.params.pattern).toBe('c');
		expect(undoSlice({ past: [], future: [], labels: [], futureLabels: [] }, a)).toBeNull();
	});
});
