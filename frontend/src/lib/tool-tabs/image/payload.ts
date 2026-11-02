import { operationsToSteps } from '$lib/media/editors/editOperations';
import { planOutputSize, planToOperations } from '$lib/media/editors/imageEditPlan';
import type {
	MediaToolInputRef,
	MediaToolSaveMode,
	MediaToolStep,
	StartMediaToolRunBody
} from '$lib/media-tools/types';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import type { MediaRef } from '$lib/types/tabs';
import { catalogToolId, type CatalogParams, type CropParams, type PaintParams, type ResizeParams } from './steps';

export interface PixelSize {
	width: number;
	height: number;
}

export type PayloadGap = { kind: 'bake' | 'size'; step: string };

export class PayloadError extends Error {
	constructor(readonly gap: PayloadGap) {
		super(gap.kind === 'bake' ? 'A look still has to be rendered' : 'The size before a crop is not known yet');
	}
}

function positive(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}

export function resizeOutput(params: ResizeParams, input: PixelSize | null): PixelSize {
	if (params.lock && input && input.width > 0 && input.height > 0) {
		const width = Math.max(1, Math.round(params.width));
		return { width, height: Math.max(1, Math.round((width * input.height) / input.width)) };
	}
	return { width: Math.max(1, Math.round(params.width)), height: Math.max(1, Math.round(params.height)) };
}

export function sizeAfter(step: EditStep, input: PixelSize | null): PixelSize | null {
	switch (step.tool) {
		case 'crop':
			return input ? planOutputSize((step.params as CropParams).plan, input) : null;
		case 'resize':
			return resizeOutput(step.params as ResizeParams, input);
		case 'canvas': {
			const values = (step.params as CatalogParams).values ?? {};
			const width = positive(values.width);
			const height = positive(values.height);
			return width && height ? { width, height } : input;
		}
		case 'subject':
			return null;
		default:
			return input;
	}
}

export function inputSizes(steps: readonly EditStep[], source: PixelSize | null, known: Record<string, PixelSize> = {}): (PixelSize | null)[] {
	const sizes: (PixelSize | null)[] = [];
	let size = source;
	for (const step of steps) {
		size = known[step.id] ?? size;
		sizes.push(size);
		if (!step.hidden) size = sizeAfter(step, size);
	}
	return sizes;
}

export function outputSize(steps: readonly EditStep[], source: PixelSize | null): PixelSize | null {
	let size = source;
	for (const step of steps) if (!step.hidden) size = sizeAfter(step, size);
	return size;
}

function layerRef(id: string): { item_id: string } {
	return { item_id: id };
}

export function stepPayload(step: EditStep, input: PixelSize | null, baked?: string | null): MediaToolStep[] {
	switch (step.tool) {
		case 'crop': {
			if (!input) throw new PayloadError({ kind: 'size', step: step.id });
			return operationsToSteps(planToOperations((step.params as CropParams).plan, input));
		}
		case 'resize': {
			const params = step.params as ResizeParams;
			const out = resizeOutput(params, input);
			return [{ tool: 'resize', params: params.lock ? { width: out.width } : { width: out.width, height: out.height } }];
		}
		case 'paint':
			return [{ tool: 'paint_layer', params: { layer: layerRef((step.params as PaintParams).layer) } }];
		case 'effects':
			if (!baked) throw new PayloadError({ kind: 'bake', step: step.id });
			return [{ tool: 'paint_layer', params: { layer: layerRef(baked) } }];
		default:
			return [{ tool: catalogToolId(step.tool), params: { ...((step.params as CatalogParams)?.values ?? {}) } }];
	}
}

function hiddenPayload(step: EditStep, input: PixelSize | null): MediaToolStep[] {
	if (step.tool === 'effects') return [{ tool: 'paint_layer', params: {}, hidden: true }];
	if (step.tool === 'crop' && !input) return [{ tool: 'crop', params: {}, hidden: true }];
	return stepPayload(step, input).map((entry) => ({ ...entry, hidden: true }));
}

export interface PayloadContext {
	source: PixelSize | null;
	sizes?: Record<string, PixelSize>;
	baked?: Record<string, string>;
}

export function runSteps(steps: readonly EditStep[], context: PayloadContext): MediaToolStep[] {
	const sizes = inputSizes(steps, context.source, context.sizes);
	return steps.flatMap((step, i) =>
		step.hidden ? hiddenPayload(step, sizes[i]) : stepPayload(step, sizes[i], context.baked?.[step.id] ?? null)
	);
}

export function payloadGaps(steps: readonly EditStep[], context: PayloadContext): PayloadGap[] {
	const sizes = inputSizes(steps, context.source, context.sizes);
	const gaps: PayloadGap[] = [];
	steps.forEach((step, i) => {
		if (step.hidden) return;
		if (step.tool === 'effects' && !context.baked?.[step.id]) gaps.push({ kind: 'bake', step: step.id });
		if (step.tool === 'crop' && !sizes[i]) gaps.push({ kind: 'size', step: step.id });
	});
	return gaps;
}

export function inputRef(source: MediaRef | null): MediaToolInputRef | null {
	if (!source) return null;
	if (source.artifact_id) return { item_id: source.artifact_id };
	if (source.item_id) return { item_id: source.item_id };
	if (source.generation_file_id) return { generation_file_id: source.generation_file_id };
	return null;
}

export function runBody(
	steps: MediaToolStep[],
	input: MediaToolInputRef,
	mode: MediaToolSaveMode,
	targetItemId?: string | null
): StartMediaToolRunBody {
	const output: StartMediaToolRunBody['output'] = { mode };
	if (mode === 'update' && targetItemId) output.target_item_id = targetItemId;
	return { steps, inputs: [input], output };
}

export type SourceOrigin = 'library' | 'history' | 'upload' | 'unknown';

export function sourceOrigin(source: MediaRef | null): SourceOrigin {
	if (!source) return 'unknown';
	if (source.artifact_id) return 'upload';
	if (source.item_id) return 'library';
	if (source.generation_file_id) return 'history';
	return 'unknown';
}

export function replaceOption(origin: SourceOrigin): { allowed: boolean; reason?: string } {
	if (origin === 'library') return { allowed: true };
	if (origin === 'history') return { allowed: false, reason: 'Files from History stay as generated' };
	if (origin === 'upload') return { allowed: false, reason: 'A dropped file has no original to replace' };
	return { allowed: false, reason: 'This file is not in your Library' };
}
