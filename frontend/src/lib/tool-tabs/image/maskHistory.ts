export interface AlphaPatch {
	x: number;
	y: number;
	width: number;
	height: number;
	before: Uint8Array;
	after: Uint8Array;
}

export const MASK_HISTORY_BYTES = 48 * 1024 * 1024;
export const MASK_HISTORY_STEPS = 100;

export function alphaPatch(before: Uint8Array, after: Uint8Array, width: number, height: number): AlphaPatch | null {
	let minX = width;
	let minY = height;
	let maxX = -1;
	let maxY = -1;
	for (let y = 0; y < height; y++) {
		const row = y * width;
		for (let x = 0; x < width; x++) {
			if (before[row + x] === after[row + x]) continue;
			if (x < minX) minX = x;
			if (x > maxX) maxX = x;
			if (y < minY) minY = y;
			if (y > maxY) maxY = y;
		}
	}
	if (maxX < 0) return null;
	const w = maxX - minX + 1;
	const h = maxY - minY + 1;
	const trimmedBefore = new Uint8Array(w * h);
	const trimmedAfter = new Uint8Array(w * h);
	for (let y = 0; y < h; y++) {
		const from = (minY + y) * width + minX;
		trimmedBefore.set(before.subarray(from, from + w), y * w);
		trimmedAfter.set(after.subarray(from, from + w), y * w);
	}
	return { x: minX, y: minY, width: w, height: h, before: trimmedBefore, after: trimmedAfter };
}

export function applyAlphaPatch(alpha: Uint8Array, patch: AlphaPatch, width: number, side: 'before' | 'after'): void {
	const source = patch[side];
	for (let y = 0; y < patch.height; y++) {
		alpha.set(source.subarray(y * patch.width, (y + 1) * patch.width), (patch.y + y) * width + patch.x);
	}
}

export function trimPatches(patches: AlphaPatch[], budgetBytes = MASK_HISTORY_BYTES, limit = MASK_HISTORY_STEPS): AlphaPatch[] {
	let start = Math.max(0, patches.length - limit);
	let total = 0;
	for (let i = start; i < patches.length; i++) total += patches[i].before.byteLength + patches[i].after.byteLength;
	while (start < patches.length - 1 && total > budgetBytes) {
		total -= patches[start].before.byteLength + patches[start].after.byteLength;
		start += 1;
	}
	return start === 0 ? patches : patches.slice(start);
}
