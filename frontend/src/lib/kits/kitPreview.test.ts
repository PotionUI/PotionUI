import { describe, expect, it } from 'vitest';
import { itemAspect, layoutPreview, visualItems } from './kitPreview';
import type { KitItem } from './types';

function item(id: string, width: number | null, height: number | null, kind: KitItem['kind'] = 'image'): KitItem {
	return {
		id, kit_id: 'k', kind, name: id, position: 0, media_ref: null, text: null, url: `/${id}.png`,
		thumbnail_url: kind === 'text' || kind === 'audio' ? null : `/${id}.png`, width, height,
		duration_seconds: null, missing: false, created_at: ''
	};
}

describe('visualItems', () => {
	it('keeps images and videos with a picture, drops text, audio and missing ones, caps at four', () => {
		const missing = { ...item('m', 10, 10), missing: true };
		const list = [item('a', 1, 1), item('t', null, null, 'text'), item('au', null, null, 'audio'), missing, item('v', 16, 9, 'video'), item('b', 1, 1), item('c', 1, 1), item('d', 1, 1)];
		expect(visualItems(list).map((entry) => entry.id)).toEqual(['a', 'v', 'b', 'c']);
	});
});

describe('itemAspect', () => {
	it('uses the payload size, then a measured size, then square', () => {
		expect(itemAspect(item('a', 200, 100))).toBe(2);
		expect(itemAspect(item('a', null, null), { a: 0.5 })).toBe(0.5);
		expect(itemAspect(item('a', null, null))).toBe(1);
	});
});

describe('layoutPreview', () => {
	const area = (layout: ReturnType<typeof layoutPreview>) => layout.rows.flatMap((row) => row.tiles).reduce((sum, tile) => sum + tile.width * tile.height, 0);

	it('keeps every tile at its own aspect ratio', () => {
		const layout = layoutPreview([item('p', 2, 3), item('l', 3, 2), item('s', 1, 1)], 300);
		const wanted: Record<string, number> = { p: 2 / 3, l: 3 / 2, s: 1 };
		for (const tile of layout.rows.flatMap((row) => row.tiles)) expect(tile.width / tile.height).toBeCloseTo(wanted[tile.item.id], 1);
	});

	it('never exceeds the card width or the preview height', () => {
		for (const set of [
			[item('a', 400, 900), item('b', 900, 500), item('c', 600, 600)],
			[item('a', 1600, 600), item('b', 1200, 700)],
			[item('a', 600, 900), item('b', 900, 600), item('c', 700, 700), item('d', 500, 900)]
		]) {
			const layout = layoutPreview(set, 300);
			expect(layout.height).toBeLessThanOrEqual(225);
			for (const row of layout.rows) expect(row.tiles.reduce((sum, tile) => sum + tile.width, 0) + 2 * (row.tiles.length - 1)).toBeLessThanOrEqual(300);
		}
	});

	it('stacks two wide pictures so both stay large instead of a thin strip', () => {
		const layout = layoutPreview([item('a', 1600, 600), item('b', 1200, 700)], 300);
		expect(layout.rows).toHaveLength(2);
		expect(area(layout)).toBeGreaterThan(2 * 300 * 70);
	});

	it('fits a lone tall picture inside the preview height', () => {
		const layout = layoutPreview([item('p', 1, 2)], 300);
		expect(layout.height).toBeLessThanOrEqual(225);
		expect(layout.rows[0].tiles[0].width).toBeLessThan(300);
	});

	it('returns nothing without a width or without pictures', () => {
		expect(layoutPreview([], 300).rows).toEqual([]);
		expect(layoutPreview([item('a', 1, 1)], 0).rows).toEqual([]);
	});
});
