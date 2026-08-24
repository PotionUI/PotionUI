import type { Rect, Size } from './cropGeometry';

/**
 * Thin canvas wrapper for the Prepare editor. Deliberately holds no decisions -
 * every number it draws with was computed by cropGeometry - so the untestable
 * half (vitest has no canvas) stays trivial and the testable half stays pure.
 */
export type CropRasterizer = (source: CanvasImageSource, cell: Size, rect: Rect) => Promise<Blob>;

export const rasterizeCropToPng: CropRasterizer = (source, cell, rect) =>
	new Promise<Blob>((resolve, reject) => {
		const canvas = document.createElement('canvas');
		canvas.width = Math.round(cell.width);
		canvas.height = Math.round(cell.height);

		const ctx = canvas.getContext('2d');
		if (!ctx) {
			reject(new Error('Could not get a 2D canvas context'));
			return;
		}

		ctx.imageSmoothingEnabled = true;
		ctx.imageSmoothingQuality = 'high';
		ctx.drawImage(source, rect.x, rect.y, rect.width, rect.height);

		canvas.toBlob((blob) => {
			if (blob) resolve(blob);
			else reject(new Error('Could not encode the prepared image'));
		}, 'image/png');
	});
