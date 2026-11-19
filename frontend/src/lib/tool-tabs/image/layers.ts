import type { MediaToolStep } from '$lib/media-tools/types';
import { opacityOf, type EditStep } from '$lib/tool-tabs/frame/editStack';
import type { NormRect } from '$lib/services/api/editorTasks';
import { chosenResult, patchPlacement, readGenerateParams } from './generate/generateStep';

export type Rgba = readonly [number, number, number, number];

const LAYER_TOOLS: ReadonlySet<string> = new Set(['paint', 'generate']);
const OPACITY_TOOLS: ReadonlySet<string> = new Set(['paint_layer', 'place_patch']);

export function isLayerStep(step: Pick<EditStep, 'tool'>): boolean {
	return LAYER_TOOLS.has(step.tool);
}

export function compositeOver(base: Rgba, layer: Rgba, opacity: number): [number, number, number, number] {
	const clamped = Math.min(1, Math.max(0, opacity));
	const layerAlpha = Math.round(layer[3] * clamped) / 255;
	const baseAlpha = base[3] / 255;
	const outAlpha = layerAlpha + baseAlpha * (1 - layerAlpha);
	if (outAlpha <= 0) return [0, 0, 0, 0];
	const channel = (index: number) => Math.round((layer[index] * layerAlpha + base[index] * baseAlpha * (1 - layerAlpha)) / outAlpha);
	return [channel(0), channel(1), channel(2), Math.round(outAlpha * 255)];
}

export function withLayerOpacity(step: Pick<EditStep, 'layer'>, entries: MediaToolStep[]): MediaToolStep[] {
	const opacity = opacityOf(step);
	if (opacity >= 1) return entries;
	return entries.map((entry) => (OPACITY_TOOLS.has(entry.tool) && Object.keys(entry.params ?? {}).length ? { ...entry, params: { ...entry.params, opacity } } : entry));
}

export function appliesBeneath(step: Pick<EditStep, 'tool'>): boolean {
	return step.tool === 'effects';
}

export function layerThumb(step: Pick<EditStep, 'tool' | 'params'>): string | null {
	if (step.tool !== 'paint') return null;
	const url = (step.params as { url?: unknown } | null)?.url;
	return typeof url === 'string' && url ? url : null;
}

export interface AboveLayer {
	url: string;
	rect: NormRect | null;
	opacity: number;
}

export function aboveLayerOf(step: EditStep): AboveLayer | null {
	if (step.hidden) return null;
	if (step.tool === 'paint') {
		const url = (step.params as { url?: unknown } | null)?.url;
		return typeof url === 'string' && url ? { url, rect: null, opacity: opacityOf(step) } : null;
	}
	if (step.tool !== 'generate') return null;
	const params = readGenerateParams(step.params);
	const result = params ? chosenResult(params) : null;
	const rect = params ? patchPlacement(params) : null;
	return result && rect ? { url: result.url, rect, opacity: opacityOf(step) } : null;
}

export function aboveKeyOf(layers: readonly AboveLayer[]): string {
	return layers.map((layer) => `${layer.url}@${layer.opacity}@${layer.rect ? `${layer.rect.x},${layer.rect.y},${layer.rect.width},${layer.rect.height}` : ''}`).join('|');
}

export interface PatchMask {
	url: string | null;
	rect: NormRect;
}

export function patchMaskOf(step: Pick<EditStep, 'tool' | 'params'>): PatchMask | null {
	if (step.tool !== 'generate') return null;
	const params = readGenerateParams(step.params);
	const rect = params ? patchPlacement(params) : null;
	if (!params || !rect) return null;
	return { url: params.area.region?.url ?? null, rect };
}

export function isFlattenedPaint(step: Pick<EditStep, 'tool' | 'params'>): boolean {
	return step.tool === 'paint' && !(step.params as { name?: unknown } | null)?.name;
}

export function mergeLayerPixels(below: Uint8ClampedArray, belowOpacity: number, upper: Uint8ClampedArray, upperOpacity: number): Uint8ClampedArray<ArrayBuffer> {
	const out = new Uint8ClampedArray(below.length);
	for (let i = 0; i < below.length; i += 4) {
		const lower = compositeOver([0, 0, 0, 0], [below[i], below[i + 1], below[i + 2], below[i + 3]], belowOpacity);
		const merged = compositeOver(lower, [upper[i], upper[i + 1], upper[i + 2], upper[i + 3]], upperOpacity);
		out[i] = merged[0];
		out[i + 1] = merged[1];
		out[i + 2] = merged[2];
		out[i + 3] = merged[3];
	}
	return out;
}
