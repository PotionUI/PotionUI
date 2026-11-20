export const DEFAULT_PEN = '#111111';

export const DEFAULT_SIZE = 8;

export const SWATCHES: readonly string[] = [
	'#111111',
	'#ffffff',
	'#e5484d',
	'#f5a524',
	'#f5d90a',
	'#30a46c',
	'#3e63dd',
	'#8e4ec6'
];

export function hexToRgba(hex: string): readonly [number, number, number, number] {
	const value = hex.replace('#', '');
	const full =
		value.length === 3
			? value
					.split('')
					.map((c) => c + c)
					.join('')
			: value;
	const n = Number.parseInt(full.padEnd(6, '0').slice(0, 6), 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255];
}

export function rgbaToHex(r: number, g: number, b: number): string {
	return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}
