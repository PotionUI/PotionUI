import { OPS, type FilterStep, type OpParam, type OpSpec } from './engine';

export type OpLookup = (id: string) => OpSpec | undefined;

export type ScalarParam = OpParam & { type: 'int' | 'float'; min: number; max: number; default: number };

export function opLookup(extra: OpSpec[] = []): OpLookup {
	const table = new Map<string, OpSpec>();
	for (const def of OPS) table.set(def.id, def);
	for (const def of extra) table.set(def.id, def);
	return (id) => table.get(id);
}

function isScalar(param: OpParam): param is ScalarParam {
	return param.type !== 'curve';
}

export function scalarParams(def: OpSpec | undefined): ScalarParam[] {
	return (def?.params ?? []).filter(isScalar);
}

export function pointParams(def: OpSpec | undefined): OpParam[] {
	return (def?.params ?? []).filter((param) => param.type === 'curve');
}

export function stepValue(step: FilterStep, param: ScalarParam): number {
	const raw = step[param.id];
	return typeof raw === 'number' ? raw : param.default;
}

export function isStepEnabled(step: FilterStep): boolean {
	return step.enabled !== false;
}

export function setStepParam(
	steps: FilterStep[],
	index: number,
	paramId: string,
	value: number
): FilterStep[] {
	return steps.map((step, i) => (i === index ? { ...step, [paramId]: value } : step));
}

export function toggleStep(steps: FilterStep[], index: number): FilterStep[] {
	return steps.map((step, i) => (i === index ? { ...step, enabled: !isStepEnabled(step) } : step));
}

export function editedParams(
	base: FilterStep | undefined,
	current: FilterStep,
	lookup: OpLookup
): number {
	if (!base) return 0;
	let edited = isStepEnabled(base) === isStepEnabled(current) ? 0 : 1;
	for (const param of scalarParams(lookup(current.op))) {
		if (stepValue(base, param) !== stepValue(current, param)) edited += 1;
	}
	return edited;
}

export function countEdits(base: FilterStep[], current: FilterStep[], lookup: OpLookup): number {
	return current.reduce((total, step, index) => total + editedParams(base[index], step, lookup), 0);
}

export function editedSteps(base: FilterStep[], current: FilterStep[], lookup: OpLookup): number {
	return current.filter((step, index) => editedParams(base[index], step, lookup) > 0).length;
}

export function serializeSteps(steps: FilterStep[]): FilterStep[] {
	return steps.map((step) => {
		const { enabled, ...rest } = step;
		return enabled === false ? { ...rest, enabled: false } : { ...rest };
	});
}

export function stepsEqual(a: FilterStep[], b: FilterStep[]): boolean {
	return JSON.stringify(serializeSteps(a)) === JSON.stringify(serializeSteps(b));
}
