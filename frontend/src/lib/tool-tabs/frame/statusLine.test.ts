import { describe, expect, it } from 'vitest';
import { formatSize, statusParts, statusText } from './statusLine';

const picture = { width: 1536, height: 1024 };

describe('the status line', () => {
	it('shows only the size for a tool with nothing marked', () => {
		expect(statusText({ tool: 'effects', areaTool: false, size: picture, marked: null, output: null })).toBe('1536×1024');
	});

	it('adds the marked area for a tool that has one', () => {
		const parts = statusParts({ tool: 'edit', areaTool: true, size: picture, marked: { width: 412, height: 380 }, output: null });
		expect(parts).toEqual({ size: '1536×1024', extra: 'Marked 412×380' });
		expect(statusText({ tool: 'mask', areaTool: true, size: picture, marked: { width: 412, height: 380 }, output: null })).toBe('1536×1024 · Marked 412×380');
	});

	it('leaves the marked part out until an area is drawn', () => {
		expect(statusText({ tool: 'edit', areaTool: true, size: picture, marked: null, output: null })).toBe('1536×1024');
	});

	it('adds the output size for Crop and Resize', () => {
		expect(statusText({ tool: 'crop', areaTool: false, size: picture, marked: null, output: { width: 1200, height: 800 } })).toBe('1536×1024 · Output 1200×800');
		expect(statusText({ tool: 'resize', areaTool: false, size: picture, marked: null, output: { width: 640, height: 480 } })).toBe('1536×1024 · Output 640×480');
	});

	it('never shows the marked area for Crop or the output for other tools', () => {
		expect(statusText({ tool: 'crop', areaTool: true, size: picture, marked: { width: 4, height: 4 }, output: null })).toBe('1536×1024');
		expect(statusText({ tool: 'paint', areaTool: false, size: picture, marked: null, output: { width: 1, height: 1 } })).toBe('1536×1024');
	});

	it('rounds and rejects an empty size', () => {
		expect(formatSize({ width: 100.4, height: 50.6 })).toBe('100×51');
		expect(formatSize({ width: 0, height: 50 })).toBeNull();
		expect(formatSize(null)).toBeNull();
		expect(statusText({ tool: null, areaTool: false, size: null, marked: null, output: null })).toBe('');
	});
});
