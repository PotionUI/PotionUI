import { loadImage } from '$lib/media/editors/loadImage';

export function canvasFromImage(image: HTMLImageElement): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = image.naturalWidth;
	canvas.height = image.naturalHeight;
	const context = canvas.getContext('2d');
	if (!context) throw new Error('This browser could not open a drawing surface.');
	context.drawImage(image, 0, 0);
	return canvas;
}

export async function canvasFromUrl(url: string): Promise<HTMLCanvasElement> {
	return canvasFromImage(await loadImage(url));
}

export async function canvasFromBlob(blob: Blob): Promise<HTMLCanvasElement> {
	const url = URL.createObjectURL(blob);
	try {
		return await canvasFromUrl(url);
	} finally {
		URL.revokeObjectURL(url);
	}
}

export function generationImageUrl(filePath: string): string | null {
	const segments = filePath.split('/').filter((segment) => segment.length > 0);
	if (segments.length < 2) return null;
	const filename = segments[segments.length - 1];
	const generationId = segments[segments.length - 2];
	return `/api/media/generations/${generationId}/${filename}`;
}

export function displayName(
	candidates: Array<string | undefined | null>,
	fallback: string
): string {
	for (const candidate of candidates) {
		const trimmed = (candidate ?? '').trim();
		if (trimmed) return trimmed;
	}
	return fallback;
}
