import { describe, expect, it } from 'vitest';
import { drawSizeChoices, penForBackground, readDrawConfig } from './drawConfig';

describe('readDrawConfig', () => {
	it('reads the editor.draw block a preset declares', () => {
		const parsed = readDrawConfig({
			editor: { draw: { size: [1328, 1328], background: 'black', pen: '#ffffff' } }
		});
		expect(parsed.size).toEqual([1328, 1328]);
		expect(parsed.background).toBe('black');
		expect(parsed.pen).toBe('#ffffff');
	});

	it('also finds the block under configuration', () => {
		const parsed = readDrawConfig({
			configuration: { editor: { draw: { background: 'transparent' } } }
		});
		expect(parsed.background).toBe('transparent');
		expect(parsed.size).toBeNull();
	});

	it('ignores out-of-range sizes, unknown backgrounds and malformed pens', () => {
		const parsed = readDrawConfig({
			editor: { draw: { size: [8, 99999], background: 'pink', pen: 'red' } }
		});
		expect(parsed.size).toBeNull();
		expect(parsed.background).toBeUndefined();
		expect(parsed.pen).toBeNull();
	});

	it('copes with missing or non-object config', () => {
		expect(readDrawConfig(null).size).toBeNull();
		expect(readDrawConfig('x').background).toBeUndefined();
	});
});

describe('drawSizeChoices', () => {
	it('leads with the preset size and drops the duplicate stock entry', () => {
		const choices = drawSizeChoices([1024, 1024]);
		expect(choices[0].label).toBe('Preset · 1024 × 1024');
		expect(choices.filter((c) => c.width === 1024 && c.height === 1024)).toHaveLength(1);
	});

	it('offers stock sizes when no preset size is declared', () => {
		expect(drawSizeChoices(null).map((c) => c.label)).toContain('768 × 512');
	});
});

describe('penForBackground', () => {
	it('contrasts with the background unless the preset chose a pen', () => {
		expect(penForBackground('white', null)).toBe('#111111');
		expect(penForBackground('black', null)).toBe('#ffffff');
		expect(penForBackground('black', '#00ff00')).toBe('#00ff00');
	});
});
