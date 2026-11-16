import type { Layer } from './types';

export interface ThumbParams {
	layer: Layer;
	revision: number;
}

function paint(node: HTMLCanvasElement, params: ThumbParams) {
	const context = node.getContext('2d');
	if (!context) return;
	const { canvas } = params.layer;
	context.clearRect(0, 0, node.width, node.height);
	const scale = Math.min(node.width / canvas.width, node.height / canvas.height);
	const width = canvas.width * scale;
	const height = canvas.height * scale;
	context.drawImage(canvas, (node.width - width) / 2, (node.height - height) / 2, width, height);
}

export default function thumb(node: HTMLCanvasElement, params: ThumbParams) {
	paint(node, params);
	return {
		update(next: ThumbParams) {
			paint(node, next);
		}
	};
}
