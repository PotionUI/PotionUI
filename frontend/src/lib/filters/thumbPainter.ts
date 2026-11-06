import { loadCube } from './catalog';
import type { FilterExtensions } from './engine';
import { makeThumbSource, renderThumb, ThumbCache } from './thumbs';
import type { FilterItem } from './types';

export interface ThumbInput {
	item: FilterItem;
	source: ImageData | null;
}

export class LookThumbPainter {
	private cache = new ThumbCache();
	private revision = 0;

	constructor(private readonly extensions: () => FilterExtensions | undefined = () => undefined) {}

	sourceFrom(canvas: HTMLCanvasElement | null, size: number): ImageData | null {
		this.revision += 1;
		this.cache.prune(this.revision);
		return canvas ? makeThumbSource(canvas, size) : null;
	}

	draw = (node: HTMLCanvasElement, input: ThumbInput) => {
		let token = 0;
		const paint = async (next: ThumbInput) => {
			const mine = ++token;
			const image = next.source;
			if (!image) return;
			const at = this.revision;
			try {
				let rendered = this.cache.get(at, next.item);
				if (!rendered) {
					const cube = next.item.has_lut ? await loadCube(next.item) : null;
					rendered = renderThumb(image, next.item, cube, this.extensions());
					this.cache.set(at, next.item, rendered);
				}
				if (mine !== token) return;
				showPixels(node, rendered);
			} catch {
				return;
			}
		};
		void paint(input);
		return {
			update(next: ThumbInput) {
				void paint(next);
			},
			destroy() {
				token += 1;
			}
		};
	};
}

export function showPixels(node: HTMLCanvasElement, image: ImageData): void {
	node.width = image.width;
	node.height = image.height;
	node.getContext('2d', { willReadFrequently: true })?.putImageData(image, 0, 0);
}

export function drawSource(node: HTMLCanvasElement, source: ImageData | null) {
	if (source) showPixels(node, source);
	return {
		update(next: ImageData | null) {
			if (next) showPixels(node, next);
		}
	};
}
