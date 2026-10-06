import { describe, expect, it } from 'vitest';
import type { PaintFilter } from '$lib/components/imageEditor/types';
import type { OpSpec } from './engine';
import { buildExtensions, sharedExtensions } from './extensions';
import { renderRecipe } from './render';

function op(id: string, kind: 'colour' | 'spatial'): OpSpec {
	return {
		id,
		label: id,
		kind,
		source: 'plugin',
		plugin_id: id.split('.')[0],
		params: [{ id: 'strength', label: 'Strength', type: 'int', min: 0, max: 100, default: 100, unit: null }]
	};
}

function adjustment(id: string, extra: Partial<PaintFilter>): PaintFilter {
	return { id, label: id, params: [], active: () => true, apply: (image) => image, ...extra };
}

const pixel = () => ({ width: 1, height: 1, data: new Uint8ClampedArray([100, 100, 100, 255]) });

describe('plugin op extensions', () => {
	it('folds a colour op map into the LUT', () => {
		const extensions = buildExtensions(
			[op('tape.zero_red', 'colour')],
			[adjustment('tape.zero_red', { map: ([, g, b]) => [0, g, b] })]
		);
		const output = renderRecipe(pixel(), { steps: [{ op: 'tape.zero_red' }] }, 100, { extensions });
		expect(Array.from(output.data)).toEqual([0, 100, 100, 255]);
	});

	it('blends a spatial op apply by the intensity', () => {
		const extensions = buildExtensions(
			[op('tape.black', 'spatial')],
			[
				adjustment('tape.black', {
					apply: (image) => ({ ...image, data: new Uint8ClampedArray([0, 0, 0, 255]) })
				})
			]
		);
		const output = renderRecipe(pixel(), { steps: [{ op: 'tape.black' }] }, 50, { extensions });
		expect(Array.from(output.data)).toEqual([50, 50, 50, 255]);
	});

	it('refuses a plugin op the browser has no implementation for', () => {
		const extensions = buildExtensions([op('tape.missing', 'colour')], []);
		expect(() => renderRecipe(pixel(), { steps: [{ op: 'tape.missing' }] }, 100, { extensions })).toThrow(
			/tape\.missing/
		);
	});

	it('ignores core ops and reuses the result for the same inputs', () => {
		const core = { ...op('tone', 'colour'), source: 'core' as const };
		const ops = [core, op('tape.black', 'spatial')];
		const adjustments = [adjustment('tape.black', {})];
		const first = sharedExtensions(ops, adjustments);
		expect(Object.keys(first.ops ?? {})).toEqual(['tape.black']);
		expect(sharedExtensions(ops, [...adjustments])).toBe(first);
		expect(sharedExtensions([...ops], adjustments)).not.toBe(first);
	});
});
