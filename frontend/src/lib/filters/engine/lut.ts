import { COLOUR_OPS, clamp01, type ColourMap } from './colour';
import { OPS_BY_ID, getOp, resolveValues, splitSteps } from './ops';
import type { Cube, FilterExtensions, FilterStep, Lut, RgbTriple } from './types';

export const BASE_SIZE = 33;

export function identityLut(size: number = BASE_SIZE): Lut {
	const data = new Float32Array(size * size * size * 3);
	const m = size - 1;
	let o = 0;
	for (let b = 0; b < size; b++) {
		for (let g = 0; g < size; g++) {
			for (let r = 0; r < size; r++) {
				data[o++] = r / m;
				data[o++] = g / m;
				data[o++] = b / m;
			}
		}
	}
	return { size, data };
}

export function tetra(
	data: Float32Array,
	n: number,
	x: number,
	y: number,
	z: number,
	out: Float64Array
): void {
	const xi = Math.min(Math.floor(x), n - 2);
	const yi = Math.min(Math.floor(y), n - 2);
	const zi = Math.min(Math.floor(z), n - 2);
	const fr = x - xi;
	const fg = y - yi;
	const fb = z - zi;
	const sg = 3 * n;
	const sb = 3 * n * n;
	const o = zi * sb + yi * sg + xi * 3;
	for (let k = 0; k < 3; k++) {
		const c000 = data[o + k];
		const c100 = data[o + 3 + k];
		const c010 = data[o + sg + k];
		const c001 = data[o + sb + k];
		const c110 = data[o + 3 + sg + k];
		const c101 = data[o + 3 + sb + k];
		const c011 = data[o + sg + sb + k];
		const c111 = data[o + 3 + sg + sb + k];
		let v: number;
		if (fr >= fg) {
			if (fg >= fb) v = c000 + fr * (c100 - c000) + fg * (c110 - c100) + fb * (c111 - c110);
			else if (fr >= fb) v = c000 + fr * (c100 - c000) + fb * (c101 - c100) + fg * (c111 - c101);
			else v = c000 + fb * (c001 - c000) + fr * (c101 - c001) + fg * (c111 - c101);
		} else if (fb >= fg) {
			v = c000 + fb * (c001 - c000) + fg * (c011 - c001) + fr * (c111 - c011);
		} else if (fb >= fr) {
			v = c000 + fg * (c010 - c000) + fb * (c011 - c010) + fr * (c111 - c011);
		} else {
			v = c000 + fg * (c010 - c000) + fr * (c110 - c010) + fb * (c111 - c110);
		}
		out[k] = v;
	}
}

function sampleCube(cube: Cube, c: Float64Array, out: Float64Array): void {
	const m = cube.size - 1;
	const u0 = clamp01((c[0] - cube.domainMin[0]) / (cube.domainMax[0] - cube.domainMin[0]));
	const u1 = clamp01((c[1] - cube.domainMin[1]) / (cube.domainMax[1] - cube.domainMin[1]));
	const u2 = clamp01((c[2] - cube.domainMin[2]) / (cube.domainMax[2] - cube.domainMin[2]));
	tetra(cube.data, cube.size, u0 * m, u1 * m, u2 * m, out);
}

export class FilterOpUnavailable extends Error {
	readonly opId: string;
	constructor(opId: string) {
		super(`op '${opId}' has no implementation in this process`);
		this.name = 'FilterOpUnavailable';
		this.opId = opId;
	}
}

function colourMap(step: FilterStep, extensions?: FilterExtensions): ColourMap {
	const spec = getOp(step.op, extensions);
	if (!spec) throw new FilterOpUnavailable(step.op);
	const values = resolveValues(spec, step);
	const builder = step.op in OPS_BY_ID ? COLOUR_OPS[step.op] : undefined;
	if (builder) return builder(values);
	const plugin = extensions?.colour?.[step.op];
	if (!plugin) throw new FilterOpUnavailable(step.op);
	return (c) => {
		const mapped = plugin([c[0], c[1], c[2]] as RgbTriple, values);
		c[0] = mapped[0];
		c[1] = mapped[1];
		c[2] = mapped[2];
	};
}

export function compileLut(
	steps: FilterStep[],
	cube?: Cube | null,
	intensity: number = 100,
	extensions?: FilterExtensions
): Lut {
	const { colour } = splitSteps(steps, extensions);
	const maps = colour.map((step) => colourMap(step, extensions));
	const size = cube ? Math.max(BASE_SIZE, cube.size) : BASE_SIZE;
	const k = intensity / 100;
	const m = size - 1;
	const data = new Float32Array(size * size * size * 3);
	const c = new Float64Array(3);
	const sampled = new Float64Array(3);
	let o = 0;
	for (let b = 0; b < size; b++) {
		for (let g = 0; g < size; g++) {
			for (let r = 0; r < size; r++) {
				const i0 = r / m;
				const i1 = g / m;
				const i2 = b / m;
				c[0] = i0;
				c[1] = i1;
				c[2] = i2;
				if (cube) {
					sampleCube(cube, c, sampled);
					c[0] = sampled[0];
					c[1] = sampled[1];
					c[2] = sampled[2];
				}
				for (const map of maps) {
					map(c);
					c[0] = clamp01(c[0]);
					c[1] = clamp01(c[1]);
					c[2] = clamp01(c[2]);
				}
				data[o++] = i0 + k * (c[0] - i0);
				data[o++] = i1 + k * (c[1] - i1);
				data[o++] = i2 + k * (c[2] - i2);
			}
		}
	}
	return { size, data };
}

export function applyLut(data: Uint8ClampedArray, lut: Lut): void {
	const n = lut.size;
	const m = n - 1;
	const out = new Float64Array(3);
	for (let i = 0; i < data.length; i += 4) {
		tetra(lut.data, n, (data[i] / 255) * m, (data[i + 1] / 255) * m, (data[i + 2] / 255) * m, out);
		data[i] = Math.floor(clamp01(out[0]) * 255 + 0.5);
		data[i + 1] = Math.floor(clamp01(out[1]) * 255 + 0.5);
		data[i + 2] = Math.floor(clamp01(out[2]) * 255 + 0.5);
	}
}
