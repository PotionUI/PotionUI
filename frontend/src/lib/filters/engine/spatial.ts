import { clamp01 } from './colour';
import { FilterOpUnavailable } from './lut';
import { OPS_BY_ID, getOp, resolveValues, splitSteps } from './ops';
import type { FilterExtensions, FilterStep, StepValues } from './types';

export function lowbias32(input: number): number {
	let x = input | 0;
	x ^= x >>> 16;
	x = Math.imul(x, 0x7feb352d);
	x ^= x >>> 15;
	x = Math.imul(x, 0x846ca68b);
	x ^= x >>> 16;
	return x >>> 0;
}

export function grainCell(size: number, width: number, height: number): number {
	return Math.max(1, Math.floor((size * Math.min(width, height)) / 1000 + 0.5));
}

export function grainNoise(cx: number, cy: number, seed: number): number {
	const h = lowbias32(
		(Math.imul(cx, 73856093) ^ Math.imul(cy, 19349663) ^ Math.imul(seed + 1, 83492791)) >>> 0
	);
	return h / 4294967296 + lowbias32(h ^ 0x9e3779b9) / 4294967296 - 1;
}

function vignette(
	data: Uint8ClampedArray,
	width: number,
	height: number,
	p: StepValues,
	k: number
): void {
	const diag = Math.hypot(width, height);
	const a = ((p.amount as number) / 100) * k;
	const e0 = (p.midpoint as number) / 100;
	const e1 = e0 + Math.max(0.05, (p.feather as number) / 100);
	for (let y = 0; y < height; y++) {
		const dy = y + 0.5 - height / 2;
		for (let x = 0; x < width; x++) {
			const r = (Math.hypot(x + 0.5 - width / 2, dy) / diag) * 2;
			const t = clamp01((r - e0) / (e1 - e0));
			const w = t * t * (3 - 2 * t);
			const i = (y * width + x) * 4;
			for (let c = 0; c < 3; c++) {
				const v = data[i + c] / 255;
				const out = a >= 0 ? v * (1 - a * w) : v + (1 - v) * -a * w;
				data[i + c] = Math.floor(clamp01(out) * 255 + 0.5);
			}
		}
	}
}

function grain(
	data: Uint8ClampedArray,
	width: number,
	height: number,
	p: StepValues,
	k: number
): void {
	const amount = ((p.amount as number) / 100) * k;
	const cell = grainCell(p.size as number, width, height);
	const seed = p.seed as number;
	for (let y = 0; y < height; y++) {
		const cy = Math.floor(y / cell);
		for (let x = 0; x < width; x++) {
			const noise = grainNoise(Math.floor(x / cell), cy, seed);
			const i = (y * width + x) * 4;
			const yv = (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]) / 255;
			const t = 2 * yv - 1;
			const mid = 1 - 0.7 * (t * t);
			const delta = noise * amount * 0.14 * mid * 255;
			for (let c = 0; c < 3; c++) {
				data[i + c] = Math.max(0, Math.min(255, Math.floor(data[i + c] + delta + 0.5)));
			}
		}
	}
}

const CORE_SPATIAL: Record<
	string,
	(data: Uint8ClampedArray, width: number, height: number, p: StepValues, k: number) => void
> = { vignette, grain };

export function applySpatial(
	data: Uint8ClampedArray,
	width: number,
	height: number,
	steps: FilterStep[],
	intensity: number = 100,
	extensions?: FilterExtensions
): void {
	const { spatial } = splitSteps(steps, extensions);
	const k = intensity / 100;
	for (const step of spatial) {
		const spec = getOp(step.op, extensions);
		if (!spec) throw new FilterOpUnavailable(step.op);
		const values = resolveValues(spec, step);
		if (step.op in OPS_BY_ID) {
			CORE_SPATIAL[step.op](data, width, height, values, k);
			continue;
		}
		const plugin = extensions?.spatial?.[step.op];
		if (!plugin) throw new FilterOpUnavailable(step.op);
		plugin(data, width, height, values, k);
	}
}
