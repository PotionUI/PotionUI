import type { PaintDocument } from './types';

export function flattenDocument(doc: PaintDocument): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = doc.width;
	canvas.height = doc.height;
	const context = canvas.getContext('2d');
	if (!context) throw new Error('This browser could not open a drawing surface.');
	for (const layer of doc.layers) {
		if (!layer.visible) continue;
		context.globalAlpha = layer.opacity;
		context.drawImage(layer.canvas, layer.x, layer.y);
	}
	context.globalAlpha = 1;
	return canvas;
}

export function canvasToPngFile(canvas: HTMLCanvasElement, fileName: string): Promise<File> {
	return new Promise((resolve, reject) => {
		canvas.toBlob((blob) => {
			if (!blob) {
				reject(new Error('The image could not be encoded.'));
				return;
			}
			resolve(new File([blob], fileName, { type: 'image/png' }));
		}, 'image/png');
	});
}
