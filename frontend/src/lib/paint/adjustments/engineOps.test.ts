import { beforeEach, describe, expect, it, vi } from 'vitest';

type Buffer = { width: number; height: number; data: Uint8ClampedArray };
const renderRecipe = vi.fn((buffer: Buffer, _recipe?: { steps: unknown[] }, _intensity?: number) => buffer);

vi.mock('$lib/filters/render', () => ({
	renderRecipe: (buffer: Buffer, recipe: { steps: unknown[] }, intensity: number) =>
		renderRecipe(buffer, recipe, intensity)
}));

const { ENGINE_ADJUSTMENTS } = await import('./engineOps');
const { BUILTIN_FILTERS, defaultFilterValues } = await import('./builtin');

const IMAGE = { width: 1, height: 1, data: new Uint8ClampedArray([10, 20, 30, 255]) };

function find(id: string) {
	const filter = ENGINE_ADJUSTMENTS.find((candidate) => candidate.id === id);
	if (!filter) throw new Error(`missing ${id}`);
	return filter;
}

beforeEach(() => renderRecipe.mockClear());

describe('engine-backed adjustments', () => {
	it('registers the new adjustments next to tone, invert and grayscale', () => {
		const ids = BUILTIN_FILTERS.map((filter) => filter.id);
		expect(ids).toEqual(
			expect.arrayContaining([
				'tone',
				'exposure',
				'white_balance',
				'curves',
				'vibrance',
				'split_tone',
				'fade',
				'vignette',
				'grain',
				'invert',
				'grayscale'
			])
		);
	});

	it('exposes slider params from the op catalogue', () => {
		const exposure = find('exposure');
		expect(exposure.params).toEqual([
			{ id: 'stops', label: 'Stops', min: -2, max: 2, value: 0, unit: undefined, step: 0.1 }
		]);
		expect(find('split_tone').params.find((param) => param.id === 'shadow_hue')?.unit).toBe('°');
		expect(find('vignette').params.find((param) => param.id === 'midpoint')?.value).toBe(50);
	});

	it('is inactive at its defaults and active once a param moves', () => {
		const white = find('white_balance');
		expect(white.active(defaultFilterValues(white))).toBe(false);
		expect(white.active({ temperature: 12, tint: 0 })).toBe(true);
		const vignette = find('vignette');
		expect(vignette.active(defaultFilterValues(vignette))).toBe(false);
		expect(vignette.active({ amount: 0, midpoint: 50, feather: 70 })).toBe(true);
	});

	it('renders through the engine as a single step at full strength', async () => {
		await find('exposure').apply(IMAGE, { stops: 1.5 });
		expect(renderRecipe).toHaveBeenCalledTimes(1);
		const [, recipe, intensity] = renderRecipe.mock.calls[0];
		expect(recipe!.steps).toEqual([{ op: 'exposure', stops: 1.5 }]);
		expect(intensity).toBe(100);
	});

	it('fills omitted values with the op defaults', async () => {
		await find('vignette').apply(IMAGE, { amount: 30 });
		const recipe = renderRecipe.mock.calls[0][1]!;
		expect(recipe.steps).toEqual([{ op: 'vignette', amount: 30, midpoint: 50, feather: 60 }]);
	});

	it('curves is a read-only plot with no sliders and never runs', () => {
		const curves = find('curves');
		expect(curves.plot).toBe(true);
		expect(curves.params).toEqual([]);
		expect(curves.active({})).toBe(false);
	});
});
