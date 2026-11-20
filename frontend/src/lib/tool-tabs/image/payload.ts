import { operationsToSteps } from '$lib/media/editOperations';
import { planOutputSize, planToOperations } from '$lib/media/imageEditPlan';
import type {
	MediaToolInputRef,
	MediaToolSaveMode,
	MediaToolStep,
	StartMediaToolRunBody
} from '$lib/media-tools/types';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import type { MediaRef } from '$lib/types/tabs';
import { callKind } from '$lib/editors/stepKinds';
import { withLayerOpacity } from './layers';
import type { PixelSize } from '$lib/editors/types';
import { imageStepContext, resolveImageStep, stepOutOfDate, unavailablePayload } from './stepKinds';
import { sliceStepParams, type SliceParams } from './slice/model';
import { catalogToolId, isToolEffect, type CatalogParams, type CropParams, type PaintParams, type ResizeParams } from './steps';

export type { PixelSize };

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

export function coreSizeAfter(step: EditStep, input: PixelSize | null): PixelSize | null {
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

export function sizeAfter(step: EditStep, input: PixelSize | null, beneath: readonly EditStep[] = []): PixelSize | null {
	const resolved = resolveImageStep(step);
	if (resolved.status === 'loading') return coreSizeAfter(step, input);
	if (resolved.status !== 'kind') return input;
	if (stepOutOfDate(step, beneath)) return input;
	const { kind, params } = resolved;
	const ctx = imageStepContext(step, input, beneath);
	return callKind(kind, 'outputSize', () => (kind.outputSize ? kind.outputSize(params, ctx) : input), input);
}

function visibleBefore(steps: readonly EditStep[], index: number): EditStep[] {
	return steps.slice(0, index).filter((step) => !step.hidden);
}

export function inputSizes(steps: readonly EditStep[], source: PixelSize | null, known: Record<string, PixelSize> = {}): (PixelSize | null)[] {
	const sizes: (PixelSize | null)[] = [];
	let size = source;
	steps.forEach((step, i) => {
		size = known[step.id] ?? size;
		sizes.push(size);
		if (!step.hidden) size = sizeAfter(step, size, visibleBefore(steps, i));
	});
	return sizes;
}

export function outputSize(steps: readonly EditStep[], source: PixelSize | null): PixelSize | null {
	let size = source;
	steps.forEach((step, i) => {
		if (!step.hidden) size = sizeAfter(step, size, visibleBefore(steps, i));
	});
	return size;
}

function layerRef(id: string): { item_id: string } {
	return { item_id: id };
}

export function coreStepPayload(step: EditStep, input: PixelSize | null, baked?: string | null): MediaToolStep[] {
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
			return (step.params as PaintParams).layer ? [{ tool: 'paint_layer', params: { layer: layerRef((step.params as PaintParams).layer) } }] : [];
		case 'effects':
			if (isToolEffect(step.params)) return [{ tool: step.params.tool, params: { ...step.params.values } }];
			if (!baked) throw new PayloadError({ kind: 'bake', step: step.id });
			return [{ tool: 'paint_layer', params: { layer: layerRef(baked) } }];
		case 'slice':
			return [{ tool: 'slice', params: sliceStepParams(step.params as SliceParams) }];
		default:
			return [{ tool: catalogToolId(step.tool), params: { ...((step.params as CatalogParams)?.values ?? {}) } }];
	}
}

export function stepPayload(step: EditStep, input: PixelSize | null, baked?: string | null, beneath: readonly EditStep[] = []): MediaToolStep[] {
	const resolved = resolveImageStep(step);
	if (resolved.status === 'loading') return withLayerOpacity(step, coreStepPayload(step, input, baked));
	if (resolved.status !== 'kind') return unavailablePayload(step);
	const { kind, params } = resolved;
	const ctx = imageStepContext(step, input, beneath, baked ?? null);
	return withLayerOpacity(step, callKind(kind, 'payload', () => kind.payload(params, ctx), unavailablePayload(step)));
}

function hiddenPayload(step: EditStep, input: PixelSize | null): MediaToolStep[] {
	const resolution = resolveImageStep(step).status;
	if (resolution === 'unavailable') return unavailablePayload(step);
	if (step.tool === 'effects' && isToolEffect(step.params)) return [{ tool: step.params.tool, params: { ...step.params.values }, hidden: true }];
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
	return steps.flatMap((step, i) => {
		const beneath = visibleBefore(steps, i);
		return step.hidden || stepOutOfDate(step, beneath) ? hiddenPayload(step, sizes[i]) : stepPayload(step, sizes[i], context.baked?.[step.id] ?? null, beneath);
	});
}

export function payloadGaps(steps: readonly EditStep[], context: PayloadContext): PayloadGap[] {
	const sizes = inputSizes(steps, context.source, context.sizes);
	const gaps: PayloadGap[] = [];
	steps.forEach((step, i) => {
		if (step.hidden) return;
		if (step.tool === 'effects' && !isToolEffect(step.params) && !context.baked?.[step.id]) gaps.push({ kind: 'bake', step: step.id });
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
	targetItemId?: string | null,
	collectionId?: string | null,
	edit?: { id: string; revision: number } | null,
	name?: string | null
): StartMediaToolRunBody {
	const output: StartMediaToolRunBody['output'] = { mode };
	if (mode === 'update' && targetItemId) output.target_item_id = targetItemId;
	if (collectionId) output.collection_id = collectionId;
	if (edit) output.edit = { id: edit.id, revision: edit.revision };
	const trimmed = (name ?? '').trim();
	if (trimmed) output.name = trimmed.slice(0, 200);
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
