export interface CapabilityParam {
	name: string;
	kind: 'enum' | 'range' | 'boolean' | 'text';
	values?: string[];
	minimum?: number | null;
	maximum?: number | null;
	step?: number | null;
	integer?: boolean;
	default?: unknown;
	required?: boolean;
	label?: string | null;
	description?: string | null;
	tasks?: string[];
	extra?: boolean;
}

export interface CapabilityInput {
	role: string;
	modality?: string;
	min_items?: number;
	max_items?: number;
	tasks?: string[];
}

export interface CloudCapabilities {
	model_id: string;
	label?: string;
	tasks?: string[];
	params: CapabilityParam[];
	inputs: CapabilityInput[];
}

export type CapabilitiesByModelField = Record<string, CloudCapabilities | null>;

interface CapabilityBinding {
	model_field: string;
	param?: string;
	input?: string;
}

interface SchemaNode {
	type?: string;
	name?: string;
	visible?: boolean;
	options?: Array<{ label: string; value: unknown }>;
	capability?: CapabilityBinding | null;
	configuration?: { tasks?: string[]; include_unbound?: boolean; [key: string]: unknown };
	children?: SchemaNode[];
	[key: string]: unknown;
}

interface SchemaLike {
	properties?: Record<string, { children?: SchemaNode[] }>;
}

export const CLOUD_OPTIONS_TYPE = 'cloud_options';
const MODEL_REF_PREFIX = 'model:';

function walk(nodes: SchemaNode[] | undefined, visit: (node: SchemaNode) => void): void {
	if (!Array.isArray(nodes)) return;
	for (const node of nodes) {
		visit(node);
		walk(node.children, visit);
	}
}

function walkSchema(schema: SchemaLike | null | undefined, visit: (node: SchemaNode) => void): void {
	if (!schema?.properties) return;
	for (const root of Object.values(schema.properties)) walk(root?.children, visit);
}

export function cloudModelId(value: unknown): string | null {
	const path = typeof value === 'string' ? value : ((value as { modelPath?: string } | null)?.modelPath ?? '');
	if (!path.startsWith(MODEL_REF_PREFIX)) return null;
	const id = path.slice(MODEL_REF_PREFIX.length).trim();
	return id || null;
}

export function resolveCapabilities(
	modelFields: readonly string[],
	formData: Record<string, unknown>,
	lookup: (modelId: string) => CloudCapabilities | undefined
): CapabilitiesByModelField {
	const result: CapabilitiesByModelField = {};
	for (const modelField of modelFields) {
		const id = cloudModelId(formData[modelField]);
		result[modelField] = id ? (lookup(id) ?? null) : null;
	}
	return result;
}

export function collectCapabilityModelFields(schema: SchemaLike | null | undefined): string[] {
	const fields = new Set<string>();
	walkSchema(schema, (node) => {
		const modelField = node.capability?.model_field;
		if (modelField) fields.add(modelField);
	});
	return [...fields];
}

export function modeTasksFor(schema: SchemaLike | null | undefined, modelField: string): string[] {
	let tasks: string[] = [];
	walkSchema(schema, (node) => {
		if (node.name === modelField && Array.isArray(node.configuration?.tasks)) {
			tasks = node.configuration.tasks.map(String);
		}
	});
	return tasks;
}

export function entryApplies(entryTasks: readonly string[] | undefined, modeTasks: readonly string[]): boolean {
	if (!entryTasks || entryTasks.length === 0) return true;
	if (modeTasks.length === 0) return true;
	return entryTasks.some((task) => modeTasks.includes(task));
}

function claimedParams(schema: SchemaLike, modelField: string): Set<string> {
	const claimed = new Set<string>();
	walkSchema(schema, (node) => {
		const binding = node.capability;
		if (binding?.model_field === modelField && binding.param) claimed.add(binding.param);
	});
	return claimed;
}

export function resolveCloudOptionParams(
	caps: CloudCapabilities | null,
	modeTasks: readonly string[],
	claimed: ReadonlySet<string>,
	includeUnbound: boolean
): CapabilityParam[] {
	if (!caps) return [];
	return caps.params.filter((param) => {
		if (!entryApplies(param.tasks, modeTasks)) return false;
		if (param.extra || param.name.startsWith('x.')) return true;
		return includeUnbound && !claimed.has(param.name);
	});
}

export function humanizeParamName(name: string): string {
	const base = name.startsWith('x.') ? name.slice(2) : name;
	const words = base.replace(/[._-]+/g, ' ').trim();
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}

function constrainEnum(node: SchemaNode, param: CapabilityParam): void {
	const labels = new Map((node.options ?? []).map((option) => [String(option.value), option.label]));
	node.options = (param.values ?? []).map((value) => ({ label: labels.get(String(value)) ?? String(value), value }));
}

const MAX_RANGE_OPTIONS = 50;
const SAMPLED_RANGE_OPTIONS = 11;

function rangeOptions(param: CapabilityParam): Array<{ label: string; value: number }> | null {
	if (typeof param.minimum !== 'number' || typeof param.maximum !== 'number') return null;
	const min = param.minimum;
	const max = param.maximum;
	const step = typeof param.step === 'number' && param.step > 0 ? param.step : 1;
	const stepSize = param.integer ? Math.max(1, Math.round(step)) : step;
	const decimals = (String(stepSize).split('.')[1] ?? '').length;
	const steps = Math.floor((max - min) / stepSize + 1e-9);
	if (steps < 0) return null;
	const round = (value: number) => Number(value.toFixed(decimals));
	const snap = (value: number) => round(min + Math.min(steps, Math.max(0, Math.round((value - min) / stepSize))) * stepSize);
	const picked = new Set<number>();
	if (steps + 1 <= MAX_RANGE_OPTIONS) {
		for (let index = 0; index <= steps; index++) picked.add(round(min + index * stepSize));
	} else {
		for (let index = 0; index < SAMPLED_RANGE_OPTIONS; index++) {
			picked.add(snap(min + ((max - min) * index) / (SAMPLED_RANGE_OPTIONS - 1)));
		}
		picked.add(round(min + steps * stepSize));
		if (typeof param.default === 'number') picked.add(snap(param.default));
	}
	return [...picked].sort((left, right) => left - right).map((value) => ({ label: String(value), value }));
}

function constrainRange(node: SchemaNode, param: CapabilityParam): void {
	if (node.type === 'select') {
		const options = rangeOptions(param);
		if (options) node.options = options;
		return;
	}
	if (typeof param.minimum === 'number') {
		node.minimum = param.minimum;
		node.min = param.minimum;
	}
	if (typeof param.maximum === 'number') {
		node.maximum = param.maximum;
		node.max = param.maximum;
	}
	if (typeof param.step === 'number' && param.step > 0) node.step = param.step;
	if (param.integer) {
		node.integer = true;
		const step = typeof node.step === 'number' ? node.step : 1;
		node.step = Math.max(1, Math.round(step));
	}
}

export function applyCapabilitiesToSchema(schema: SchemaLike | null | undefined, caps: CapabilitiesByModelField): string[] {
	const hidden: string[] = [];
	if (!schema) return hidden;
	walkSchema(schema, (node) => {
		const binding = node.capability;
		if (!binding?.model_field) return;
		const modelCaps = caps[binding.model_field] ?? null;
		if (node.type === CLOUD_OPTIONS_TYPE) {
			const params = resolveCloudOptionParams(
				modelCaps,
				modeTasksFor(schema, binding.model_field),
				claimedParams(schema, binding.model_field),
				Boolean(node.configuration?.include_unbound)
			);
			node.resolved_params = params;
			if (modelCaps && params.length === 0) {
				node.visible = false;
				if (node.name) hidden.push(node.name);
			} else if (!modelCaps) {
				node.visible = false;
				if (node.name) hidden.push(node.name);
			}
			return;
		}
		if (!modelCaps) return;
		const modeTasks = modeTasksFor(schema, binding.model_field);
		if (binding.param) {
			const param = modelCaps.params.find((entry) => entry.name === binding.param);
			if (!param || !entryApplies(param.tasks, modeTasks)) {
				node.visible = false;
				if (node.name) hidden.push(node.name);
				return;
			}
			if (param.kind === 'enum') constrainEnum(node, param);
			else if (param.kind === 'range') constrainRange(node, param);
			return;
		}
		if (binding.input) {
			const input = modelCaps.inputs.find((entry) => entry.role === binding.input);
			if (!input || !entryApplies(input.tasks, modeTasks)) {
				node.visible = false;
				if (node.name) hidden.push(node.name);
			}
		}
	});
	return hidden;
}

function isBlank(value: unknown): boolean {
	return value === undefined || value === null || value === '';
}

function validEnum(param: CapabilityParam, value: unknown): boolean {
	return (param.values ?? []).some((entry) => String(entry) === String(value));
}

function withinRange(param: CapabilityParam, value: number): boolean {
	if (typeof param.minimum === 'number' && value < param.minimum) return false;
	if (typeof param.maximum === 'number' && value > param.maximum) return false;
	if (param.integer && !Number.isInteger(value)) return false;
	return true;
}

function clampToRange(param: CapabilityParam, value: number): number {
	let next = value;
	if (typeof param.minimum === 'number') next = Math.max(param.minimum, next);
	if (typeof param.maximum === 'number') next = Math.min(param.maximum, next);
	if (param.integer) next = Math.round(next);
	return next;
}

function replacementFor(param: CapabilityParam, value: unknown): unknown {
	if (param.kind === 'enum') {
		if (!isBlank(param.default) && validEnum(param, param.default)) return param.default;
		return param.values?.[0];
	}
	if (param.kind === 'range') {
		if (typeof param.default === 'number' && withinRange(param, param.default)) return param.default;
		return typeof value === 'number' ? clampToRange(param, value) : param.minimum;
	}
	if (param.kind === 'boolean' && typeof param.default === 'boolean') return param.default;
	return value;
}

function hasUsableDefault(param: CapabilityParam): boolean {
	if (isBlank(param.default)) return false;
	if (param.kind === 'enum') return validEnum(param, param.default);
	if (param.kind === 'range') return typeof param.default === 'number' && withinRange(param, param.default);
	if (param.kind === 'boolean') return typeof param.default === 'boolean';
	return false;
}

function isInvalid(param: CapabilityParam, value: unknown): boolean {
	if (isBlank(value)) {
		if (hasUsableDefault(param)) return true;
		return Boolean(param.required) && (param.kind === 'enum' || param.kind === 'range');
	}
	if (param.kind === 'enum') return !validEnum(param, value);
	if (param.kind === 'range') return typeof value !== 'number' || !withinRange(param, value);
	return false;
}

export function capabilityValueChanges(
	schema: SchemaLike | null | undefined,
	formData: Record<string, unknown>,
	caps: CapabilitiesByModelField
): Record<string, unknown> {
	const changes: Record<string, unknown> = {};
	walkSchema(schema, (node) => {
		const binding = node.capability;
		if (!binding?.model_field || !node.name || node.visible === false) return;
		const modelCaps = caps[binding.model_field] ?? null;
		if (!modelCaps) return;
		if (node.type === CLOUD_OPTIONS_TYPE) {
			const params = (node.resolved_params as CapabilityParam[] | undefined) ?? [];
			const current = (formData[node.name] as Record<string, unknown> | undefined) ?? {};
			const next: Record<string, unknown> = {};
			for (const param of params) {
				const value = current[param.name];
				if (isBlank(value) && !hasUsableDefault(param)) continue;
				if (isInvalid(param, value)) {
					const replacement = replacementFor(param, value);
					if (!isBlank(replacement)) next[param.name] = replacement;
				} else {
					next[param.name] = value;
				}
			}
			if (JSON.stringify(next) !== JSON.stringify(current)) changes[node.name] = next;
			return;
		}
		if (!binding.param) return;
		const param = modelCaps.params.find((entry) => entry.name === binding.param);
		if (!param) return;
		const value = formData[node.name];
		if (isInvalid(param, value)) {
			const replacement = replacementFor(param, value);
			if (!isBlank(replacement) && replacement !== value) changes[node.name] = replacement;
		}
	});
	return changes;
}

export function omitCapabilityHidden<T extends Record<string, unknown>>(data: T, hidden: ReadonlySet<string>): T {
	if (hidden.size === 0) return data;
	const result: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(data)) {
		const owner = [...hidden].find(
			(name) =>
				key === name ||
				key.startsWith(`${name}__`) ||
				key === `${name}_inpaint_mask` ||
				key === `${name}_tagFilters`
		);
		if (!owner) result[key] = value;
	}
	return result as T;
}

export function splitOptionErrors(messages: readonly string[], keys: readonly string[]): {
	byKey: Record<string, string[]>;
	rest: string[];
} {
	const byKey: Record<string, string[]> = {};
	const rest: string[] = [];
	for (const message of messages) {
		const key = keys.find((candidate) => message.startsWith(`${candidate}: `));
		if (key) (byKey[key] ??= []).push(message.slice(key.length + 2));
		else rest.push(message);
	}
	return { byKey, rest };
}
