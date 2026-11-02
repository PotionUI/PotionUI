export interface Rect {
	x0: number;
	y0: number;
	x1: number;
	y1: number;
}

export interface Band {
	a: number;
	b: number;
}

export interface EdgeBand extends Band {
	edge: boolean;
}

export interface Analysis {
	w: number;
	h: number;
	bg: [number, number, number];
	transparent: boolean;
	isBg: Uint8Array;
	minGutter: number;
}

export const ANALYSIS_SIDE = 768;
export const DEFAULT_TOLERANCE = 12;
export const CLEAN_BAND = 0.985;
export const CLEAN_SEGMENT = 0.97;
export const MIN_PANEL_AREA = 0.012;

const FULL: Rect = { x0: 0, y0: 0, x1: 1, y1: 1 };

function median(values: number[]): number {
	if (!values.length) return 0;
	const sorted = values.slice().sort((a, b) => a - b);
	return sorted[sorted.length >> 1];
}

export function analysePixels(data: ArrayLike<number>, w: number, h: number, tolerance = DEFAULT_TOLERANCE): Analysis {
	const ring: [number[], number[], number[], number[]] = [[], [], [], []];
	const edge = Math.max(2, Math.round(Math.min(w, h) * 0.01));
	for (let y = 0; y < h; y++) {
		for (let x = 0; x < w; x++) {
			if (x >= edge && x < w - edge && y >= edge && y < h - edge) continue;
			const i = (y * w + x) * 4;
			ring[0].push(data[i]);
			ring[1].push(data[i + 1]);
			ring[2].push(data[i + 2]);
			ring[3].push(data[i + 3]);
		}
	}
	const bg: [number, number, number] = [median(ring[0]), median(ring[1]), median(ring[2])];
	const transparent = median(ring[3]) < 16;
	const isBg = new Uint8Array(w * h);
	for (let p = 0; p < w * h; p++) {
		const i = p * 4;
		if (data[i + 3] < 16) {
			isBg[p] = 1;
			continue;
		}
		if (transparent) continue;
		const d = Math.max(Math.abs(data[i] - bg[0]), Math.abs(data[i + 1] - bg[1]), Math.abs(data[i + 2] - bg[2]));
		isBg[p] = d <= tolerance ? 1 : 0;
	}
	return { w, h, bg, transparent, isBg, minGutter: Math.max(3, Math.round(Math.max(w, h) * 0.006)) };
}

export function analyseImage(image: CanvasImageSource & { width: number; height: number }, tolerance = DEFAULT_TOLERANCE, maxSide = ANALYSIS_SIDE): Analysis {
	const scale = Math.min(1, maxSide / Math.max(image.width, image.height, 1));
	const w = Math.max(1, Math.round(image.width * scale));
	const h = Math.max(1, Math.round(image.height * scale));
	const canvas = document.createElement('canvas');
	canvas.width = w;
	canvas.height = h;
	const context = canvas.getContext('2d', { willReadFrequently: true });
	if (!context) throw new Error('This browser could not open a drawing surface.');
	context.drawImage(image, 0, 0, w, h);
	return analysePixels(context.getImageData(0, 0, w, h).data, w, h, tolerance);
}

function px(an: Analysis, r: Rect) {
	return {
		x0: Math.max(0, Math.floor(r.x0 * an.w + 1e-6)),
		y0: Math.max(0, Math.floor(r.y0 * an.h + 1e-6)),
		x1: Math.min(an.w, Math.ceil(r.x1 * an.w - 1e-6)),
		y1: Math.min(an.h, Math.ceil(r.y1 * an.h - 1e-6))
	};
}

export function bgFraction(an: Analysis, r: Rect): number {
	const p = px(an, r);
	const y1 = Math.min(an.h, Math.max(p.y1, p.y0 + 1));
	const x1 = Math.min(an.w, Math.max(p.x1, p.x0 + 1));
	let bg = 0;
	let all = 0;
	for (let y = p.y0; y < y1; y++) {
		for (let x = p.x0; x < x1; x++) {
			all++;
			bg += an.isBg[y * an.w + x];
		}
	}
	return all ? bg / all : 1;
}

export function bands(an: Analysis, axis: 'x' | 'y', within: Rect, clean = CLEAN_BAND): EdgeBand[] {
	const p = px(an, within);
	const out: EdgeBand[] = [];
	const [lo, hi, size] = axis === 'x' ? [p.x0, p.x1, an.w] : [p.y0, p.y1, an.h];
	let start = -1;
	for (let t = lo; t <= hi; t++) {
		let gutter = false;
		if (t < hi) {
			let bg = 0;
			let all = 0;
			if (axis === 'x') for (let y = p.y0; y < p.y1; y++, all++) bg += an.isBg[y * an.w + t];
			else for (let x = p.x0; x < p.x1; x++, all++) bg += an.isBg[t * an.w + x];
			gutter = all > 0 && bg / all >= clean;
		}
		if (gutter && start < 0) start = t;
		if (!gutter && start >= 0) {
			const edge = start === lo || t === hi;
			if (t - start >= an.minGutter || edge) out.push({ a: start / size, b: t / size, edge });
			start = -1;
		}
	}
	return out;
}

export function contentBox(an: Analysis, within: Rect = FULL): Rect | null {
	const p = px(an, within);
	let x0 = Infinity;
	let y0 = Infinity;
	let x1 = -1;
	let y1 = -1;
	for (let y = p.y0; y < p.y1; y++) {
		for (let x = p.x0; x < p.x1; x++) {
			if (an.isBg[y * an.w + x]) continue;
			if (x < x0) x0 = x;
			if (x > x1) x1 = x;
			if (y < y0) y0 = y;
			if (y > y1) y1 = y;
		}
	}
	if (x1 < 0) return null;
	return { x0: x0 / an.w, y0: y0 / an.h, x1: (x1 + 1) / an.w, y1: (y1 + 1) / an.h };
}

export const area = (r: Rect) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);

export const union = (a: Rect, b: Rect): Rect => ({
	x0: Math.min(a.x0, b.x0),
	y0: Math.min(a.y0, b.y0),
	x1: Math.max(a.x1, b.x1),
	y1: Math.max(a.y1, b.y1)
});

export function xyCut(an: Analysis, within: Rect = FULL, depth = 0): Rect[] {
	const box = contentBox(an, within);
	if (!box) return [];
	if (depth > 6) return [box];
	for (const axis of ['y', 'x'] as const) {
		const inner = bands(an, axis, box).filter((band) => !band.edge);
		if (!inner.length) continue;
		const lo = axis === 'x' ? box.x0 : box.y0;
		const hi = axis === 'x' ? box.x1 : box.y1;
		const cuts = [lo, ...inner.flatMap((band) => [band.a, band.b]), hi];
		const out: Rect[] = [];
		for (let i = 0; i < cuts.length; i += 2) {
			const piece = axis === 'x' ? { ...box, x0: cuts[i], x1: cuts[i + 1] } : { ...box, y0: cuts[i], y1: cuts[i + 1] };
			out.push(...xyCut(an, piece, depth + 1));
		}
		return out;
	}
	return [box];
}

function gap(a: Rect, b: Rect, an: Analysis): { x: number; y: number } {
	return {
		x: Math.max(0, Math.max(a.x0, b.x0) - Math.min(a.x1, b.x1)) * an.w,
		y: Math.max(0, Math.max(a.y0, b.y0) - Math.min(a.y1, b.y1)) * an.h
	};
}

function overlapShare(a0: number, a1: number, b0: number, b1: number): number {
	const shared = Math.min(a1, b1) - Math.max(a0, b0);
	return shared <= 0 ? 0 : shared / Math.max(1e-9, Math.min(a1 - a0, b1 - b0));
}

export function orderRows<T extends Rect>(rects: readonly T[], tolerance = 0.04): T[] {
	const sorted = rects.slice().sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
	const rows: T[][] = [];
	for (const rect of sorted) {
		const row = rows.find((candidate) => Math.abs(candidate[0].y0 - rect.y0) <= tolerance);
		if (row) row.push(rect);
		else rows.push([rect]);
	}
	return rows.flatMap((row) => row.sort((a, b) => a.x0 - b.x0));
}

export function detectPanels(an: Analysis): Rect[] {
	const leaves = xyCut(an);
	const panels = leaves.filter((leaf) => area(leaf) >= MIN_PANEL_AREA);
	for (const leaf of leaves) {
		if (area(leaf) >= MIN_PANEL_AREA) continue;
		const host = panels.findIndex((panel) => {
			const g = gap(panel, leaf, an);
			return g.x <= 2 * an.minGutter && g.y <= 2 * an.minGutter;
		});
		if (host >= 0) panels[host] = union(panels[host], leaf);
	}
	let merged = true;
	while (merged) {
		merged = false;
		for (let i = 0; i < panels.length && !merged; i++) {
			for (let j = i + 1; j < panels.length && !merged; j++) {
				const a = panels[i];
				const b = panels[j];
				const g = gap(a, b, an);
				const closeX = g.x < an.minGutter && g.y === 0 && overlapShare(a.y0, a.y1, b.y0, b.y1) > 0.6;
				const closeY = g.y < an.minGutter && g.x === 0 && overlapShare(a.x0, a.x1, b.x0, b.x1) > 0.6;
				if (closeX || closeY) {
					panels.splice(j, 1);
					panels[i] = union(a, b);
					merged = true;
				}
			}
		}
	}
	return orderRows(panels);
}
