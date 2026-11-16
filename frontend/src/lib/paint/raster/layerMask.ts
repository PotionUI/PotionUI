export function sampleMaskForLayer(
	mask: Uint8ClampedArray,
	docWidth: number,
	docHeight: number,
	layerWidth: number,
	layerHeight: number,
	offsetX: number,
	offsetY: number
): Uint8ClampedArray {
	const out = new Uint8ClampedArray(layerWidth * layerHeight);
	for (let y = 0; y < layerHeight; y++) {
		const docY = y + offsetY;
		if (docY < 0 || docY >= docHeight) continue;
		for (let x = 0; x < layerWidth; x++) {
			const docX = x + offsetX;
			if (docX < 0 || docX >= docWidth) continue;
			out[y * layerWidth + x] = mask[docY * docWidth + docX];
		}
	}
	return out;
}

export function clearByMask(data: Uint8ClampedArray, mask: Uint8ClampedArray): void {
	const pixels = data.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = mask[p];
		if (coverage === 0) continue;
		const i = p * 4 + 3;
		data[i] = coverage >= 255 ? 0 : Math.round((data[i] * (255 - coverage)) / 255);
	}
}

export function extractByMask(data: Uint8ClampedArray, mask: Uint8ClampedArray): Uint8ClampedArray {
	const out = new Uint8ClampedArray(data);
	const pixels = data.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = mask[p];
		const i = p * 4 + 3;
		out[i] = coverage >= 255 ? data[i] : Math.round((data[i] * coverage) / 255);
	}
	return out;
}

export function blendByMask(
	original: Uint8ClampedArray,
	filtered: Uint8ClampedArray,
	mask: Uint8ClampedArray
): Uint8ClampedArray {
	const out = new Uint8ClampedArray(original);
	const pixels = original.length / 4;
	for (let p = 0; p < pixels; p++) {
		const coverage = mask[p];
		if (coverage === 0) continue;
		const i = p * 4;
		if (coverage >= 255) {
			out[i] = filtered[i];
			out[i + 1] = filtered[i + 1];
			out[i + 2] = filtered[i + 2];
			out[i + 3] = filtered[i + 3];
			continue;
		}
		for (let c = 0; c < 4; c++) {
			out[i + c] = original[i + c] + ((filtered[i + c] - original[i + c]) * coverage) / 255;
		}
	}
	return out;
}
