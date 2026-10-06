import type { PixelBuffer } from '$lib/components/imageEditor/types';
import type { Cube, FilterExtensions } from './engine';
import { renderRecipe } from './render';
import type { FilterItem } from './types';

export const THUMB_SIZE = 144;

export function makeThumbSource(canvas: HTMLCanvasElement, size = THUMB_SIZE): ImageData | null {
	try {
		const side = Math.min(canvas.width, canvas.height);
		if (side < 1) return null;
		const target = document.createElement('canvas');
		target.width = size;
		target.height = size;
		const context = target.getContext('2d');
		if (!context) return null;
		context.imageSmoothingQuality = 'high';
		context.drawImage(
			canvas,
			(canvas.width - side) / 2,
			(canvas.height - side) / 2,
			side,
			side,
			0,
			0,
			size,
			size
		);
		return context.getImageData(0, 0, size, size);
	} catch {
		return null;
	}
}

export function renderThumb(
	source: ImageData,
	item: FilterItem,
	cube: Cube | null,
	extensions?: FilterExtensions
): ImageData {
	const buffer: PixelBuffer = { width: source.width, height: source.height, data: source.data };
	const output = renderRecipe(buffer, { steps: item.steps, cube }, item.intensity, { extensions });
	if (typeof ImageData === 'undefined') return output as unknown as ImageData;
	return new ImageData(output.data as Uint8ClampedArray<ArrayBuffer>, output.width, output.height);
}

export class ThumbCache {
	private entries = new Map<string, ImageData>();

	key(imageRevision: number, item: FilterItem): string {
		return `${imageRevision}:${item.id}:${item.revision}`;
	}

	get(imageRevision: number, item: FilterItem): ImageData | undefined {
		return this.entries.get(this.key(imageRevision, item));
	}

	set(imageRevision: number, item: FilterItem, image: ImageData): void {
		this.entries.set(this.key(imageRevision, item), image);
	}

	prune(imageRevision: number): void {
		const prefix = `${imageRevision}:`;
		for (const key of this.entries.keys()) if (!key.startsWith(prefix)) this.entries.delete(key);
	}

	get size(): number {
		return this.entries.size;
	}
}
