import type { StepRowInfo, StepState } from '$lib/editors/stepKinds';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { generateStateOf, readGenerateParams } from '../image/generate/generateStep';
import { OUT_OF_DATE_CLIP_DETAIL, OUT_OF_DATE_CLIP_LABEL } from './generateChain';

export function generateRowInfo(step: EditStep<unknown>, stale: boolean): StepRowInfo | null {
	const params = readGenerateParams(step.params);
	if (!params) return null;
	const own = generateStateOf(params);
	if (own.kind === 'busy') return { state: own, locked: false, actions: [{ id: 'cancel', label: 'Cancel', icon: 'close' }], hideToggle: false, removable: false };
	if (own.kind === 'skipped') return { state: own, locked: false, actions: [{ id: 'retry', label: 'Retry', icon: 'refresh' }] };
	if (stale) {
		const state: StepState = { kind: 'skipped', tone: 'warning', label: OUT_OF_DATE_CLIP_LABEL, detail: OUT_OF_DATE_CLIP_DETAIL };
		return { state, locked: false, actions: [{ id: 'redo', label: 'Redo on the new clip', icon: 'refresh' }], hideToggle: step.hidden };
	}
	return { state: own, locked: false, actions: [{ id: 'again', label: 'Try again', icon: 'refresh' }] };
}
