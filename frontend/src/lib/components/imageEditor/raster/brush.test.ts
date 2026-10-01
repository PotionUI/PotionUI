import { describe, expect, it, vi } from 'vitest';
import { canvasPoint, drawDot, drawSegment, strokeBounds } from './brush';

function fakeContext() {
	return {
		globalCompositeOperation: '',
		strokeStyle: '',
		fillStyle: '',
		lineWidth: 0,
		lineCap: '',
		lineJoin: '',
		beginPath: vi.fn(),
		moveTo: vi.fn(),
		lineTo: vi.fn(),
		stroke: vi.fn(),
		arc: vi.fn(),
		fill: vi.fn()
	};
}

describe('canvasPoint', () => {
	it('maps client pixels into canvas pixels through the display scale', () => {
		const point = canvasPoint(
			{ left: 100, top: 50, width: 200, height: 100 },
			{ width: 400, height: 400 },
			200,
			100
		);
		expect(point).toEqual({ x: 200, y: 200 });
	});

	it('returns null for a collapsed box', () => {
		expect(
			canvasPoint({ left: 0, top: 0, width: 0, height: 10 }, { width: 5, height: 5 }, 1, 1)
		).toBeNull();
	});
});

describe('drawSegment', () => {
	it('paints with source-over and a round cap', () => {
		const context = fakeContext();
		drawSegment(
			context as unknown as CanvasRenderingContext2D,
			{ x: 1, y: 2 },
			{ x: 3, y: 4 },
			{ size: 12, color: 'rgb(1, 2, 3)', erase: false }
		);
		expect(context.globalCompositeOperation).toBe('source-over');
		expect(context.lineWidth).toBe(12);
		expect(context.lineCap).toBe('round');
		expect(context.strokeStyle).toBe('rgb(1, 2, 3)');
		expect(context.moveTo).toHaveBeenCalledWith(1, 2);
		expect(context.lineTo).toHaveBeenCalledWith(3, 4);
		expect(context.stroke).toHaveBeenCalledOnce();
	});

	it('erases with destination-out', () => {
		const context = fakeContext();
		drawSegment(
			context as unknown as CanvasRenderingContext2D,
			{ x: 0, y: 0 },
			{ x: 0, y: 0 },
			{ size: 4, color: 'rgb(0, 0, 0)', erase: true }
		);
		expect(context.globalCompositeOperation).toBe('destination-out');
	});
});

describe('strokeBounds', () => {
	it('pads the segment by the brush radius', () => {
		expect(strokeBounds({ x: 10, y: 10 }, { x: 20, y: 5 }, 8)).toEqual({
			x: 4,
			y: -1,
			width: 22,
			height: 17
		});
	});
});

describe('drawDot', () => {
	it('fills a circle of the brush diameter', () => {
		const context = fakeContext();
		drawDot(
			context as unknown as CanvasRenderingContext2D,
			{ x: 5, y: 6 },
			{ size: 10, color: 'rgb(9, 9, 9)', erase: false }
		);
		expect(context.arc).toHaveBeenCalledWith(5, 6, 5, 0, Math.PI * 2);
		expect(context.fill).toHaveBeenCalledOnce();
		expect(context.fillStyle).toBe('rgb(9, 9, 9)');
	});
});
