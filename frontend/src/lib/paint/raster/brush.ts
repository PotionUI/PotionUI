import type { Point } from '../types';

export interface BrushStyle {
	size: number;
	color: string;
	erase: boolean;
}

export function canvasPoint(
	box: { left: number; top: number; width: number; height: number },
	canvas: { width: number; height: number },
	clientX: number,
	clientY: number
): Point | null {
	if (box.width <= 0 || box.height <= 0) return null;
	return {
		x: ((clientX - box.left) / box.width) * canvas.width,
		y: ((clientY - box.top) / box.height) * canvas.height
	};
}

export function drawSegment(
	context: CanvasRenderingContext2D,
	from: Point,
	to: Point,
	style: BrushStyle
): void {
	context.globalCompositeOperation = style.erase ? 'destination-out' : 'source-over';
	context.strokeStyle = style.color;
	context.fillStyle = style.color;
	context.lineWidth = style.size;
	context.lineCap = 'round';
	context.lineJoin = 'round';
	context.beginPath();
	context.moveTo(from.x, from.y);
	context.lineTo(to.x, to.y);
	context.stroke();
}

export function strokeBounds(
	from: Point,
	to: Point,
	size: number
): {
	x: number;
	y: number;
	width: number;
	height: number;
} {
	const pad = size / 2 + 2;
	const x0 = Math.min(from.x, to.x) - pad;
	const y0 = Math.min(from.y, to.y) - pad;
	const x1 = Math.max(from.x, to.x) + pad;
	const y1 = Math.max(from.y, to.y) + pad;
	return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function drawDot(context: CanvasRenderingContext2D, point: Point, style: BrushStyle): void {
	context.globalCompositeOperation = style.erase ? 'destination-out' : 'source-over';
	context.fillStyle = style.color;
	context.beginPath();
	context.arc(point.x, point.y, style.size / 2, 0, Math.PI * 2);
	context.fill();
}
