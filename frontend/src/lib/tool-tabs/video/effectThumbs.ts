import { showPixels } from '$lib/filters/thumbPainter';
import { makeThumbSource } from '$lib/filters/thumbs';
import type { StillPixels } from '$lib/tool-tabs/image/shaderEffect';
import type { EffectTile } from './effectTiles';

export interface TileInput {
	tile: EffectTile;
	source: ImageData | null;
}

export interface TileRenderer {
	render(pixels: StillPixels, effect: EffectTile['effect'], values: Record<string, unknown>): Promise<StillPixels>;
}

export class EffectThumbPainter {
	private cache = new Map<string, ImageData>();
	private revision = 0;
	private queue: Promise<void> = Promise.resolve();

	constructor(private readonly renderer: TileRenderer) {}

	begin(): number {
		this.revision += 1;
		this.cache.clear();
		return this.revision;
	}

	private run<T>(job: () => Promise<T>): Promise<T> {
		const next = this.queue.then(job, job);
		this.queue = next.then(
			() => undefined,
			() => undefined
		);
		return next;
	}

	draw = (node: HTMLCanvasElement, input: TileInput) => {
		let token = 0;
		const paint = async (next: TileInput) => {
			const mine = ++token;
			const source = next.source;
			if (!source) return;
			const at = this.revision;
			const key = `${at}:${next.tile.id}`;
			try {
				let image = this.cache.get(key);
				if (!image) {
					if (!next.tile.renderable) {
						showPixels(node, source);
						return;
					}
					image = await this.run(async () => {
						if (mine !== token || at !== this.revision) throw new Error('stale');
						const out = await this.renderer.render({ width: source.width, height: source.height, data: new Uint8ClampedArray(source.data) }, next.tile.effect, next.tile.values);
						return new ImageData(out.data as Uint8ClampedArray<ArrayBuffer>, out.width, out.height);
					});
					this.cache.set(key, image);
				}
				if (mine !== token) return;
				showPixels(node, image);
			} catch {
				if (mine === token) showPixels(node, source);
			}
		};
		void paint(input);
		return {
			update(next: TileInput) {
				void paint(next);
			},
			destroy() {
				token += 1;
			}
		};
	};
}

export async function frameSource(dataUrl: string, size: number): Promise<ImageData | null> {
	const image = new Image();
	image.src = dataUrl;
	await image.decode();
	const canvas = document.createElement('canvas');
	canvas.width = image.naturalWidth;
	canvas.height = image.naturalHeight;
	const context = canvas.getContext('2d');
	if (!context) return null;
	context.drawImage(image, 0, 0);
	return makeThumbSource(canvas, size);
}
