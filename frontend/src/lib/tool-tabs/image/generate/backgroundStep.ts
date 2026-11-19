import { normalizeHex } from '$lib/components/form-fields/colorValue';
import { canvasFromUrl } from '$lib/paint/loadPicked';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import type { MediaToolStep } from '$lib/media-tools/types';
import type { StepKind } from '$lib/editors/stepKinds';
import { opacityOf, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { MediaRef } from '$lib/types/tabs';

export const REPLACE_BACKGROUND_TASK = 'replace_background';
export const REPLACE_BACKGROUND_TOOL = 'replace_background';
export const REPLACE_BACKGROUND_VERSION = 1;
export const FIND_SUBJECT_TASK = 'find_subject';
export const REPLACE_BACKGROUND_PROMPT = 'Keep the subject exactly as it is, do not change it. Replace everything marked with this background: {prompt}';
export const DEFAULT_BACKGROUND_COLOR = '#FFFFFF';

export type BackgroundMode = 'describe' | 'picture' | 'color' | 'transparent';
export type BackgroundKind = 'color' | 'picture' | 'transparent';

export interface BackgroundPicture {
	item_id: string;
	url: string | null;
	name: string | null;
}

export interface BackgroundMatte {
	item_id: string;
	url: string | null;
}

export interface ReplaceBackgroundParams {
	v: number;
	kind: BackgroundKind;
	color: string | null;
	picture: BackgroundPicture | null;
	matte: BackgroundMatte;
}

export interface BackgroundChoice {
	mode: BackgroundMode;
	color: string;
	picture: BackgroundPicture | null;
}

export const BACKGROUND_MODES: ReadonlyArray<{ id: BackgroundMode; label: string }> = [
	{ id: 'describe', label: 'Describe' },
	{ id: 'picture', label: 'Picture' },
	{ id: 'color', label: 'Colour' },
	{ id: 'transparent', label: 'Transparent' }
];

export const NEEDS_SUBJECT_REASON = 'Find the subject or mark the background first';
export const NEEDS_PICTURE_REASON = 'Choose a picture first';

export function initialBackground(): BackgroundChoice {
	return { mode: 'describe', color: DEFAULT_BACKGROUND_COLOR, picture: null };
}

export function isDirectMode(taskId: string | null | undefined, mode: BackgroundMode): boolean {
	return taskId === REPLACE_BACKGROUND_TASK && mode !== 'describe';
}

export function directBlockedReason(choice: BackgroundChoice, hasMark: boolean): string | null {
	if (!hasMark) return NEEDS_SUBJECT_REASON;
	if (choice.mode === 'picture' && !choice.picture) return NEEDS_PICTURE_REASON;
	return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
	return typeof value === 'string' && value ? value : null;
}

export function readReplaceBackgroundParams(raw: unknown): ReplaceBackgroundParams | null {
	if (!isRecord(raw)) return null;
	const kind = raw.kind === 'color' || raw.kind === 'picture' || raw.kind === 'transparent' ? raw.kind : null;
	const matte = isRecord(raw.matte) ? text(raw.matte.item_id) : null;
	if (!kind || !matte) return null;
	const picture = isRecord(raw.picture) && text(raw.picture.item_id) ? { item_id: text(raw.picture.item_id)!, url: text(raw.picture.url), name: text(raw.picture.name) } : null;
	if (kind === 'picture' && !picture) return null;
	const color = normalizeHex(raw.color);
	if (kind === 'color' && !color) return null;
	return {
		v: REPLACE_BACKGROUND_VERSION,
		kind,
		color: kind === 'color' ? color : null,
		picture: kind === 'picture' ? picture : null,
		matte: { item_id: matte, url: isRecord(raw.matte) ? text(raw.matte.url) : null }
	};
}

export function paramsFor(choice: BackgroundChoice, matte: { item_id: string; url?: string | null }): ReplaceBackgroundParams | null {
	if (choice.mode === 'describe') return null;
	return readReplaceBackgroundParams({ kind: choice.mode, color: choice.color, picture: choice.picture, matte: { item_id: matte.item_id, url: matte.url ?? null } });
}

export function backgroundSummary(params: ReplaceBackgroundParams): string {
	if (params.kind === 'color') return params.color ?? 'Colour';
	if (params.kind === 'picture') return params.picture?.name ?? 'Picture';
	return 'Transparent';
}

export function backgroundDraft(params: ReplaceBackgroundParams): StepDraft<ReplaceBackgroundParams> {
	return { tool: REPLACE_BACKGROUND_TOOL, icon: editorToolIcons.replace_background, label: 'Replace background', summary: backgroundSummary(params), params, lane: 'paint' };
}

export function backgroundPayload(raw: unknown): MediaToolStep[] {
	const params = readReplaceBackgroundParams(raw);
	if (!params) return [{ tool: REPLACE_BACKGROUND_TOOL, params: {}, hidden: true }];
	const out: Record<string, unknown> = { kind: params.kind, matte: { item_id: params.matte.item_id } };
	if (params.kind === 'color') out.color = params.color;
	if (params.kind === 'picture' && params.picture) out.picture = { item_id: params.picture.item_id };
	return [{ tool: REPLACE_BACKGROUND_TOOL, params: out }];
}

export function swatchUrl(color: string): string {
	const fill = normalizeHex(color) ?? '#000000';
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="${fill}"/></svg>`;
	return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export function backgroundThumb(params: ReplaceBackgroundParams): string | null {
	if (params.kind === 'color' && params.color) return swatchUrl(params.color);
	if (params.kind === 'picture') return params.picture?.url ?? null;
	return null;
}

export function pictureTarget(ref: Pick<MediaRef, 'item_id' | 'artifact_id' | 'url' | 'name'>): BackgroundPicture | null {
	const id = ref.item_id ?? ref.artifact_id ?? null;
	return id ? { item_id: id, url: ref.url ?? null, name: ref.name ?? null } : null;
}

export type Backdrop = { kind: 'color'; rgb: readonly [number, number, number] } | { kind: 'transparent' } | { kind: 'picture'; pixels: Uint8ClampedArray };

export function hexToRgb(hex: string): [number, number, number] {
	const value = normalizeHex(hex) ?? '#000000';
	return [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)];
}

export function mixBackground(base: Uint8ClampedArray, matte: Uint8ClampedArray, backdrop: Backdrop, opacity = 1): void {
	for (let i = 0; i + 3 < base.length; i += 4) {
		const weight = (matte[i] / 255) * (matte[i + 3] / 255) * opacity;
		if (weight <= 0) continue;
		if (backdrop.kind === 'transparent') {
			base[i + 3] = Math.round(base[i + 3] * (1 - weight));
			continue;
		}
		const source = backdrop.kind === 'color' ? [backdrop.rgb[0], backdrop.rgb[1], backdrop.rgb[2], 255] : [backdrop.pixels[i], backdrop.pixels[i + 1], backdrop.pixels[i + 2], backdrop.pixels[i + 3]];
		const layer = (source[3] / 255) * weight;
		const under = base[i + 3] / 255;
		const out = layer + under * (1 - layer);
		if (out <= 0) continue;
		for (let channel = 0; channel < 3; channel += 1) base[i + channel] = Math.round((source[channel] * layer + base[i + channel] * under * (1 - layer)) / out);
		base[i + 3] = Math.round(out * 255);
	}
}

function coverCanvas(source: HTMLCanvasElement, width: number, height: number): HTMLCanvasElement {
	const out = document.createElement('canvas');
	out.width = width;
	out.height = height;
	const context = out.getContext('2d', { willReadFrequently: true });
	if (!context || !source.width || !source.height) return out;
	const scale = Math.max(width / source.width, height / source.height);
	const drawn = { width: source.width * scale, height: source.height * scale };
	context.imageSmoothingQuality = 'high';
	context.drawImage(source, (width - drawn.width) / 2, (height - drawn.height) / 2, drawn.width, drawn.height);
	return out;
}

function pixelsOf(canvas: HTMLCanvasElement): Uint8ClampedArray {
	return canvas.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, canvas.width, canvas.height).data;
}

export const replaceBackgroundStepKind: StepKind<ReplaceBackgroundParams> = {
	id: REPLACE_BACKGROUND_TOOL,
	editor: 'image',
	label: 'Replace background',
	icon: editorToolIcons.replace_background,
	lane: 'paint',
	layer: true,
	version: REPLACE_BACKGROUND_VERSION,
	summary: (_params, ctx) => ctx.step.summary,
	outputSize: (_params, ctx) => ctx.input,
	payload: (raw) => backgroundPayload(raw),
	async preview(frame, raw, ctx, signal) {
		const params = readReplaceBackgroundParams(raw);
		if (!params?.matte.url) return frame;
		const { width, height } = frame.canvas;
		const matte = coverCanvas(await canvasFromUrl(params.matte.url), width, height);
		let backdrop: Backdrop | null = null;
		if (params.kind === 'color' && params.color) backdrop = { kind: 'color', rgb: hexToRgb(params.color) };
		else if (params.kind === 'transparent') backdrop = { kind: 'transparent' };
		else if (params.kind === 'picture' && params.picture?.url) backdrop = { kind: 'picture', pixels: pixelsOf(coverCanvas(await canvasFromUrl(params.picture.url), width, height)) };
		if (!backdrop || signal.aborted) return frame;
		const out = document.createElement('canvas');
		out.width = width;
		out.height = height;
		const context = out.getContext('2d', { willReadFrequently: true });
		if (!context) return frame;
		context.drawImage(frame.canvas, 0, 0);
		const image = context.getImageData(0, 0, width, height);
		mixBackground(image.data, pixelsOf(matte), backdrop, opacityOf(ctx.step));
		context.putImageData(image, 0, 0);
		return { canvas: out, size: frame.size };
	},
	extras(step) {
		const params = readReplaceBackgroundParams(step.params);
		return { thumb: params ? backgroundThumb(params) : null };
	},
	reopenTool: REPLACE_BACKGROUND_TASK
};
