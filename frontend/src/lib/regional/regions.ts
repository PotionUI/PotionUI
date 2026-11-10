import {
	MIN_CROP_FRACTION,
	clampCropRect,
	moveCropRect,
	resizeCropRect,
	type CropCorner,
	type CropRect
} from '$lib/media/editors/cropGeometry';

export interface Region {
	x: number;
	y: number;
	w: number;
	h: number;
	caption: string;
}

export interface RegionalPromptValue {
	enabled: boolean;
	items: Region[];
}

export interface RegionalCaps {
	sizeField: string;
}

export interface CanvasSize {
	width: number;
	height: number;
}

export type RegionCorner = CropCorner;

export const REGIONAL_FORM_KEY = 'regional_prompt';
export const MIN_REGION = MIN_CROP_FRACTION;
export const DRAW_THRESHOLD_PX = 5;
export const NUDGE_STEP = 0.01;
export const NUDGE_STEP_LARGE = 0.05;
export const DEFAULT_CANVAS_SIZE: CanvasSize = { width: 1024, height: 1024 };

export const EMPTY_REGIONAL: RegionalPromptValue = Object.freeze({ enabled: false, items: [] }) as RegionalPromptValue;

export function resolveRegionalCaps(
	vars: Record<string, any> | null | undefined,
	mode: string | null | undefined
): RegionalCaps | null {
	const raw = vars?.regional_prompt;
	if (!mode || !raw || typeof raw !== 'object') return null;
	const modes: unknown = raw.modes;
	if (!Array.isArray(modes) || !modes.includes(mode)) return null;
	const sizeField = typeof raw.size_field === 'string' ? raw.size_field : '';
	return { sizeField };
}

function finite(value: unknown, fallback: number): number {
	return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function unit(value: number): number {
	return Math.min(1, Math.max(0, value));
}

export function normalizeRegion(raw: unknown): Region | null {
	if (!raw || typeof raw !== 'object') return null;
	const item = raw as Record<string, unknown>;
	const rect = clampCropRect({
		x: unit(finite(item.x, 0)),
		y: unit(finite(item.y, 0)),
		width: unit(finite(item.w, 0)),
		height: unit(finite(item.h, 0))
	});
	return {
		x: rect.x,
		y: rect.y,
		w: rect.width,
		h: rect.height,
		caption: typeof item.caption === 'string' ? item.caption : ''
	};
}

export function readRegionalValue(formData: Record<string, unknown> | null | undefined): RegionalPromptValue {
	const raw = formData?.[REGIONAL_FORM_KEY];
	if (!raw || typeof raw !== 'object') return EMPTY_REGIONAL;
	const value = raw as Record<string, unknown>;
	const items = Array.isArray(value.items)
		? value.items.map(normalizeRegion).filter((region): region is Region => region !== null)
		: [];
	return { enabled: value.enabled === true, items };
}

export function parseCanvasSize(value: unknown): CanvasSize {
	if (typeof value === 'string') {
		const match = /^\s*(\d+)\s*[x×]\s*(\d+)\s*$/i.exec(value);
		if (match) {
			const width = Number(match[1]);
			const height = Number(match[2]);
			if (width > 0 && height > 0) return { width, height };
		}
	}
	return DEFAULT_CANVAS_SIZE;
}

export function formatCanvasSize(size: CanvasSize): string {
	return `${size.width}×${size.height}`;
}

export function regionIndexLabel(index: number): string {
	return String(index + 1).padStart(2, '0');
}

export function toRect(region: Region): CropRect {
	return { x: region.x, y: region.y, width: region.w, height: region.h };
}

export function fromRect(rect: CropRect, caption: string): Region {
	return { x: rect.x, y: rect.y, w: rect.width, h: rect.height, caption };
}

export function moveRegion(region: Region, dx: number, dy: number): Region {
	return fromRect(moveCropRect(toRect(region), dx, dy), region.caption);
}

export function resizeRegion(region: Region, corner: RegionCorner, dx: number, dy: number): Region {
	return fromRect(resizeCropRect(toRect(region), corner, dx, dy, { ratio: null, stageAspect: 1 }), region.caption);
}

export function drawRegion(a: { x: number; y: number }, b: { x: number; y: number }): Region | null {
	const x1 = unit(Math.min(a.x, b.x));
	const y1 = unit(Math.min(a.y, b.y));
	const x2 = unit(Math.max(a.x, b.x));
	const y2 = unit(Math.max(a.y, b.y));
	if (x2 - x1 < MIN_REGION || y2 - y1 < MIN_REGION) return null;
	return { x: x1, y: y1, w: x2 - x1, h: y2 - y1, caption: '' };
}

export function draftRect(a: { x: number; y: number }, b: { x: number; y: number }): CropRect {
	const x1 = unit(Math.min(a.x, b.x));
	const y1 = unit(Math.min(a.y, b.y));
	return { x: x1, y: y1, width: unit(Math.max(a.x, b.x)) - x1, height: unit(Math.max(a.y, b.y)) - y1 };
}

const NUDGE_KEYS: Record<string, [number, number]> = {
	ArrowLeft: [-1, 0],
	ArrowRight: [1, 0],
	ArrowUp: [0, -1],
	ArrowDown: [0, 1]
};

export function nudgeRegion(region: Region, key: string, large: boolean): Region | null {
	const dir = NUDGE_KEYS[key];
	if (!dir) return null;
	const step = large ? NUDGE_STEP_LARGE : NUDGE_STEP;
	return moveRegion(region, dir[0] * step, dir[1] * step);
}

export function removeRegionAt(items: Region[], index: number): Region[] {
	return items.filter((_, i) => i !== index);
}

export function selectionAfterRemove(count: number, removed: number, selected: number | null): number | null {
	if (selected === null) return null;
	if (selected === removed) return null;
	if (selected > removed) return Math.min(selected - 1, count - 2);
	return selected;
}

export function replaceRegionAt(items: Region[], index: number, region: Region): Region[] {
	return items.map((item, i) => (i === index ? region : item));
}

export function regionalSwitchCount(value: RegionalPromptValue): number | undefined {
	return value.enabled && value.items.length > 0 ? value.items.length : undefined;
}

export function tagAbove(region: Region, canvasHeightPx: number): boolean {
	return region.h * canvasHeightPx < 32 && region.y * canvasHeightPx > 28;
}

export function moveRegionTo(items: Region[], from: number, to: number): Region[] {
	if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
	const next = items.slice();
	const [moved] = next.splice(from, 1);
	next.splice(to, 0, moved);
	return next;
}

export function selectionAfterMove(selected: number | null, from: number, to: number): number | null {
	if (selected === null) return null;
	if (selected === from) return to;
	if (from < to && selected > from && selected <= to) return selected - 1;
	if (from > to && selected >= to && selected < from) return selected + 1;
	return selected;
}

export function regionalFormDataFromResult(
	formData: Record<string, unknown> | null | undefined,
	caps: RegionalCaps | null,
	result: unknown
): Record<string, unknown> | null {
	if (!caps || !result || typeof result !== 'object') return null;
	const raw = result as Record<string, unknown>;
	const items = Array.isArray(raw.items)
		? raw.items.map(normalizeRegion).filter((region): region is Region => region !== null)
		: [];
	return { ...(formData ?? {}), [REGIONAL_FORM_KEY]: { enabled: raw.enabled === true, items } };
}
