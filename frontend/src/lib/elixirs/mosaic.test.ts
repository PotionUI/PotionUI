import { beforeEach, expect, test } from 'vitest';
import { aspectOf, drawnFirst, forgetAspects, layoutMosaic, previewMosaic, rememberAspect, showAllLabel } from './mosaic';
import type { OutputEntry } from './types';

const ratios = [1, 1.5, 0.75, 1.78, 0.56, 2, 1.33, 1, 0.8, 1.2];

function files(count: number): OutputEntry[] {
	return Array.from({ length: count }, (_, index) => {
		const aspect = ratios[index % ratios.length];
		return { kind: 'library', item_id: `f${index}`, media_type: 'image', width: Math.round(aspect * 1000), height: 1000 };
	});
}

function rowsOf(mosaic: ReturnType<typeof layoutMosaic>) {
	const rows: (typeof mosaic.tiles)[] = [];
	let row: typeof mosaic.tiles = [];
	for (const tile of mosaic.tiles) {
		row.push(tile);
		if (tile.rowEnd) {
			rows.push(row);
			row = [];
		}
	}
	if (row.length) rows.push(row);
	return rows;
}

beforeEach(() => forgetAspects());

test('every full row fills the width with no empty space', () => {
	const mosaic = layoutMosaic(files(43), 600);
	const rows = rowsOf(mosaic);

	expect(rows.length).toBeGreaterThan(2);
	for (const row of rows.slice(0, -1)) {
		const total = row.reduce((sum, tile) => sum + tile.width, 0) + 4 * (row.length - 1);
		expect(total).toBeGreaterThan(600 - row.length - 4);
		expect(total).toBeLessThanOrEqual(600);
	}
});

test('every tile in a row shares one height and keeps its aspect ratio', () => {
	const mosaic = layoutMosaic(files(43), 600);

	for (const row of rowsOf(mosaic)) {
		expect(new Set(row.map((tile) => tile.height)).size).toBe(1);
	}
	for (const tile of mosaic.tiles) {
		const wanted = Math.min(2.6, Math.max(0.45, aspectOf(tile.entry)));
		expect(Math.abs(tile.width / tile.height - wanted)).toBeLessThan(0.05 + 1 / tile.height);
	}
});

test('the row height grows with the column width', () => {
	expect(layoutMosaic(files(20), 900).rowHeight).toBeGreaterThan(layoutMosaic(files(20), 300).rowHeight);
});

test('a preview keeps whole rows only and reports how many files it shows', () => {
	const mosaic = layoutMosaic(files(1000), 400, 3);

	expect(mosaic.rows).toBe(3);
	expect(mosaic.total).toBe(1000);
	expect(mosaic.shown).toBe(mosaic.tiles.length);
	expect(mosaic.shown).toBeLessThan(40);
	expect(mosaic.tiles.filter((tile) => tile.rowEnd)).toHaveLength(3);
});

test('an unknown width still lays out so a hidden or not yet measured card has tiles', () => {
	expect(layoutMosaic(files(6), 0).tiles).toHaveLength(6);
});

test('a measured ratio is used until the file reports its own size', () => {
	const entry: OutputEntry = { kind: 'library', item_id: 'm1', media_type: 'image' };

	expect(aspectOf(entry)).toBe(1);
	expect(rememberAspect(entry, 200, 100)).toBe(true);
	expect(rememberAspect(entry, 200, 100)).toBe(false);
	expect(aspectOf(entry)).toBe(2);
	expect(aspectOf({ ...entry, width: 50, height: 100 })).toBe(0.5);
});

test('a preview of a random pick shows the drawn files first when others are hidden', () => {
	const entries = files(200);
	const drawn = new Set(['library:f150', 'library:f199']);

	const mosaic = previewMosaic(entries, 400, drawn);

	expect(mosaic.tiles.slice(0, 2).map((tile) => tile.key)).toEqual(['library:f150', 'library:f199']);
	expect(drawnFirst(entries, null)).toBe(entries);
});

test('a preview that fits keeps the natural order', () => {
	const entries = files(4);

	const mosaic = previewMosaic(entries, 400, new Set(['library:f3']));

	expect(mosaic.tiles.map((tile) => tile.key)).toEqual(['library:f0', 'library:f1', 'library:f2', 'library:f3']);
});

test('the show-all label counts with thousands separators', () => {
	expect(showAllLabel(1000)).toBe('Show all 1,000 files');
});
