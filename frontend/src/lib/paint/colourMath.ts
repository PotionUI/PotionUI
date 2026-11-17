import { hexToRgba, rgbaToHex } from './palette';

export interface Hsv {
	h: number;
	s: number;
	v: number;
}

export function hexToHsv(hex: string): Hsv {
	const [r, g, b] = hexToRgba(hex).map((channel) => channel / 255);
	const max = Math.max(r, g, b);
	const min = Math.min(r, g, b);
	const delta = max - min;
	let h = 0;
	if (delta > 0) {
		if (max === r) h = ((g - b) / delta) % 6;
		else if (max === g) h = (b - r) / delta + 2;
		else h = (r - g) / delta + 4;
		h *= 60;
		if (h < 0) h += 360;
	}
	return { h, s: max === 0 ? 0 : delta / max, v: max };
}

export function hsvToHex({ h, s, v }: Hsv): string {
	const c = v * s;
	const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
	const m = v - c;
	const sector = Math.floor((((h % 360) + 360) % 360) / 60);
	const [r, g, b] = [
		[c, x, 0],
		[x, c, 0],
		[0, c, x],
		[0, x, c],
		[x, 0, c],
		[c, 0, x]
	][sector];
	return rgbaToHex(Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255));
}

export function normalizeHex(input: string): string | null {
	const value = input.trim().replace(/^#/, '');
	if (/^[0-9a-f]{3}$/i.test(value)) return `#${value.split('').map((c) => c + c).join('')}`.toLowerCase();
	if (/^[0-9a-f]{6}$/i.test(value)) return `#${value}`.toLowerCase();
	return null;
}
