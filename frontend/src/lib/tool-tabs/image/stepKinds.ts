import { get } from 'svelte/store';
import { mediaToolCatalog, mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { hasFailed } from '$lib/editors/guard';
import { callKind, resolveStepKind, stepKindKey, unavailableState, type StepContext, type StepResolution, type StepRowInfo, type StepState } from '$lib/editors/stepKinds';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import type { PixelSize } from '$lib/editors/types';
import type { MediaToolStep } from '$lib/media-tools/types';
import { ensureCoreImageEditor } from './coreEditor';

export type ImageStepLike = Pick<EditStep, 'tool'> & { params?: unknown };

export function resolveImageStep(step: ImageStepLike): StepResolution {
	ensureCoreImageEditor();
	return resolveStepKind('image', { tool: step.tool, params: step.params }, get(mediaToolCatalogRevision) === 0);
}

export function isStepUnavailable(step: ImageStepLike): boolean {
	return resolveImageStep(step).status !== 'kind';
}

export function unavailablePayload(step: Pick<EditStep, 'tool'>): MediaToolStep[] {
	return [{ tool: step.tool, params: {}, hidden: true }];
}

export function imageStepContext(
	step: EditStep,
	input: PixelSize | null,
	beneath: readonly EditStep[] = [],
	baked: string | null = null,
	maxSide?: number
): StepContext {
	const ctx: StepContext = { editor: 'image', input, beneath, catalog: get(mediaToolCatalog), step, baked };
	if (maxSide !== undefined) ctx.maxSide = maxSide;
	return ctx;
}

export function imageRowInfo(step: EditStep): StepRowInfo | null {
	const resolved = resolveImageStep(step);
	if (resolved.status !== 'kind') return { state: unavailableState(resolved), locked: true };
	const { kind } = resolved;
	const entry = stepKindKey(kind);
	const failed = !!kind.pluginId && hasFailed(entry);
	const done: StepState = { kind: 'done' };
	const state: StepState = failed
		? { kind: 'skipped', tone: 'danger', label: 'Could not run' }
		: kind.state
			? callKind(kind, 'state', () => kind.state!(step, imageStepContext(step, null)), done)
			: done;
	if (state.kind === 'done' && !kind.row) return null;
	return kind.row ? { state, locked: false, entry, row: kind.row } : { state, locked: false, entry };
}

export function runStepAction(step: EditStep, action: string): void {
	const resolved = resolveImageStep(step);
	if (resolved.status !== 'kind') return;
	const { kind } = resolved;
	callKind(kind, 'action', () => kind.onAction?.(step, action, imageStepContext(step, null)), undefined);
}
