import { writable, type Readable } from 'svelte/store';
import type { ExpandFormat } from '$lib/services/api/editorTasks';
import type { ExpandParams } from './generateStep';

export const MAX_SIDE = 8192;
export const MAX_PIXELS = 36_000_000;
export const CUSTOM_ID = 'custom';
export const MAX_PICKS = 6;

export interface Size {
	width: number;
	height: number;
}

export interface Anchor {
	x: number;
	y: number;
}

export interface ExpandTarget extends Size {
	id: string;
	label: string;
	x: number;
	y: number;
	ratio: [number, number] | null;
	anchor: Anchor;
}

export interface ExpandUi {
	formats: ExpandFormat[];
	loaded: boolean;
	picks: string[];
	custom: Size;
	anchor: Anchor;
	source: Size | null;
}

export const CENTER: Anchor = { x: 0.5, y: 0.5 };

export function parseRatio(value: string): [number, number] | null {
	const parts = value.split(':').map(Number);
	if (parts.length !== 2 || !parts.every((part) => Number.isFinite(part) && part > 0)) return null;
	return [parts[0], parts[1]];
}

export function containSize(source: Size, ratio: readonly [number, number]): Size {
	const wanted = ratio[0] / ratio[1];
	if (source.width / source.height < wanted) return { width: Math.max(source.width, Math.ceil(source.height * wanted)), height: source.height };
	return { width: source.width, height: Math.max(source.height, Math.ceil(source.width / wanted)) };
}

export function atLeast(source: Size, size: Size): Size {
	return { width: Math.max(source.width, Math.round(size.width)), height: Math.max(source.height, Math.round(size.height)) };
}

export function withinLimits(size: Size): boolean {
	return size.width <= MAX_SIDE && size.height <= MAX_SIDE && size.width * size.height <= MAX_PIXELS;
}

export function placement(source: Size, target: Size, anchor: Anchor): { x: number; y: number } {
	return {
		x: Math.min(target.width - source.width, Math.max(0, Math.round((target.width - source.width) * anchor.x))),
		y: Math.min(target.height - source.height, Math.max(0, Math.round((target.height - source.height) * anchor.y)))
	};
}

export function anchorFor(source: Size, target: Size, offset: { x: number; y: number }): Anchor {
	const roomX = target.width - source.width;
	const roomY = target.height - source.height;
	return { x: roomX > 0 ? Math.min(1, Math.max(0, offset.x / roomX)) : 0.5, y: roomY > 0 ? Math.min(1, Math.max(0, offset.y / roomY)) : 0.5 };
}

export function sameShape(source: Size, target: Size): boolean {
	return source.width === target.width && source.height === target.height;
}

export function targetFor(source: Size, id: string, label: string, ratio: readonly [number, number] | null, custom: Size, anchor: Anchor): ExpandTarget | null {
	const size = ratio ? containSize(source, ratio) : atLeast(source, custom);
	if (sameShape(source, size) || !withinLimits(size)) return null;
	return { id, label, ...size, ...placement(source, size, anchor), ratio: ratio ? [ratio[0], ratio[1]] : null, anchor };
}

export function targetsOf(ui: ExpandUi, from: Size | null = ui.source): ExpandTarget[] {
	const source = from;
	if (!source) return [];
	const found: ExpandTarget[] = [];
	for (const id of ui.picks) {
		if (id === CUSTOM_ID) {
			const target = targetFor(source, CUSTOM_ID, 'Custom', null, ui.custom, ui.anchor);
			if (target) found.push(target);
			continue;
		}
		const format = ui.formats.find((entry) => entry.id === id);
		const ratio = format ? parseRatio(format.ratio) : null;
		const target = format && ratio ? targetFor(source, format.id, format.label, ratio, ui.custom, ui.anchor) : null;
		if (target) found.push(target);
	}
	return found;
}

export function retarget(expand: ExpandParams, source: Size): ExpandParams | null {
	const target = targetFor(source, '', expand.label, expand.ratio, { width: expand.width, height: expand.height }, expand.anchor);
	return target ? { width: target.width, height: target.height, x: target.x, y: target.y, label: expand.label, ratio: expand.ratio, anchor: expand.anchor } : null;
}

export function paramsOf(target: ExpandTarget): ExpandParams {
	return { width: target.width, height: target.height, x: target.x, y: target.y, label: target.label, ratio: target.ratio, anchor: target.anchor };
}

export function formatName(target: Pick<ExpandTarget, 'label' | 'width' | 'height'>): string {
	return `${target.label} ${target.width}×${target.height}`;
}

export function passesFor(source: Size, target: Size, growth = 0.8): number {
	let width = source.width;
	let height = source.height;
	let count = 0;
	while ((width < target.width || height < target.height) && count < 32) {
		width = Math.min(target.width, width + Math.max(64, Math.ceil(growth * width)));
		height = Math.min(target.height, height + Math.max(64, Math.ceil(growth * height)));
		count += 1;
	}
	return count;
}

function freshUi(): ExpandUi {
	return { formats: [], loaded: false, picks: [], custom: { width: 1024, height: 1024 }, anchor: { ...CENTER }, source: null };
}

const state = writable<ExpandUi>(freshUi());

export const expandUi: Readable<ExpandUi> & {
	set(next: ExpandUi): void;
	update(change: (current: ExpandUi) => ExpandUi): void;
	reset(): void;
} = {
	subscribe: state.subscribe,
	set: state.set,
	update: state.update,
	reset: () => state.set(freshUi())
};

export function togglePick(ui: ExpandUi, id: string): ExpandUi {
	if (ui.picks.includes(id)) return { ...ui, picks: ui.picks.filter((entry) => entry !== id) };
	if (ui.picks.length >= MAX_PICKS) return ui;
	return { ...ui, picks: [...ui.picks, id] };
}
