import { vi } from 'vitest';

const buffers = new WeakMap<HTMLCanvasElement, Uint8ClampedArray>();

class PixelContext {
	globalCompositeOperation = 'source-over';
	lineWidth = 1;
	strokeStyle = '';
	fillStyle = '';
	lineCap = '';
	lineJoin = '';
	private from = { x: 0, y: 0 };
	private to = { x: 0, y: 0 };

	constructor(private readonly canvas: HTMLCanvasElement) {}

	private buffer(): Uint8ClampedArray {
		const need = this.canvas.width * this.canvas.height * 4;
		let held = buffers.get(this.canvas);
		if (!held || held.length !== need) {
			held = new Uint8ClampedArray(need);
			buffers.set(this.canvas, held);
		}
		return held;
	}

	getImageData(_x: number, _y: number, width: number, height: number) {
		return { width, height, data: new Uint8ClampedArray(this.buffer()) };
	}

	putImageData(image: { data: Uint8ClampedArray }) {
		this.buffer().set(image.data);
	}

	createImageData(width: number, height: number) {
		return { width, height, data: new Uint8ClampedArray(width * height * 4) };
	}

	clearRect() {
		this.buffer().fill(0);
	}

	beginPath() {}
	drawImage() {}

	moveTo(x: number, y: number) {
		this.from = { x, y };
	}

	lineTo(x: number, y: number) {
		this.to = { x, y };
	}

	stroke() {
		const buffer = this.buffer();
		const half = this.lineWidth / 2;
		const x0 = Math.max(0, Math.floor(Math.min(this.from.x, this.to.x) - half));
		const x1 = Math.min(this.canvas.width, Math.ceil(Math.max(this.from.x, this.to.x) + half));
		const y0 = Math.max(0, Math.floor(Math.min(this.from.y, this.to.y) - half));
		const y1 = Math.min(this.canvas.height, Math.ceil(Math.max(this.from.y, this.to.y) + half));
		const erase = this.globalCompositeOperation === 'destination-out';
		for (let y = y0; y < y1; y += 1) {
			for (let x = x0; x < x1; x += 1) {
				const at = (y * this.canvas.width + x) * 4;
				buffer.set(erase ? [0, 0, 0, 0] : [255, 255, 255, 255], at);
			}
		}
	}
}

export function installPixelCanvas() {
	const contexts = new WeakMap<HTMLCanvasElement, PixelContext>();
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
		let held = contexts.get(this);
		if (!held) {
			held = new PixelContext(this);
			contexts.set(this, held);
		}
		return held as unknown as CanvasRenderingContext2D;
	} as never);
	vi.stubGlobal(
		'ImageData',
		class {
			constructor(
				readonly data: Uint8ClampedArray,
				readonly width: number,
				readonly height: number
			) {}
		}
	);
	return {
		pixelsOf(canvas: HTMLCanvasElement): Uint8ClampedArray {
			return new Uint8ClampedArray(buffers.get(canvas) ?? new Uint8ClampedArray(0));
		},
		paint(canvas: HTMLCanvasElement, x: number, y: number) {
			const held = buffers.get(canvas);
			if (held) held.set([255, 255, 255, 255], (y * canvas.width + x) * 4);
		}
	};
}

export function placeCanvas(canvas: HTMLCanvasElement, box: { left: number; top: number; width: number; height: number }) {
	canvas.getBoundingClientRect = () =>
		({ ...box, right: box.left + box.width, bottom: box.top + box.height, x: box.left, y: box.top, toJSON: () => ({}) }) as DOMRect;
}

export function pointer(type: string, x: number, y: number, extra: PointerEventInit = {}) {
	return new PointerEvent(type, { button: 0, clientX: x, clientY: y, bubbles: true, pointerId: 1, ...extra });
}
