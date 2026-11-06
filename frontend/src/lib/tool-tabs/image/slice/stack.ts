import { appliedSteps, undoneSteps, type EditStackState, type EditStep } from '$lib/tool-tabs/frame/editStack';

export const SLICE_TOOL = 'slice';

export function isSlice(step: Pick<EditStep, 'tool'> | null | undefined): boolean {
	return step?.tool === SLICE_TOOL;
}

export function sliceLast<P>(state: EditStackState<P>): EditStackState<P> {
	const applied = appliedSteps(state);
	const index = applied.findIndex(isSlice);
	if (index < 0 || index === applied.length - 1) return state;
	const steps = [...applied.slice(0, index), ...applied.slice(index + 1), applied[index], ...undoneSteps(state)];
	return { ...state, steps };
}

export function canMoveWithSlice(state: EditStackState<unknown>, id: string, to: number): boolean {
	const applied = appliedSteps(state);
	const index = applied.findIndex(isSlice);
	if (index < 0) return true;
	if (applied[index].id === id) return false;
	return to < index;
}

export function activeSlice<P>(state: EditStackState<P>): { step: EditStep<P> | null; pending: boolean } | null {
	if (state.pending && isSlice(state.pending)) {
		const target = state.pending.target ? appliedSteps(state).find((step) => step.id === state.pending!.target) ?? null : null;
		return { step: target, pending: true };
	}
	const step = appliedSteps(state).find(isSlice);
	if (!step || step.hidden) return null;
	return { step, pending: false };
}

export function withoutSlice<P>(steps: readonly EditStep<P>[]): EditStep<P>[] {
	return steps.filter((step) => !isSlice(step));
}
