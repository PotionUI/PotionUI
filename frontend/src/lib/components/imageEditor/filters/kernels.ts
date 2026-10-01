export interface ToneParams {
	brightness: number;
	contrast: number;
	saturation: number;
	hue: number;
}

export const NEUTRAL_TONE: ToneParams = {
	brightness: 0,
	contrast: 0,
	saturation: 0,
	hue: 0
};

export function isNeutralTone(params: ToneParams): boolean {
	return (
		params.brightness === 0 && params.contrast === 0 && params.saturation === 0 && params.hue === 0
	);
}

function blend(original: number, next: number, coverage: number): number {
	if (coverage >= 255) return next;
	if (coverage <= 0) return original;
	return original + ((next - original) * coverage) / 255;
}

function covered(mask: Uint8ClampedArray | undefined, pixel: number): number {
	return mask ? mask[pixel] : 255;
}

export function applyTone(
	data: Uint8ClampedArray,
	params: ToneParams,
	mask?: Uint8ClampedArray
): void {
	const brightness = Math.max(0, 1 + params.brightness / 100);
	const contrast = Math.max(0, 1 + params.contrast / 100);
	const saturation = Math.max(0, 1 + params.saturation / 100);
	const angle = (params.hue * Math.PI) / 180;
	const cos = Math.cos(angle);
	const sin = Math.sin(angle);

	const s = saturation;
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
		0.213 + cos * 0.787 - sin * 0.213,
		0.715 - cos * 0.715 - sin * 0.715,
		0.072 - cos * 0.072 + sin * 0.928,
		0.213 - cos * 0.213 + sin * 0.143,
		0.715 + cos * 0.285 + sin * 0.14,
		0.072 - cos * 0.072 - sin * 0.283,
		0.213 - cos * 0.213 - sin * 0.787,
		0.715 - cos * 0.715 + sin * 0.715,
		0.072 + cos * 0.928 + sin * 0.072
	];

	const pixels = data.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = covered(mask, p);
		if (coverage === 0) continue;
		const i = p * 4;
		const r0 = data[i];
		const g0 = data[i + 1];
		const b0 = data[i + 2];

		let r = Math.min(255, r0 * brightness);
		let g = Math.min(255, g0 * brightness);
		let b = Math.min(255, b0 * brightness);

		r = Math.min(255, Math.max(0, (r - 127.5) * contrast + 127.5));
		g = Math.min(255, Math.max(0, (g - 127.5) * contrast + 127.5));
		b = Math.min(255, Math.max(0, (b - 127.5) * contrast + 127.5));

		const sr = sat[0] * r + sat[1] * g + sat[2] * b;
		const sg = sat[3] * r + sat[4] * g + sat[5] * b;
		const sb = sat[6] * r + sat[7] * g + sat[8] * b;

		const hr = hue[0] * sr + hue[1] * sg + hue[2] * sb;
		const hg = hue[3] * sr + hue[4] * sg + hue[5] * sb;
		const hb = hue[6] * sr + hue[7] * sg + hue[8] * sb;

		data[i] = blend(r0, Math.min(255, Math.max(0, hr)), coverage);
		data[i + 1] = blend(g0, Math.min(255, Math.max(0, hg)), coverage);
		data[i + 2] = blend(b0, Math.min(255, Math.max(0, hb)), coverage);
	}
}

export function applyInvert(data: Uint8ClampedArray, mask?: Uint8ClampedArray): void {
	const pixels = data.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = covered(mask, p);
		if (coverage === 0) continue;
		const i = p * 4;
		data[i] = blend(data[i], 255 - data[i], coverage);
		data[i + 1] = blend(data[i + 1], 255 - data[i + 1], coverage);
		data[i + 2] = blend(data[i + 2], 255 - data[i + 2], coverage);
	}
}

export function applyGrayscale(data: Uint8ClampedArray, mask?: Uint8ClampedArray): void {
	const pixels = data.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = covered(mask, p);
		if (coverage === 0) continue;
		const i = p * 4;
		const luma = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
		data[i] = blend(data[i], luma, coverage);
		data[i + 1] = blend(data[i + 1], luma, coverage);
		data[i + 2] = blend(data[i + 2], luma, coverage);
	}
}
