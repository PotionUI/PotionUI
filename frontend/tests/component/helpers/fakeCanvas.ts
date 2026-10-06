const pixelStore = new WeakMap<HTMLCanvasElement, Uint8ClampedArray>();

class FakeImageData {
	data: Uint8ClampedArray;
	width: number;
	height: number;
	colorSpace = 'srgb';

	constructor(first: Uint8ClampedArray | number, second: number, third?: number) {
		if (typeof first === 'number') {
			this.width = first;
			this.height = second;
			this.data = new Uint8ClampedArray(first * second * 4);
		} else {
			this.data = first;
			this.width = second;
			this.height = third ?? first.length / 4 / second;
		}
	}
}

function pixels(canvas: HTMLCanvasElement): Uint8ClampedArray {
	const size = canvas.width * canvas.height * 4;
	let data = pixelStore.get(canvas);
	if (!data || data.length !== size) {
		data = new Uint8ClampedArray(size);
		pixelStore.set(canvas, data);
	}
	return data;
}

function parseColor(style: string): [number, number, number, number] {
	const match = /rgba?\(([^)]+)\)/.exec(style);
	if (!match) return [0, 0, 0, 255];
	const [r, g, b, a] = match[1].split(',').map((part) => Number(part.trim()));
	return [r, g, b, a === undefined ? 255 : a * 255];
}

function makeContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
	const base: Record<string, unknown> = {
		canvas,
		fillStyle: 'rgb(0, 0, 0)',
		strokeStyle: '',
		globalAlpha: 1,
		lineWidth: 1,
		imageSmoothingEnabled: true,
		imageSmoothingQuality: 'low',
		fillRect(x: number, y: number, width: number, height: number) {
			const data = pixels(canvas);
			const color = parseColor(String(base.fillStyle));
			for (let row = Math.max(0, y); row < Math.min(canvas.height, y + height); row++) {
				for (let column = Math.max(0, x); column < Math.min(canvas.width, x + width); column++) {
					data.set(color, (row * canvas.width + column) * 4);
				}
			}
		},
		clearRect() {
			pixels(canvas).fill(0);
		},
		getImageData(x: number, y: number, width: number, height: number) {
			const data = pixels(canvas);
			const out = new FakeImageData(width, height);
			for (let row = 0; row < height; row++) {
				for (let column = 0; column < width; column++) {
					const sx = x + column;
					const sy = y + row;
					if (sx < 0 || sy < 0 || sx >= canvas.width || sy >= canvas.height) continue;
					const from = (sy * canvas.width + sx) * 4;
					out.data.set(data.subarray(from, from + 4), (row * width + column) * 4);
				}
			}
			return out;
		},
		putImageData(image: FakeImageData, x: number, y: number) {
			const data = pixels(canvas);
			for (let row = 0; row < image.height; row++) {
				for (let column = 0; column < image.width; column++) {
					const tx = x + column;
					const ty = y + row;
					if (tx < 0 || ty < 0 || tx >= canvas.width || ty >= canvas.height) continue;
					const from = (row * image.width + column) * 4;
					data.set(image.data.subarray(from, from + 4), (ty * canvas.width + tx) * 4);
				}
			}
		},
		drawImage(source: HTMLCanvasElement, ...args: number[]) {
			const data = pixels(canvas);
			const from = pixels(source);
			let [sx, sy, sw, sh, dx, dy, dw, dh] = [0, 0, source.width, source.height, 0, 0, source.width, source.height];
			if (args.length === 2) [dx, dy] = args;
			else if (args.length === 4) [dx, dy, dw, dh] = args;
			else if (args.length === 8) [sx, sy, sw, sh, dx, dy, dw, dh] = args;
			for (let row = 0; row < dh; row++) {
				for (let column = 0; column < dw; column++) {
					const tx = Math.round(dx) + column;
					const ty = Math.round(dy) + row;
					if (tx < 0 || ty < 0 || tx >= canvas.width || ty >= canvas.height) continue;
					const px = Math.min(source.width - 1, Math.floor(sx + (column / dw) * sw));
					const py = Math.min(source.height - 1, Math.floor(sy + (row / dh) * sh));
					const index = (py * source.width + px) * 4;
					data.set(from.subarray(index, index + 4), (ty * canvas.width + tx) * 4);
				}
			}
		},
		createPattern: () => null
	};
	return new Proxy(base, {
		get: (target, key) => (key in target ? target[key as string] : () => undefined),
		set: (target, key, value) => {
			target[key as string] = value;
			return true;
		}
	}) as unknown as CanvasRenderingContext2D;
}

export function installFakeCanvas(): () => void {
	const original = HTMLCanvasElement.prototype.getContext;
	const contexts = new WeakMap<HTMLCanvasElement, CanvasRenderingContext2D>();
	const previousImageData = (globalThis as { ImageData?: unknown }).ImageData;
	(globalThis as { ImageData?: unknown }).ImageData = FakeImageData;
	HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string) {
		if (kind !== '2d') return null;
		let context = contexts.get(this);
		if (!context) {
			context = makeContext(this);
			contexts.set(this, context);
		}
		return context;
	} as typeof original;
	return () => {
		HTMLCanvasElement.prototype.getContext = original;
		(globalThis as { ImageData?: unknown }).ImageData = previousImageData;
	};
}

export function readPixel(canvas: HTMLCanvasElement, x = 0, y = 0): number[] {
	const data = pixels(canvas);
	const index = (y * canvas.width + x) * 4;
	return Array.from(data.subarray(index, index + 4));
}
