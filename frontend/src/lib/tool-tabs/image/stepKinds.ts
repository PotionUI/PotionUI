import { get } from 'svelte/store';
import { mediaToolCatalog, mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { hasFailed } from '$lib/editors/guard';
import { callKind, resolveStepKind, stepKindKey, unavailableState, type StepContext, type StepResolution, type StepRowInfo, type StepState } from '$lib/editors/stepKinds';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import type { PixelSize } from '$lib/editors/types';
import type { MediaToolStep } from '$lib/media-tools/types';
import { ensureCoreImageEditor } from './coreEditor';
import { geometryKey } from './mask';

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

export const OUT_OF_DATE_LABEL = 'The picture changed';
export const OUT_OF_DATE_DETAIL = 'The picture beneath changed';

export function stepOutOfDate(step: EditStep, beneath: readonly EditStep[]): boolean {
	if (step.hidden) return false;
	const resolved = resolveImageStep(step);
	if (resolved.status !== 'kind' || !resolved.kind.shapeBasis) return false;
	const { kind, params } = resolved;
	const recorded = callKind(kind, 'shapeBasis', () => kind.shapeBasis!(params), null);
	if (recorded === null) return false;
	const current = kind.shapeKey ? callKind(kind, 'shapeKey', () => kind.shapeKey!(params, beneath), geometryKey(beneath)) : geometryKey(beneath);
	return recorded !== current;
}

export function stepsBeneath(steps: readonly EditStep[], index: number): EditStep[] {
	return steps.slice(0, index).filter((step) => !step.hidden);
}

export function outOfDateCount(steps: readonly EditStep[]): number {
	return steps.filter((step, index) => stepOutOfDate(step, stepsBeneath(steps, index))).length;
}

export function outOfDateNotice(count: number): string | null {
	if (count <= 0) return null;
	return `${count} ${count === 1 ? 'edit is' : 'edits are'} not included: the picture beneath ${count === 1 ? 'it' : 'them'} changed.`;
}

export function imageRowInfo(step: EditStep, beneath: readonly EditStep[] = []): StepRowInfo | null {
	const resolved = resolveImageStep(step);
	if (resolved.status !== 'kind') return { state: unavailableState(resolved), locked: true };
	const { kind } = resolved;
	const entry = stepKindKey(kind);
	const failed = !!kind.pluginId && hasFailed(entry);
	const done: StepState = { kind: 'done' };
	const own: StepState = failed
		? { kind: 'skipped', tone: 'danger', label: 'Could not run' }
		: kind.state
			? callKind(kind, 'state', () => kind.state!(step, imageStepContext(step, null, beneath)), done)
			: done;
	const outOfDate = own.kind === 'done' && stepOutOfDate({ ...step, hidden: false }, beneath);
	const state: StepState = outOfDate ? { kind: 'skipped', tone: 'warning', label: OUT_OF_DATE_LABEL, detail: OUT_OF_DATE_DETAIL } : own;
	const extras = kind.extras ? callKind(kind, 'extras', () => kind.extras!(step, state), {}) : {};
	const hasExtras = !!(extras.actions?.length || extras.thumb || extras.hideToggle === false || extras.removable === false);
	if (state.kind === 'done' && !kind.row && !hasExtras) return null;
	const info: StepRowInfo = { state, locked: false, entry, ...extras };
	if (kind.row) info.row = kind.row;
	return info;
}

export function runStepAction(step: EditStep, action: string): void {
	const resolved = resolveImageStep(step);
	if (resolved.status !== 'kind') return;
	const { kind } = resolved;
	callKind(kind, 'action', () => kind.onAction?.(step, action, imageStepContext(step, null)), undefined);
}
