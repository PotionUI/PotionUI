import { flattenJustifiedRows, layoutJustifiedRows } from '$lib/utils/justifiedLayout';
import type { OutputEntry } from './types';
import { tileKey } from './tiles';

export const MOSAIC_GAP = 4;
export const FALLBACK_WIDTH = 320;
export const CARD_ROWS = 3;
export const DRAWER_PAGE_SIZE = 24;

export interface MosaicTile {
	entry: OutputEntry;
	index: number;
	key: string;
	width: number;
	height: number;
	rowEnd: boolean;
}

export interface Mosaic {
	entries: OutputEntry[];
	tiles: MosaicTile[];
	rowHeight: number;
	rows: number;
	total: number;
	shown: number;
}

export function mosaicRowHeight(width: number): number {
	return Math.min(220, Math.max(64, Math.round(width / 3.4)));
}

const measured = new Map<string, number>();

export function rememberAspect(entry: OutputEntry, width: number, height: number): boolean {
	if (!(width > 0 && height > 0)) return false;
	const key = tileKey(entry);
	const aspect = width / height;
	const known = measured.get(key);
	if (known !== undefined && Math.abs(known - aspect) < 0.001) return false;
	measured.set(key, aspect);
	return true;
}

export function forgetAspects(): void {
	measured.clear();
}

export function aspectOf(entry: OutputEntry): number {
	const width = Number(entry.width);
	const height = Number(entry.height);
	if (width > 0 && height > 0) return width / height;
	return measured.get(tileKey(entry)) ?? 1;
}

export function layoutMosaic(entries: OutputEntry[], width: number, maxRows: number = Infinity, gap: number = MOSAIC_GAP): Mosaic {
	const usable = width > 0 ? width : FALLBACK_WIDTH;
	const rowHeight = mosaicRowHeight(usable);
	const items = entries.map((entry, index) => ({ item: { entry, index }, aspect: aspectOf(entry) }));
	const rows = layoutJustifiedRows(items, usable, rowHeight, gap);
	const kept = rows.slice(0, Math.max(0, maxRows));
	const tiles = flattenJustifiedRows(kept).map((box) => ({
		entry: box.item.entry,
		index: box.item.index,
		key: tileKey(box.item.entry),
		width: box.width,
		height: box.height,
		rowEnd: box.rowEnd
	}));
	return { entries, tiles, rowHeight, rows: kept.length, total: entries.length, shown: tiles.length };
}

export function drawnFirst(entries: OutputEntry[], drawn: Set<string> | null | undefined): OutputEntry[] {
	if (!drawn) return entries;
	const picked = entries.filter((entry) => drawn.has(tileKey(entry)));
	const rest = entries.filter((entry) => !drawn.has(tileKey(entry)));
	return [...picked, ...rest];
}

export function previewMosaic(entries: OutputEntry[], width: number, drawn: Set<string> | null | undefined, maxRows: number = CARD_ROWS): Mosaic {
	const natural = layoutMosaic(entries, width, maxRows);
	if (!drawn || natural.shown >= entries.length) return natural;
	return layoutMosaic(drawnFirst(entries, drawn), width, maxRows);
}

export function showAllLabel(total: number): string {
	return `Show all ${total.toLocaleString('en-US')} files`;
}
