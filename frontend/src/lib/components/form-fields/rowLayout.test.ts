import { describe, expect, it } from 'vitest';
import { parseFieldWidth, resolveRowLayout } from './rowLayout';
import { SECTION_WELL_INSET } from './rowInset';

describe('resolveRowLayout', () => {
	it('uses the 300px default collapse boundary', () => {
		expect(resolveRowLayout({ columns: 2 }, 299)).toMatchObject({ collapseAt: 300, activeColumns: 1 });
		expect(resolveRowLayout({ columns: 2 }, 300)).toMatchObject({ collapseAt: 300, activeColumns: 2 });
	});

	it.each([
		['configuration collapse_at', { configuration: { columns: 3, collapse_at: 700 } }],
		['configuration collapseAt', { configuration: { columns: 3, collapseAt: 700 } }],
		['root collapse_at', { columns: 3, collapse_at: 700 }],
		['root collapseAt', { columns: 3, collapseAt: 700 }]
	])('keeps the explicit %s override', (_label, config) => {
		expect(resolveRowLayout(config, 699)).toMatchObject({ collapseAt: 700, activeColumns: 1 });
		expect(resolveRowLayout(config, 700)).toMatchObject({ collapseAt: 700, activeColumns: 3 });
	});

	it('keeps configuration overrides ahead of root overrides', () => {
		const config = { columns: 3, collapseAt: 700, configuration: { collapse_at: 680 } };
		expect(resolveRowLayout(config, 680)).toMatchObject({ collapseAt: 680, activeColumns: 3 });
	});
});

describe('parseFieldWidth', () => {
	it('reads a positive number as-is', () => {
		expect(parseFieldWidth(3)).toBe(3);
		expect(parseFieldWidth(0.6)).toBe(0.6);
	});

	it('reads a string fraction as a/b', () => {
		expect(parseFieldWidth('3/5')).toBeCloseTo(0.6);
		expect(parseFieldWidth('2/5')).toBeCloseTo(0.4);
	});

	it.each([
		['zero', 0],
		['negative', -1],
		['non-numeric string', 'abc'],
		['zero denominator', '3/0'],
		['empty string', ''],
		['null', null],
		['undefined', undefined]
	])('falls back to weight 1 for %s', (_label, width) => {
		expect(parseFieldWidth(width)).toBe(1);
	});
});

describe('resolveRowLayout weighted columns', () => {
	it('renders the exact current repeat() string when no child declares a width', () => {
		const config = { columns: 2, children: [{ name: 'seed' }, { name: 'images' }] };
		expect(resolveRowLayout(config, 400)).toMatchObject({
			gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
			weighted: false
		});
	});

	it('builds weighted tracks from string fractions (seed 3/5 + images 2/5)', () => {
		const config = {
			columns: 2,
			children: [
				{ name: 'seed', width: '3/5' },
				{ name: 'images', width: '2/5' }
			]
		};
		expect(resolveRowLayout(config, 400)).toMatchObject({
			gridTemplateColumns: 'minmax(0, 0.6fr) minmax(0, 0.4fr)',
			weighted: true
		});
	});

	it('produces the same ratio from numeric weights', () => {
		const config = {
			columns: 2,
			children: [
				{ name: 'seed', width: 3 },
				{ name: 'images', width: 2 }
			]
		};
		const layout = resolveRowLayout(config, 400);
		expect(layout.gridTemplateColumns).toBe('minmax(0, 3fr) minmax(0, 2fr)');
		expect(layout.weighted).toBe(true);
	});

	it('mixes a declared width with a default weight of 1', () => {
		const config = {
			columns: 2,
			children: [{ name: 'seed', width: 3 }, { name: 'images' }]
		};
		expect(resolveRowLayout(config, 400)).toMatchObject({
			gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 1fr)',
			weighted: true
		});
	});

	it('treats invalid widths as weight 1, still triggering weighted mode alongside a valid sibling', () => {
		const config = {
			columns: 2,
			children: [
				{ name: 'seed', width: 3 },
				{ name: 'images', width: 'abc' }
			]
		};
		expect(resolveRowLayout(config, 400)).toMatchObject({
			gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 1fr)',
			weighted: true
		});
	});

	it('keeps weighted tracks free to shrink below min-content (Krea-2 seed 4/5 + stepper 1/5 repro)', () => {
		// 348px pane content - 10px gap-2.5 = 338px available for the two tracks
		// (see the row-layout.spec.ts journey comment for the pane-width derivation).
		// Tracks are `minmax(0, Xfr)`, not `minmax(min-content, Xfr)`: a wide
		// sibling's min-content no longer locks its own track and squeezes the
		// rest to their bare min-content. Overflow inside a track is instead
		// each field's own responsibility (RowField.svelte's `min-width: 0` +
		// truncation on unshrinkable content).
		const config = {
			columns: 2,
			children: [
				{ name: 'seed', width: '4/5' },
				{ name: 'quantity', type: 'stepper', width: '1/5' }
			]
		};
		expect(resolveRowLayout(config, 338)).toMatchObject({
			gridTemplateColumns: 'minmax(0, 0.8fr) minmax(0, 0.2fr)',
			weighted: true
		});
	});

	it('ignores weights entirely and stacks to one column when collapsed', () => {
		const config = {
			columns: 2,
			collapseAt: 300,
			children: [
				{ name: 'seed', width: '3/5' },
				{ name: 'images', width: '2/5' }
			]
		};
		expect(resolveRowLayout(config, 299)).toMatchObject({
			activeColumns: 1,
			gridTemplateColumns: 'repeat(1, minmax(0, 1fr))',
			weighted: false
		});
	});

	it('stacks to one column when containerWidth is 0 (pre-measure)', () => {
		const config = {
			columns: 2,
			children: [
				{ name: 'seed', width: '3/5' },
				{ name: 'images', width: '2/5' }
			]
		};
		expect(resolveRowLayout(config, 0)).toMatchObject({
			activeColumns: 1,
			gridTemplateColumns: 'repeat(1, minmax(0, 1fr))',
			weighted: false
		});
	});

	it('flips collapse when a section well inset (RowField.svelte) narrows the width RowField sees (338px pane at a 320px collapse_at)', () => {
		const config = { columns: 2, collapseAt: 320, children: [{ name: 'seed' }, { name: 'images' }] };
		const paneWidth = 338;
		// Row rendered at the pane's full width: still above the boundary.
		expect(resolveRowLayout(config, paneWidth)).toMatchObject({ activeColumns: 2 });
		// Same row nested one section deep: the well's inset (see
		// RowField.svelte's `inset` subtraction) drops it below the boundary.
		expect(resolveRowLayout(config, paneWidth - SECTION_WELL_INSET)).toMatchObject({ activeColumns: 1 });
	});

	it('caps the track count and reports it when a row has more children than the sane upper bound', () => {
		const config = {
			columns: 2,
			children: Array.from({ length: 8 }, (_, i) => ({ name: `f${i}`, width: 1 }))
		};
		const layout = resolveRowLayout(config, 400);
		expect(layout.trackCountCapped).toBe(true);
		expect(layout.gridTemplateColumns.match(/minmax/g)?.length).toBe(6);
	});
});
