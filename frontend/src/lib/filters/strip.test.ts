import { describe, expect, it } from 'vitest';
import { buildStrip, isLocked, lockReason, moveSelection, selectableOrder } from './strip';
import type { FilterItem } from './types';

function make(overrides: Partial<FilterItem> & { id: string }): FilterItem {
	return {
		name: overrides.id,
		group: 'Colour',
		order: 10,
		intensity: 100,
		source: 'builtin',
		owned: false,
		has_lut: false,
		steps: [],
		unavailable_ops: [],
		needs_plugin: null,
		revision: 'r',
		...overrides
	};
}

const ITEMS: FilterItem[] = [
	make({ id: 'mine-b', name: 'Zed', source: 'mine', owned: true, group: 'Mine' }),
	make({ id: 'noir', name: 'Noir', group: 'Black & white', order: 10 }),
	make({ id: 'plug', name: 'Neon', source: 'plugin', plugin_id: 'crt' }),
	make({ id: 'ember', name: 'Ember', order: 20 }),
	make({ id: 'local-one', name: 'Harbor', source: 'local', group: 'Colour' }),
	make({ id: 'matte', name: 'Matte', group: 'Film', order: 10 }),
	make({ id: 'pop', name: 'Pop', order: 10 }),
	make({ id: 'mine-a', name: 'Alpha', source: 'mine', owned: true, group: 'Mine' }),
	make({ id: 'honey', name: 'Honey', order: 20 })
];

describe('buildStrip', () => {
	it('orders built-in groups by the catalog, then local, plugin and Mine', () => {
		const sections = buildStrip(ITEMS, ['Colour', 'Film', 'Black & white']);
		expect(sections.map((section) => section.key)).toEqual([
			'builtin:Colour',
			'builtin:Film',
			'builtin:Black & white',
			'local:Colour',
			'plugin:crt',
			'mine'
		]);
	});

	it('sorts inside a group by order, ties by name', () => {
		const sections = buildStrip(ITEMS, ['Colour', 'Film', 'Black & white']);
		expect(sections[0].items.map((item) => item.id)).toEqual(['pop', 'ember', 'honey']);
	});

	it('keeps Mine alphabetical rather than by the order field', () => {
		const mine = buildStrip(ITEMS, []).find((section) => section.key === 'mine')!;
		expect(mine.items.map((item) => item.name)).toEqual(['Alpha', 'Zed']);
	});

	it('appends groups the catalog did not list in first-seen order', () => {
		const sections = buildStrip(
			[make({ id: 'a', group: 'Zeta' }), make({ id: 'b', group: 'Alpha' })],
			[]
		);
		expect(sections.map((section) => section.label)).toEqual(['Zeta', 'Alpha']);
	});

	it('groups plugin filters per plugin and labels Mine', () => {
		const sections = buildStrip(
			[
				make({ id: 'x', source: 'plugin', plugin_id: 'b-pack' }),
				make({ id: 'y', source: 'plugin', plugin_id: 'a-pack' }),
				make({ id: 'm', source: 'mine', owned: true })
			],
			[]
		);
		expect(sections.map((section) => section.label)).toEqual(['a-pack', 'b-pack', 'Mine']);
	});

	it('returns no sections for an empty catalog', () => {
		expect(buildStrip([], [])).toEqual([]);
	});
});

describe('locking', () => {
	it('locks a filter with unavailable ops or a missing plugin and says which', () => {
		const off = make({ id: 'a', unavailable_ops: ['crt.scan'], needs_plugin: 'crt-pack' });
		expect(isLocked(off)).toBe(true);
		expect(lockReason(off)).toBe('Needs plugin "crt-pack" (not enabled)');
		expect(isLocked(make({ id: 'b', unavailable_ops: ['crt.scan'] }))).toBe(true);
		expect(isLocked(make({ id: 'c' }))).toBe(false);
	});

	it('locks a filter whose plugin op has no browser part and names the plugin', () => {
		const spec = {
			id: 'retro-tape.scanlines',
			label: 'Scanlines',
			kind: 'spatial' as const,
			source: 'plugin' as const,
			plugin_id: 'retro-tape',
			params: []
		};
		const item = make({ id: 'retro-tape:lines', steps: [{ op: 'tone' }, { op: 'retro-tape.scanlines' }] });
		const missing = { ops: { [spec.id]: spec }, colour: {}, spatial: {} };
		expect(isLocked(item, missing)).toBe(true);
		expect(lockReason(item, missing)).toBe("Needs a browser part of plugin 'retro-tape' that isn't loaded");
		expect(selectableOrder(buildStrip([item], ['Colour']), missing)).toEqual([null]);

		const loaded = { ...missing, spatial: { [spec.id]: () => {} } };
		expect(isLocked(item, loaded)).toBe(false);
		expect(lockReason(item, loaded)).toBe('');
		expect(isLocked(make({ id: 'core', steps: [{ op: 'tone' }] }), missing)).toBe(false);
	});
});

describe('keyboard order', () => {
	const sections = buildStrip(
		[
			make({ id: 'a', name: 'A' }),
			make({ id: 'locked', name: 'L', order: 15, needs_plugin: 'p' }),
			make({ id: 'b', name: 'B', order: 20 })
		],
		['Colour']
	);
	const order = selectableOrder(sections);

	it('starts with None and skips locked filters', () => {
		expect(order.map((entry) => entry?.id ?? null)).toEqual([null, 'a', 'b']);
	});

	it('moves, clamps at the ends and jumps with first and last', () => {
		expect(moveSelection(order, null, 'next')?.id).toBe('a');
		expect(moveSelection(order, 'a', 'next')?.id).toBe('b');
		expect(moveSelection(order, 'b', 'next')?.id).toBe('b');
		expect(moveSelection(order, 'a', 'previous')).toBeNull();
		expect(moveSelection(order, null, 'previous')).toBeNull();
		expect(moveSelection(order, 'a', 'last')?.id).toBe('b');
		expect(moveSelection(order, 'b', 'first')).toBeNull();
	});
});
