import { describe, expect, it } from 'vitest';
import { makeItem, makeKit } from './fixtures';
import { kitFieldsOf, loadedKits } from './promptKits';
import { shotKitUsage } from './shotKits';
import { encodeKitKey } from './kitMarker';

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face'), makeItem('image', 'hat'), makeItem('text', 'look')]);
const loaded = loadedKits(kitFieldsOf([{ field: 'refs', kind: 'image' }], { refs: [{ kit: mara.id }] }), new Map([[mara.id, mara]]));
const cite = (itemId?: string) => `@[refs:${encodeKitKey(mara.id, itemId)}]`;

describe('shotKitUsage', () => {
	it('puts a cited item in used with how many times, and the rest in unused', () => {
		const { used, unused } = shotKitUsage([`${cite(mara.items[0].id)} and ${cite(mara.items[0].id)}`], loaded);
		expect(used.map((entry) => [entry.item.name, entry.count])).toEqual([['face', 2]]);
		expect(unused.map((entry) => entry.item.name)).toEqual(['hat', 'look']);
		expect(used[0].path).toBe('@kits.mara.face');
		expect(unused[0].marker).toBe(`@[refs:kit:${mara.id}/${mara.items[1].id}]`);
	});

	it('counts a bare kit as its media items, not its text', () => {
		const { used, unused } = shotKitUsage([cite()], loaded);
		expect(used.map((entry) => entry.item.name)).toEqual(['face', 'hat']);
		expect(unused.map((entry) => entry.item.name)).toEqual(['look']);
	});

	it('lists everything as unused when nothing is cited', () => {
		expect(shotKitUsage(['plain'], loaded).used).toEqual([]);
	});
});
