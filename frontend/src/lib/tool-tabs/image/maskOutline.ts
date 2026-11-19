export function maskOutline(
	pixels: Uint8ClampedArray,
	width: number,
	height: number,
	tint: readonly [number, number, number],
	cutoff = 128
): Uint8ClampedArray<ArrayBuffer> {
	const out = new Uint8ClampedArray(width * height * 4);
	const lit = (x: number, y: number) => x >= 0 && y >= 0 && x < width && y < height && pixels[(y * width + x) * 4] > cutoff;
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			if (!lit(x, y)) continue;
			const at = (y * width + x) * 4;
			const edge = !lit(x - 2, y) || !lit(x + 2, y) || !lit(x, y - 2) || !lit(x, y + 2);
			out[at] = tint[0];
			out[at + 1] = tint[1];
			out[at + 2] = tint[2];
			out[at + 3] = edge && (x + y) % 22 < 14 ? 255 : 31;
		}
	}
	return out;
}
