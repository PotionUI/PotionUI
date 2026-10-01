import type { ToolHost } from '../types';

export function drawCursorRing(host: ToolHost, context: CanvasRenderingContext2D): void {
	const cursor = host.cursor;
	if (!cursor) return;
	const radius = host.settings.size / 2;
	const hairline = 1 / host.zoom;
	context.save();
	context.globalAlpha = 1;
	context.globalCompositeOperation = 'source-over';
	context.lineWidth = hairline;
	context.strokeStyle = host.cursorColor;
	context.beginPath();
	context.arc(cursor.x, cursor.y, radius, 0, Math.PI * 2);
	context.stroke();
	context.restore();
}
