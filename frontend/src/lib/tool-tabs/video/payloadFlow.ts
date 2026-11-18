import { appliedSteps, apply, beginEdit, createStack, effectiveSteps, liveEdit, commitLive, updatePending, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { SCENARIO_CLIP, SCENARIO_EFFECTS, type Scenario } from './payloadScenarios';
import { keptRange } from './timeMap';
import { compileSteps, draftFor, isEffectOp, playbackPreview, settlePending, type VideoParams } from './videoSteps';

export const SCENARIO_EFFECT_MAP = new Map(SCENARIO_EFFECTS.map((effect) => [effect.id, effect]));

export interface Snapshot {
	rows: Array<{ tool: string; icon: string; label: string; summary: string; lane?: string; params: unknown }>;
	compiled: unknown;
}

export function snapshotOf(stack: EditStackState<VideoParams>): Snapshot {
	const steps = effectiveSteps(stack);
	return {
		rows: appliedSteps(stack).map((step) => ({
			tool: step.tool,
			icon: step.icon,
			label: step.label,
			summary: step.summary,
			...(step.lane ? { lane: step.lane } : {}),
			params: step.params
		})),
		compiled: compileSteps(steps, SCENARIO_CLIP, { effects: SCENARIO_EFFECT_MAP })
	};
}

export function keptOf(stack: EditStackState<VideoParams>) {
	return keptRange(SCENARIO_CLIP.duration, playbackPreview(effectiveSteps(stack), SCENARIO_CLIP).ops);
}

export function settle(stack: EditStackState<VideoParams>): EditStackState<VideoParams> {
	return settlePending(commitLive(stack), true, SCENARIO_CLIP, keptOf(stack));
}

export function referenceWrite(stack: EditStackState<VideoParams>, op: string, params: VideoParams): EditStackState<VideoParams> {
	const draft = draftFor(op, params, SCENARIO_EFFECT_MAP);
	const pending = stack.pending;
	if (isEffectOp(op)) {
		if (pending && pending.tool === op) return updatePending(stack, draft);
		if (appliedSteps(stack).some((step) => step.tool === op)) return liveEdit(stack, draft, (step) => step.tool === op);
		return beginEdit(settle(stack), draft);
	}
	return pending && pending.tool === op ? updatePending(stack, draft) : beginEdit(settle(stack), draft);
}

export function runReference(scenario: Scenario): Snapshot {
	let stack = createStack<VideoParams>();
	for (const step of scenario.steps) {
		stack = referenceWrite(stack, step.op, step.params);
		stack = finish(stack, step.op);
	}
	return snapshotOf(settle(stack));
}

function finish(stack: EditStackState<VideoParams>, tool: string): EditStackState<VideoParams> {
	return isEffectOp(tool) ? settle(stack) : apply(stack);
}
