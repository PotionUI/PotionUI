import { bands, bgFraction, CLEAN_SEGMENT, contentBox, detectPanels, orderRows, union, type Analysis, type Band, type Rect } from './detect';

export interface Cell extends Rect {
	id: string;
	skip?: boolean;
	merged?: boolean;
	name?: string;
}

export interface Segment {
	lo: number;
	hi: number;
	clean: boolean;
}

export type Axis = 'v' | 'h';

export interface Line {
	id: string;
	axis: Axis;
	a: number;
	b: number;
	lo: number;
	hi: number;
	pairs: [string, string][];
	segments: Segment[];
	snapped: boolean;
}

export type SliceAspect = 'keep' | 'square' | 'portrait';

export interface SliceOutput {
	trim: boolean;
	tolerance: number;
	padding: number;
	aspect: SliceAspect;
}

export interface SliceLayout {
	preset: string | null;
	cols: number;
	rows: number;
	snap: boolean;
	detected: string | null;
}

export interface SliceParams {
	sheet: string;
	cells: Cell[];
	layout: SliceLayout;
	output: SliceOutput;
	pattern: string;
	collection: string | null;
}

export const GRID_PRESETS = [
	{ key: '2x1', cols: 2, rows: 1 },
	{ key: '2x2', cols: 2, rows: 2 },
	{ key: '3x2', cols: 3, rows: 2 },
	{ key: '3x3', cols: 3, rows: 3 },
	{ key: '4x4', cols: 4, rows: 4 }
] as const;

export const MAX_GRID = 12;
export const MAX_CELLS = 64;
export const LINE_GAP = 0.06;
export const DEFAULT_PATTERN = '{sheet}-{n}';
export const PATTERN_TOKENS = ['{sheet}', '{n}', '{row}', '{col}'] as const;

export const DEFAULT_OUTPUT: SliceOutput = { trim: false, tolerance: 12, padding: 0, aspect: 'keep' };

export function emptySlice(sheet = 'sheet'): SliceParams {
	return {
		sheet,
		cells: [],
		layout: { preset: null, cols: 3, rows: 2, snap: true, detected: null },
		output: { ...DEFAULT_OUTPUT },
		pattern: DEFAULT_PATTERN,
		collection: null
	};
}

const key = (n: number) => Math.round(n * 4000);

const bandRect = (axis: Axis, a: number, b: number, lo: number, hi: number): Rect =>
	axis === 'v' ? { x0: a, x1: Math.max(b, a + 0.001), y0: lo, y1: hi } : { y0: a, y1: Math.max(b, a + 0.001), x0: lo, x1: hi };

export function deriveLines(cells: readonly Cell[], an: Analysis | null): Line[] {
	const groups = new Map<string, Line>();
	const add = (axis: Axis, l: Cell, r: Cell) => {
		const a = axis === 'v' ? l.x1 : l.y1;
		const b = axis === 'v' ? r.x0 : r.y0;
		const lo = axis === 'v' ? Math.max(l.y0, r.y0) : Math.max(l.x0, r.x0);
		const hi = axis === 'v' ? Math.min(l.y1, r.y1) : Math.min(l.x1, r.x1);
		const id = `${axis}:${key(a)}:${key(b)}`;
		const clean = an ? bgFraction(an, bandRect(axis, a, b, lo, hi)) >= CLEAN_SEGMENT : true;
		const line = groups.get(id) ?? { id, axis, a, b, lo, hi, pairs: [], segments: [], snapped: b - a > 0.002 };
		line.lo = Math.min(line.lo, lo);
		line.hi = Math.max(line.hi, hi);
		line.pairs.push([l.id, r.id]);
		line.segments.push({ lo, hi, clean });
		groups.set(id, line);
	};
	for (const l of cells) {
		for (const r of cells) {
			if (l === r) continue;
			const gx = r.x0 - l.x1;
			const oy = Math.min(l.y1, r.y1) - Math.max(l.y0, r.y0);
			if (gx >= -0.0005 && gx <= LINE_GAP && oy > 0.3 * Math.min(l.y1 - l.y0, r.y1 - r.y0)) add('v', l, r);
			const gy = r.y0 - l.y1;
			const ox = Math.min(l.x1, r.x1) - Math.max(l.x0, r.x0);
			if (gy >= -0.0005 && gy <= LINE_GAP && ox > 0.3 * Math.min(l.x1 - l.x0, r.x1 - r.x0)) add('h', l, r);
		}
	}
	return [...groups.values()];
}

export function snapBand(an: Analysis, axis: Axis, pos: number, spans: readonly { lo: number; hi: number }[], radius: number): Band | null {
	let best: Band | null = null;
	let bestDist = radius;
	let bestClean = -1;
	for (const span of spans) {
		const within: Rect = axis === 'v' ? { x0: 0, x1: 1, y0: span.lo, y1: span.hi } : { y0: 0, y1: 1, x0: span.lo, x1: span.hi };
		for (const band of bands(an, axis === 'v' ? 'x' : 'y', within)) {
			if (band.edge) continue;
			const dist = Math.abs((band.a + band.b) / 2 - pos);
			if (dist > radius) continue;
			const cleanAll = spans.filter((s) => bgFraction(an, bandRect(axis, band.a, band.b, s.lo, s.hi)) >= CLEAN_SEGMENT).length;
			if (cleanAll > bestClean || (cleanAll === bestClean && dist < bestDist)) {
				best = { a: band.a, b: band.b };
				bestDist = dist;
				bestClean = cleanAll;
			}
		}
	}
	return best;
}

export function gridCells(an: Analysis | null, cols: number, rows: number, snap = true): Cell[] {
	const outer = (an && contentBox(an)) ?? { x0: 0, y0: 0, x1: 1, y1: 1 };
	const cw = (outer.x1 - outer.x0) / cols;
	const rh = (outer.y1 - outer.y0) / rows;
	const ys: Band[] = [];
	for (let r = 1; r < rows; r++) {
		const pos = outer.y0 + r * rh;
		const band = snap && an ? snapBand(an, 'h', pos, [{ lo: outer.x0, hi: outer.x1 }], rh * 0.35) : null;
		ys.push(band ?? { a: pos, b: pos });
	}
	const rowEdges = [outer.y0, ...ys.flatMap((b) => [b.a, b.b]), outer.y1];
	const rowSpans = Array.from({ length: rows }, (_, r) => ({ lo: rowEdges[r * 2], hi: rowEdges[r * 2 + 1] }));
	const xs: Band[] = [];
	for (let c = 1; c < cols; c++) {
		const pos = outer.x0 + c * cw;
		const band = snap && an ? snapBand(an, 'v', pos, rowSpans, cw * 0.35) : null;
		xs.push(band ?? { a: pos, b: pos });
	}
	const colEdges = [outer.x0, ...xs.flatMap((b) => [b.a, b.b]), outer.x1];
	const cells: Cell[] = [];
	for (let r = 0; r < rows; r++) {
		for (let c = 0; c < cols; c++) {
			cells.push({ id: `g${r}-${c}`, x0: colEdges[c * 2], x1: colEdges[c * 2 + 1], y0: rowEdges[r * 2], y1: rowEdges[r * 2 + 1] });
		}
	}
	return cells;
}

const contains = (outer: Rect, inner: Rect, slack = 0.01) =>
	inner.x0 >= outer.x0 - slack && inner.x1 <= outer.x1 + slack && inner.y0 >= outer.y0 - slack && inner.y1 <= outer.y1 + slack;

export interface DetectResult {
	cells: Cell[];
	summary: string;
}

export function detectCells(an: Analysis, previous: readonly Cell[] = []): DetectResult {
	const panels = detectPanels(an);
	const grid = previous.filter((cell) => !cell.skip);
	const cells: Cell[] = panels.map((rect, i) => {
		const spans = grid.filter((cell) => {
			const shared = Math.max(0, Math.min(cell.x1, rect.x1) - Math.max(cell.x0, rect.x0)) * Math.max(0, Math.min(cell.y1, rect.y1) - Math.max(cell.y0, rect.y0));
			return shared > 0.5 * (cell.x1 - cell.x0) * (cell.y1 - cell.y0);
		}).length;
		return { id: `d${i}`, ...rect, ...(spans > 1 ? { merged: true } : {}) };
	});
	const count = `${cells.length} ${cells.length === 1 ? 'panel' : 'panels'} found`;
	if (!grid.length) return { cells, summary: `${count}.` };
	const notes: string[] = [];
	const numbers = new Map(cells.map((cell, i) => [cell.id, String(i + 1).padStart(2, '0')]));
	for (const cell of cells) if (cell.merged) notes.push(`${numbers.get(cell.id)} spans two grid cells`);
	const splitCells = grid.filter((g) => cells.filter((cell) => contains(g, cell)).length > 1).length;
	if (splitCells) notes.push(splitCells === 1 ? 'one grid cell holds two panels' : `${splitCells} grid cells hold more than one panel`);
	return { cells, summary: notes.length ? `${count}. ${notes.join('; ')}.` : `${count}.` };
}

export function moveLine(cells: readonly Cell[], line: Pick<Line, 'axis' | 'pairs'>, a: number, b: number): Cell[] {
	const left = new Set(line.pairs.map((p) => p[0]));
	const right = new Set(line.pairs.map((p) => p[1]));
	const lo = Math.min(a, b);
	const hi = Math.max(a, b);
	return cells.map((cell) => {
		let next = cell;
		if (left.has(cell.id)) next = line.axis === 'v' ? { ...next, x1: Math.max(next.x0 + 0.002, lo) } : { ...next, y1: Math.max(next.y0 + 0.002, lo) };
		if (right.has(cell.id)) next = line.axis === 'v' ? { ...next, x0: Math.min(next.x1 - 0.002, hi) } : { ...next, y0: Math.min(next.y1 - 0.002, hi) };
		return next;
	});
}

export function dragLine(an: Analysis | null, cells: readonly Cell[], line: Line, pos: number, radius: number, snap: boolean): { cells: Cell[]; a: number; b: number; snapped: boolean } {
	const spans = line.segments.map((s) => ({ lo: s.lo, hi: s.hi }));
	const band = snap && an ? snapBand(an, line.axis, pos, spans, radius) : null;
	const a = band ? band.a : pos;
	const b = band ? band.b : pos;
	return { cells: moveLine(cells, line, a, b), a, b, snapped: !!band };
}

export function lineKey(axis: Axis, a: number, b: number): string {
	return `${axis}:${key(a)}:${key(b)}`;
}

let seq = 0;

export function freshId(prefix: string): string {
	seq += 1;
	return `${prefix}${Date.now().toString(36)}${seq}`;
}

export function mergeCells(cells: readonly Cell[], ids: readonly string[]): Cell[] {
	const chosen = cells.filter((cell) => ids.includes(cell.id));
	if (chosen.length < 2) return cells.slice();
	const lead = cells.find((cell) => ids.includes(cell.id))!;
	const box = chosen.reduce<Rect>((acc, cell) => union(acc, cell), lead);
	const merged: Cell = { ...lead, ...box, merged: true, skip: false };
	return cells.filter((cell) => cell.id === lead.id || !ids.includes(cell.id)).map((cell) => (cell.id === lead.id ? merged : cell));
}

export function splitCell(cells: readonly Cell[], id: string, an: Analysis | null): Cell[] {
	const cell = cells.find((candidate) => candidate.id === id);
	if (!cell) return cells.slice();
	const wide = (cell.x1 - cell.x0) * (an?.w ?? 1) >= (cell.y1 - cell.y0) * (an?.h ?? 1);
	const lo = wide ? cell.x0 : cell.y0;
	const hi = wide ? cell.x1 : cell.y1;
	const mid = (lo + hi) / 2;
	let cut: Band = { a: mid, b: mid };
	if (an) {
		const inner = bands(an, wide ? 'x' : 'y', cell).filter((band) => !band.edge);
		if (inner.length) cut = inner.reduce((best, band) => (Math.abs((band.a + band.b) / 2 - mid) < Math.abs((best.a + best.b) / 2 - mid) ? band : best));
	}
	const { merged: _merged, name: _name, ...base } = cell;
	const first: Cell = { ...base, id: freshId(`${cell.id}a`), ...(wide ? { x1: cut.a } : { y1: cut.a }) };
	const second: Cell = { ...base, id: freshId(`${cell.id}b`), ...(wide ? { x0: cut.b } : { y0: cut.b }) };
	return cells.flatMap((candidate) => (candidate.id === id ? [first, second] : [candidate]));
}

export function toggleSkip(cells: readonly Cell[], id: string): Cell[] {
	return cells.map((cell) => (cell.id === id ? { ...cell, skip: !cell.skip } : cell));
}

export function deleteCell(cells: readonly Cell[], id: string): Cell[] {
	return cells.filter((cell) => cell.id !== id);
}

export function addCell(cells: readonly Cell[], rect: Rect): Cell[] {
	const clamp = (n: number) => Math.min(1, Math.max(0, n));
	const box = { x0: clamp(Math.min(rect.x0, rect.x1)), y0: clamp(Math.min(rect.y0, rect.y1)), x1: clamp(Math.max(rect.x0, rect.x1)), y1: clamp(Math.max(rect.y0, rect.y1)) };
	if (box.x1 - box.x0 < 0.005 || box.y1 - box.y0 < 0.005 || cells.length >= MAX_CELLS) return cells.slice();
	return [...cells, { id: freshId('b'), ...box }];
}

export function resizeCell(cells: readonly Cell[], id: string, rect: Rect): Cell[] {
	return cells.map((cell) => (cell.id === id ? { ...cell, ...rect } : cell));
}

export function renameCell(cells: readonly Cell[], id: string, name: string): Cell[] {
	return cells.map((cell) => {
		if (cell.id !== id) return cell;
		const { name: _old, ...rest } = cell;
		return name.trim() ? { ...rest, name } : rest;
	});
}

export function reorderKept(cells: readonly Cell[], id: string, to: number): Cell[] {
	const kept = cells.filter((cell) => !cell.skip);
	const from = kept.findIndex((cell) => cell.id === id);
	if (from < 0) return cells.slice();
	const target = Math.max(0, Math.min(kept.length - 1, to));
	const next = kept.slice();
	const [moved] = next.splice(from, 1);
	next.splice(target, 0, moved);
	return [...next, ...cells.filter((cell) => cell.skip)];
}

export function kept(cells: readonly Cell[]): Cell[] {
	return cells.filter((cell) => !cell.skip);
}

export function numbering(cells: readonly Cell[]): Map<string, string> {
	return new Map(kept(cells).map((cell, i) => [cell.id, String(i + 1).padStart(2, '0')]));
}

export function gridPositions(cells: readonly Cell[]): Map<string, { row: number; col: number }> {
	const ordered = orderRows(kept(cells));
	const out = new Map<string, { row: number; col: number }>();
	let row = 0;
	let col = 0;
	let top = ordered[0]?.y0 ?? 0;
	for (const cell of ordered) {
		if (Math.abs(cell.y0 - top) > 0.04) {
			row += 1;
			col = 0;
			top = cell.y0;
		}
		col += 1;
		out.set(cell.id, { row: row + 1, col });
	}
	return out;
}

export function applyPattern(pattern: string, values: { sheet: string; n: number; row: number; col: number }): string {
	const filled = (pattern || DEFAULT_PATTERN)
		.replaceAll('{sheet}', values.sheet)
		.replaceAll('{n}', String(values.n).padStart(2, '0'))
		.replaceAll('{row}', String(values.row))
		.replaceAll('{col}', String(values.col));
	return sanitizeName(filled) || `${values.sheet}-${String(values.n).padStart(2, '0')}`;
}

export function sanitizeName(name: string): string {
	let out = '';
	for (const ch of name) {
		const code = ch.charCodeAt(0);
		if (code < 32 || code === 127 || ch === '/' || ch === '\\') continue;
		out += ch;
	}
	return out.trim().slice(0, 120);
}

export function outputNames(cells: readonly Cell[], pattern: string, sheet: string): Map<string, string> {
	const positions = gridPositions(cells);
	const names = new Map<string, string>();
	kept(cells).forEach((cell, i) => {
		const pos = positions.get(cell.id) ?? { row: 1, col: i + 1 };
		names.set(cell.id, cell.name ? sanitizeName(cell.name) : applyPattern(pattern, { sheet, n: i + 1, row: pos.row, col: pos.col }));
	});
	return names;
}

export function outputRect(an: Analysis | null, cell: Rect, output: SliceOutput): Rect {
	if (!output.trim || !an) return cell;
	return contentBox(an, cell) ?? cell;
}

export function outputSize(an: Analysis | null, cell: Rect, output: SliceOutput, source: { width: number; height: number }): { width: number; height: number; rect: Rect } {
	const rect = outputRect(an, cell, output);
	let width = Math.max(1, Math.round((rect.x1 - rect.x0) * source.width)) + output.padding * 2;
	let height = Math.max(1, Math.round((rect.y1 - rect.y0) * source.height)) + output.padding * 2;
	if (output.aspect === 'square') width = height = Math.max(width, height);
	if (output.aspect === 'portrait') {
		if (width * 3 > height * 2) height = Math.round((width * 3) / 2);
		else width = Math.round((height * 2) / 3);
	}
	return { width, height, rect };
}

export function hexColor(rgb: readonly number[]): string {
	return rgb
		.slice(0, 3)
		.map((value) => Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, '0'))
		.join('')
		.toUpperCase();
}

export function sliceSummary(params: SliceParams): string {
	const count = params.cells.length;
	const keptCount = kept(params.cells).length;
	if (params.layout.preset && keptCount === count) return `${params.layout.cols}×${params.layout.rows} grid · ${count} ${count === 1 ? 'cell' : 'cells'}`;
	return `${count} ${count === 1 ? 'cell' : 'cells'} · ${keptCount} kept`;
}

const round = (n: number) => Math.round(n * 1e6) / 1e6;

export interface SliceRect {
	x: number;
	y: number;
	w: number;
	h: number;
	name?: string;
}

export function sliceRects(params: SliceParams): SliceRect[] {
	const names = outputNames(params.cells, params.pattern, params.sheet);
	return kept(params.cells).map((cell) => {
		const x = round(Math.max(0, cell.x0));
		const y = round(Math.max(0, cell.y0));
		const rect: SliceRect = { x, y, w: round(Math.min(1, cell.x1) - x), h: round(Math.min(1, cell.y1) - y) };
		const name = names.get(cell.id);
		if (name) rect.name = name;
		return rect;
	});
}

export function sliceStepParams(params: SliceParams): Record<string, unknown> {
	return {
		rects: sliceRects(params),
		trim: params.output.trim,
		tolerance: params.output.tolerance,
		padding: params.output.padding,
		aspect: params.output.aspect,
		fill: 'auto'
	};
}

export function maxOverlap(cells: readonly Cell[]): number {
	let worst = 0;
	const list = kept(cells);
	for (let i = 0; i < list.length; i++) {
		for (let j = i + 1; j < list.length; j++) {
			const a = list[i];
			const b = list[j];
			const shared = Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
			const smaller = Math.min((a.x1 - a.x0) * (a.y1 - a.y0), (b.x1 - b.x0) * (b.y1 - b.y0));
			if (smaller > 0) worst = Math.max(worst, shared / smaller);
		}
	}
	return worst;
}

export interface SliceHistory {
	past: SliceParams[];
	future: SliceParams[];
	labels: string[];
	futureLabels: string[];
}

export function emptyHistory(): SliceHistory {
	return { past: [], future: [], labels: [], futureLabels: [] };
}

export function record(history: SliceHistory, before: SliceParams, label: string): SliceHistory {
	return { past: [...history.past, before].slice(-100), labels: [...history.labels, label].slice(-100), future: [], futureLabels: [] };
}

export function undoSlice(history: SliceHistory, current: SliceParams): { history: SliceHistory; params: SliceParams } | null {
	if (!history.past.length) return null;
	const params = history.past[history.past.length - 1];
	const label = history.labels[history.labels.length - 1] ?? '';
	return {
		params,
		history: { past: history.past.slice(0, -1), labels: history.labels.slice(0, -1), future: [current, ...history.future], futureLabels: [label, ...history.futureLabels] }
	};
}

export function redoSlice(history: SliceHistory, current: SliceParams): { history: SliceHistory; params: SliceParams } | null {
	if (!history.future.length) return null;
	const [params, ...future] = history.future;
	const [label, ...futureLabels] = history.futureLabels;
	return { params, history: { past: [...history.past, current], labels: [...history.labels, label ?? ''], future, futureLabels } };
}
