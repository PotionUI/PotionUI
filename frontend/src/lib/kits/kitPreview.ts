import { clampAspect } from '$lib/utils/justifiedLayout';
import type { KitItem } from './types';

export const PREVIEW_GAP = 2;
export const PREVIEW_MAX_ITEMS = 4;
const MAX_HEIGHT_RATIO = 0.9;

export interface PreviewTile {
	item: KitItem;
	width: number;
	height: number;
}

export interface PreviewRow {
	height: number;
	tiles: PreviewTile[];
}

export function isVisual(item: KitItem): boolean {
	if (item.missing) return false;
	if (item.kind === 'video') return !!item.thumbnail_url;
	return item.kind === 'image' && !!(item.thumbnail_url ?? item.url);
}

export function visualItems(items: readonly KitItem[]): KitItem[] {
	return items.filter(isVisual).slice(0, PREVIEW_MAX_ITEMS);
}

export function itemAspect(item: KitItem, measured: Readonly<Record<string, number>> = {}): number {
	const width = Number(item.width);
	const height = Number(item.height);
	if (width > 0 && height > 0) return width / height;
	return measured[item.id] ?? 1;
}

export interface PreviewLayout {
	height: number;
	rows: PreviewRow[];
}

export const PREVIEW_HEIGHT_RATIO = 0.75;

export function previewHeight(width: number): number {
	return Math.round(width * PREVIEW_HEIGHT_RATIO);
}

function partitions<T>(items: readonly T[]): T[][][] {
	const result: T[][][] = [];
	for (let mask = 0; mask < 1 << (items.length - 1); mask++) {
		const groups: T[][] = [[items[0]]];
		for (let i = 1; i < items.length; i++) {
			if (mask & (1 << (i - 1))) groups.push([items[i]]);
			else groups[groups.length - 1].push(items[i]);
		}
		result.push(groups);
	}
	return result;
}

function arrange(groups: KitItem[][], aspects: Map<string, number>, width: number, limit: number, gap: number): { layout: PreviewLayout; area: number } {
	const natural = groups.map((group) => {
		const sum = group.reduce((total, item) => total + aspects.get(item.id)!, 0);
		return (width - gap * (group.length - 1)) / sum;
	});
	const total = natural.reduce((sum, height) => sum + height, 0) + gap * (groups.length - 1);
	const scale = Math.min(1, (limit - gap * (groups.length - 1)) / (total - gap * (groups.length - 1)));
	let area = 0;
	const rows = groups.map((group, index) => {
		const height = Math.max(1, Math.floor(natural[index] * scale));
		const tiles = group.map((item) => ({ item, width: Math.floor(aspects.get(item.id)! * height), height }));
		for (const tile of tiles) area += tile.width * tile.height;
		return { height, tiles };
	});
	const used = rows.reduce((sum, row) => sum + row.height, 0) + gap * (rows.length - 1);
	return { layout: { height: used, rows }, area };
}

export function layoutPreview(
	items: readonly KitItem[],
	width: number,
	measured: Readonly<Record<string, number>> = {},
	gap: number = PREVIEW_GAP
): PreviewLayout {
	if (width <= 0 || items.length === 0) return { height: 0, rows: [] };
	const aspects = new Map(items.map((item) => [item.id, clampAspect(itemAspect(item, measured))]));
	const limit = previewHeight(width);
	let best: { layout: PreviewLayout; area: number } | null = null;
	for (const groups of partitions(items)) {
		const candidate = arrange(groups, aspects, width, limit, gap);
		if (!best || candidate.area > best.area * 1.02) best = candidate;
	}
	return best!.layout;
}
