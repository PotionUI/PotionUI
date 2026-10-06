import type { CurvePoint, StepValues } from './types';

export type ColourMap = (c: Float64Array) => void;

export const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);

const lin = (v: number): number =>
	v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
const enc = (l: number): number =>
	l <= 0.0031308 ? l * 12.92 : 1.055 * Math.pow(l, 1 / 2.4) - 0.055;
const luma = (r: number, g: number, b: number): number => 0.2126 * r + 0.7152 * g + 0.0722 * b;

function smoothstep(a: number, b: number, x: number): number {
	const t = clamp01((x - a) / (b - a));
	return t * t * (3 - 2 * t);
}

function hueRgb(hue: number): [number, number, number] {
	const channel = (n: number): number => {
		const k = (n + hue / 30) % 12;
		return 0.5 - 0.5 * Math.max(-1, Math.min(k - 3, 9 - k, 1));
	};
	return [channel(0), channel(8), channel(4)];
}

export function pchip(points: CurvePoint[]): (v: number) => number {
	const n = points.length;
	const x = points.map((p) => p[0]);
	const y = points.map((p) => p[1]);
	const h: number[] = [];
	const d: number[] = [];
	for (let i = 0; i < n - 1; i++) {
		h[i] = x[i + 1] - x[i];
		d[i] = (y[i + 1] - y[i]) / h[i];
	}
	const m: number[] = new Array(n).fill(0);
	if (n === 2) {
		m[0] = m[1] = d[0];
	} else {
		for (let i = 1; i < n - 1; i++) {
			if (d[i - 1] * d[i] <= 0) {
				m[i] = 0;
			} else {
				const w1 = 2 * h[i] + h[i - 1];
				const w2 = h[i] + 2 * h[i - 1];
				m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
			}
		}
		m[0] = d[0];
		m[n - 1] = d[n - 2];
	}
	return (v: number): number => {
		if (v <= x[0]) return y[0] + m[0] * (v - x[0]);
		if (v >= x[n - 1]) return y[n - 1] + m[n - 1] * (v - x[n - 1]);
		let i = 0;
		while (v > x[i + 1]) i++;
		const t = (v - x[i]) / h[i];
		const t2 = t * t;
		const t3 = t2 * t;
		return (
			(2 * t3 - 3 * t2 + 1) * y[i] +
			(t3 - 2 * t2 + t) * h[i] * m[i] +
			(-2 * t3 + 3 * t2) * y[i + 1] +
			(t3 - t2) * h[i] * m[i + 1]
		);
	};
}

const num = (values: StepValues, key: string): number => values[key] as number;

function tone(p: StepValues): ColourMap {
	const brightness = Math.max(0, 1 + num(p, 'brightness') / 100);
	const contrast = Math.max(0, 1 + num(p, 'contrast') / 100);
	const s = Math.max(0, 1 + num(p, 'saturation') / 100);
	const angle = (num(p, 'hue') * Math.PI) / 180;
	const cs = Math.cos(angle);
	const sn = Math.sin(angle);
	const sat = [
		0.213 + 0.787 * s,
		0.715 - 0.715 * s,
		0.072 - 0.072 * s,
		0.213 - 0.213 * s,
		0.715 + 0.285 * s,
		0.072 - 0.072 * s,
		0.213 - 0.213 * s,
		0.715 - 0.715 * s,
		0.072 + 0.928 * s
	];
	const hue = [
		0.213 + cs * 0.787 - sn * 0.213,
		0.715 - cs * 0.715 - sn * 0.715,
		0.072 - cs * 0.072 + sn * 0.928,
		0.213 - cs * 0.213 + sn * 0.143,
		0.715 + cs * 0.285 + sn * 0.14,
		0.072 - cs * 0.072 - sn * 0.283,
		0.213 - cs * 0.213 - sn * 0.787,
		0.715 - cs * 0.715 + sn * 0.715,
		0.072 + cs * 0.928 + sn * 0.072
	];
	return (c) => {
		let r = clamp01(c[0] * brightness);
		let g = clamp01(c[1] * brightness);
		let b = clamp01(c[2] * brightness);
		r = clamp01((r - 0.5) * contrast + 0.5);
		g = clamp01((g - 0.5) * contrast + 0.5);
		b = clamp01((b - 0.5) * contrast + 0.5);
		const sr = sat[0] * r + sat[1] * g + sat[2] * b;
		const sg = sat[3] * r + sat[4] * g + sat[5] * b;
		const sb = sat[6] * r + sat[7] * g + sat[8] * b;
		c[0] = hue[0] * sr + hue[1] * sg + hue[2] * sb;
		c[1] = hue[3] * sr + hue[4] * sg + hue[5] * sb;
		c[2] = hue[6] * sr + hue[7] * sg + hue[8] * sb;
	};
}

function exposure(p: StepValues): ColourMap {
	const gain = Math.pow(2, num(p, 'stops'));
	return (c) => {
		for (let i = 0; i < 3; i++) c[i] = enc(lin(c[i]) * gain);
	};
}

function whiteBalance(p: StepValues): ColourMap {
	const t = num(p, 'temperature') / 100;
	const n = num(p, 'tint') / 100;
	const gr = Math.pow(2, 0.35 * t) * Math.pow(2, 0.125 * n);
	const gg = Math.pow(2, -0.25 * n);
	const gb = Math.pow(2, -0.35 * t) * Math.pow(2, 0.125 * n);
	const norm = luma(gr, gg, gb);
	const rg = gr / norm;
	const gg2 = gg / norm;
	const bg = gb / norm;
	return (c) => {
		c[0] = enc(lin(c[0]) * rg);
		c[1] = enc(lin(c[1]) * gg2);
		c[2] = enc(lin(c[2]) * bg);
	};
}

function curves(p: StepValues): ColourMap {
	const master = pchip(p.master as CurvePoint[]);
	const red = pchip(p.r as CurvePoint[]);
	const green = pchip(p.g as CurvePoint[]);
	const blue = pchip(p.b as CurvePoint[]);
	return (c) => {
		c[0] = master(clamp01(red(c[0])));
		c[1] = master(clamp01(green(c[1])));
		c[2] = master(clamp01(blue(c[2])));
	};
}

function vibrance(p: StepValues): ColourMap {
	const a = num(p, 'amount') / 100;
	return (c) => {
		const y = luma(c[0], c[1], c[2]);
		const sat = Math.max(c[0], c[1], c[2]) - Math.min(c[0], c[1], c[2]);
		const f = Math.max(0, 1 + a * (1 - sat));
		for (let i = 0; i < 3; i++) c[i] = y + (c[i] - y) * f;
	};
}

function splitTone(p: StepValues): ColourMap {
	const hs = hueRgb(num(p, 'shadow_hue'));
	const hh = hueRgb(num(p, 'highlight_hue'));
	const ys = luma(hs[0], hs[1], hs[2]);
	const yh = luma(hh[0], hh[1], hh[2]);
	const ss = num(p, 'shadow_sat') / 100;
	const sh = num(p, 'highlight_sat') / 100;
	const balance = num(p, 'balance') / 200;
	return (c) => {
		const y = luma(c[0], c[1], c[2]);
		const wh = smoothstep(0, 1, clamp01(y - balance));
		const ws = 1 - wh;
		for (let i = 0; i < 3; i++) c[i] += 0.5 * (ws * ss * (hs[i] - ys) + wh * sh * (hh[i] - yh));
	};
}

function fade(p: StepValues): ColourMap {
	const lift = num(p, 'black_lift') / 100;
	const cap = num(p, 'white_cap') / 100;
	return (c) => {
		for (let i = 0; i < 3; i++) c[i] = lift + c[i] * (1 - lift - cap);
	};
}

function grayscale(): ColourMap {
	return (c) => {
		const y = luma(c[0], c[1], c[2]);
		c[0] = c[1] = c[2] = y;
	};
}

function invert(): ColourMap {
	return (c) => {
		for (let i = 0; i < 3; i++) c[i] = 1 - c[i];
	};
}

export const COLOUR_OPS: Record<string, (p: StepValues) => ColourMap> = {
	tone,
	exposure,
	white_balance: whiteBalance,
	curves,
	vibrance,
	split_tone: splitTone,
	fade,
	grayscale,
	invert
};
