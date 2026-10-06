import { OP_CATALOGUE, type FilterStepSpec, type OpDef, type OpParam } from './engine';

export type OpLookup = (id: string) => OpDef | undefined;

export function opLookup(extra: OpDef[] = []): OpLookup {
	const table = new Map<string, OpDef>();
	for (const def of OP_CATALOGUE) table.set(def.id, def);
	for (const def of extra) table.set(def.id, def);
	return (id) => table.get(id);
}

export function scalarParams(def: OpDef | undefined): OpParam[] {
	return (def?.params ?? []).filter((param) => param.type !== 'points');
}

export function pointParams(def: OpDef | undefined): OpParam[] {
	return (def?.params ?? []).filter((param) => param.type === 'points');
}

export function stepValue(step: FilterStepSpec, param: OpParam): number {
	const raw = step[param.id];
	return typeof raw === 'number' ? raw : param.default;
}

export function isStepEnabled(step: FilterStepSpec): boolean {
	return step.enabled !== false;
}

export function setStepParam(
	steps: FilterStepSpec[],
	index: number,
	paramId: string,
	value: number
): FilterStepSpec[] {
	return steps.map((step, i) => (i === index ? { ...step, [paramId]: value } : step));
}

export function toggleStep(steps: FilterStepSpec[], index: number): FilterStepSpec[] {
	return steps.map((step, i) => (i === index ? { ...step, enabled: !isStepEnabled(step) } : step));
}

export function editedParams(
	base: FilterStepSpec | undefined,
	current: FilterStepSpec,
	lookup: OpLookup
): number {
	if (!base) return 0;
	let edited = isStepEnabled(base) === isStepEnabled(current) ? 0 : 1;
	for (const param of scalarParams(lookup(current.op))) {
		if (stepValue(base, param) !== stepValue(current, param)) edited += 1;
	}
	return edited;
}

export function countEdits(base: FilterStepSpec[], current: FilterStepSpec[], lookup: OpLookup): number {
	return current.reduce((total, step, index) => total + editedParams(base[index], step, lookup), 0);
}

export function editedSteps(base: FilterStepSpec[], current: FilterStepSpec[], lookup: OpLookup): number {
	return current.filter((step, index) => editedParams(base[index], step, lookup) > 0).length;
}

export function serializeSteps(steps: FilterStepSpec[]): FilterStepSpec[] {
	return steps.map((step) => {
		const { enabled, ...rest } = step;
		return enabled === false ? { ...rest, enabled: false } : { ...rest };
	});
}

export function stepsEqual(a: FilterStepSpec[], b: FilterStepSpec[]): boolean {
	return JSON.stringify(serializeSteps(a)) === JSON.stringify(serializeSteps(b));
}
