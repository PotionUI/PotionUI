import { callKind, stepKinds } from '$lib/editors/stepKinds';
import type { EditStackState, EditStep } from '$lib/tool-tabs/frame/editStack';
import { readGenerateParams } from './generate/generateStep';
import type { PixelDims } from './generate/upscale';
import { geometryKey } from './mask';
import type { CatalogParams, CropParams, ResizeParams } from './steps';

function scaledSide(value: number, factor: number): number {
	return Math.max(1, Math.round(value * factor));
}

function rebaseResize(params: ResizeParams, factor: number): ResizeParams {
	return { ...params, width: scaledSide(params.width, factor), height: scaledSide(params.height, factor) };
}

function rebaseCanvas(params: CatalogParams, factor: number): CatalogParams {
	const values = { ...(params?.values ?? {}) };
	for (const key of ['width', 'height']) {
		const value = values[key];
		if (typeof value === 'number' && Number.isFinite(value) && value > 0) values[key] = scaledSide(value, factor);
	}
	return { ...params, values };
}

function rebaseCrop(params: CropParams, factor: number): CropParams {
	const longest = params.plan?.targetLongestSide;
	if (!longest) return params;
	return { ...params, plan: { ...params.plan, targetLongestSide: scaledSide(longest, factor) } };
}

export function rebaseStep(step: EditStep, factor: number): EditStep {
	if (!(factor > 0) || factor === 1) return step;
	switch (step.tool) {
		case 'resize': {
			const params = rebaseResize(step.params as ResizeParams, factor);
			return { ...step, params, summary: `${params.width}×${params.height} · Lanczos` };
		}
		case 'canvas':
			return { ...step, params: rebaseCanvas(step.params as CatalogParams, factor) };
		case 'crop':
			return { ...step, params: rebaseCrop(step.params as CropParams, factor) };
		default: {
			const kind = stepKinds.get('image', step.tool);
			if (!kind?.rebase) return step;
			const params = callKind(kind, 'rebase', () => kind.rebase!(step.params, factor), step.params);
			return params === step.params ? step : { ...step, params };
		}
	}
}

function beneathOf(steps: readonly EditStep[], index: number): EditStep[] {
	return steps.slice(0, index);
}

export function landUpscale(stack: EditStackState<unknown>, stepId: string, after: PixelDims): EditStackState<unknown> {
	const index = stack.steps.findIndex((step) => step.id === stepId);
	if (index < 0) return stack;
	const landed = stack.steps[index];
	const params = readGenerateParams(landed.params);
	if (!params) return stack;
	const before = params.out ?? params.basis?.size ?? null;
	const factor = before && before.width > 0 ? after.width / before.width : 1;
	const pending = { ...params, run: { ...params.run, status: 'running' as const } };
	const prior = stack.steps.map((step, i) => (i === index ? { ...step, params: pending } : step));
	const steps = stack.steps.map((step, i) => {
		if (i === index) return params.out?.width === after.width && params.out.height === after.height ? step : { ...step, params: { ...params, out: after } };
		return i > index ? rebaseStep(step, factor) : step;
	});
	const settled = steps.map((step, i) => {
		if (i <= index || step.tool !== 'generate') return step;
		const own = readGenerateParams(step.params);
		if (!own?.basis) return step;
		const wasKey = geometryKey(beneathOf(prior, i));
		if (own.basis.geometry !== wasKey) return step;
		const nowKey = geometryKey(beneathOf(steps, i));
		return nowKey === wasKey ? step : { ...step, params: { ...(step.params as object), basis: { ...own.basis, geometry: nowKey } } };
	});
	const same = settled.every((step, i) => step === stack.steps[i]);
	return same ? stack : { ...stack, steps: settled };
}
