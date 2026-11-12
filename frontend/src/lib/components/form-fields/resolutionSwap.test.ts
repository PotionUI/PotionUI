import { describe, it, expect } from 'vitest';
import { mirrorResolution, parseDims, resolveSwap } from './resolutionSwap';

const opts = (...values: string[]) => values.map((value) => ({ value }));

describe('parseDims / mirrorResolution', () => {
	it('parses x and × separators', () => {
		expect(parseDims('1920x1080')).toEqual({ width: 1920, height: 1080 });
		expect(parseDims('1920×1080')).toEqual({ width: 1920, height: 1080 });
		expect(parseDims('nope')).toBeNull();
	});
	it('mirrors non-square and refuses square', () => {
		expect(mirrorResolution('1920x1080')).toBe('1080x1920');
		expect(mirrorResolution('1024x1024')).toBeNull();
	});
});

describe('resolveSwap', () => {
	it('enables when the exact mirror is in the options', () => {
		const s = resolveSwap('1920x1080', opts('1920x1080', '1080x1920'), { allowCustom: false });
		expect(s).toMatchObject({ visible: true, enabled: true, target: '1080x1920', label: 'Swap to 1080×1920' });
	});

	it('disables with a plain reason when the mirror is absent and custom is off', () => {
		const s = resolveSwap('1920x1080', opts('1920x1080', '1280x720'), { allowCustom: false });
		expect(s).toMatchObject({ visible: true, enabled: false, target: null });
		expect(s.label).toBe('No 1080×1920 option for this model');
	});

	it('disables for a square current size when other options have orientation', () => {
		const s = resolveSwap('1024x1024', opts('1024x1024', '1920x1080'));
		expect(s.visible).toBe(true);
		expect(s.enabled).toBe(false);
	});

	it('hides when nothing has an orientation', () => {
		const s = resolveSwap('1024x1024', opts('1024x1024', '768x768'));
		expect(s.visible).toBe(false);
	});

	it('allows a custom mirror within the field limits', () => {
		const s = resolveSwap('1920x1080', opts('1920x1080'), { allowCustom: true });
		expect(s).toMatchObject({ enabled: true, target: '1080x1920' });
	});

	it('rejects a custom mirror that breaks the 8px step', () => {
		const s = resolveSwap('1140x1472', opts('1140x1472'), { allowCustom: true });
		expect(s.enabled).toBe(false);
	});

	it('rejects a custom mirror that breaks the max dimension', () => {
		const s = resolveSwap('8200x1024', opts('8200x1024'), { allowCustom: true });
		expect(s.enabled).toBe(false);
	});

	it('rejects a custom mirror over the pixel limit but still takes a listed one', () => {
		const over = resolveSwap('2048x1024', opts('2048x1024'), { allowCustom: true, maxPixels: 1_000_000 });
		expect(over.enabled).toBe(false);
		const listed = resolveSwap('2048x1024', opts('2048x1024', '1024x2048'), { allowCustom: true, maxPixels: 1_000_000 });
		expect(listed.enabled).toBe(true);
	});

	it('finds the mirror across tier groups', () => {
		const grouped = [
			{ value: '1344x768', tier: 'Standard' },
			{ value: '768x1344', tier: 'Portrait' }
		];
		const s = resolveSwap('1344x768', grouped, { allowCustom: false });
		expect(s).toMatchObject({ enabled: true, target: '768x1344' });
	});
});
