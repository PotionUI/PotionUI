export interface DisplayParams {
	canvas: HTMLCanvasElement | null;
	onrestore?: () => void;
}

export function paint(node: HTMLCanvasElement, canvas: HTMLCanvasElement | null): void {
	if (!canvas) return;
	node.width = canvas.width;
	node.height = canvas.height;
	node.getContext('2d')?.drawImage(canvas, 0, 0);
}

export function showCanvas(node: HTMLCanvasElement, params: DisplayParams) {
	let current = params;
	const restored = () => {
		paint(node, current.canvas);
		current.onrestore?.();
	};
	node.addEventListener('contextrestored', restored);
	paint(node, current.canvas);
	return {
		update(next: DisplayParams) {
			current = next;
			paint(node, next.canvas);
		},
		destroy() {
			node.removeEventListener('contextrestored', restored);
		}
	};
}
