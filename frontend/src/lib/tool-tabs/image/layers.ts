import type { MediaToolStep } from '$lib/media-tools/types';
import { opacityOf, type EditStep } from '$lib/tool-tabs/frame/editStack';

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
