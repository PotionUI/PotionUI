import {
	evaluateCondition,
	processSchemaWithReactions,
	type Condition,
	type LogicalCondition,
	type Reaction
} from '$lib/form/reactions';
import {
	PROMPT_AXIS_FIELD,
	type AxisCandidate,
	type AxisEditorKind,
	type AxisOption
} from './types';

const CONTAINER_TYPES = new Set([
	'tabs',
	'tab',
	'accordion',
	'group',
	'row',
	'section',
	'alert',
	'markdown',
	'header',
	'gate'
]);

const EXCLUDED_TYPES = new Set([
	'image',
	'video',
	'audio',
	'media',
	'string',
	'textbox',
	'llm',
	'prompt_timeline',
	'camera_shot',
	'cloud_options'
]);

const CHIP_TYPES = new Set(['select', 'sampler', 'schedule', 'checkbox_group']);
const NUMBER_TYPES = new Set(['number', 'integer', 'stepper', 'slider']);
const CHECKBOX_TYPES = new Set(['checkbox', 'boolean']);

export type AxisEditorProbe = (type: string) => boolean;

type RawField = Record<string, any>;

function optionList(raw: unknown): AxisOption[] {
	if (!Array.isArray(raw)) return [];
	const out: AxisOption[] = [];
	for (const entry of raw) {
		if (entry === null || entry === undefined) continue;
		if (typeof entry === 'object') {
			const value = (entry as RawField).value;
			if (value === undefined || value === '') continue;
			out.push({ value, label: String((entry as RawField).label ?? value) });
		} else {
			out.push({ value: entry, label: String(entry) });
		}
	}
	return out;
}

function tagOptions(config: RawField, current: unknown): AxisOption[] {
	const categories = Array.isArray(config.categories) ? config.categories : [];
	const base: Record<string, string[]> =
		current && typeof current === 'object' && !Array.isArray(current)
			? (current as Record<string, string[]>)
			: {};
	const out: AxisOption[] = [];
	for (const category of categories) {
		const key = String(category?.key ?? '');
		for (const tag of Array.isArray(category?.tags) ? category.tags : []) {
			const next: Record<string, string[]> = {};
			for (const cat of categories) next[cat.key] = [...(base[cat.key] ?? [])];
			next[key] = [String(tag)];
			out.push({ value: next, label: String(tag) });
		}
	}
	return out;
}

function numberBounds(type: string, config: RawField): { min: number | null; max: number | null; step: number | null } {
	const min = config.minimum ?? config.min ?? (type === 'slider' ? 0 : null);
	const max = config.maximum ?? config.max ?? (type === 'slider' ? 100 : null);
	const step = config.step ?? null;
	return {
		min: typeof min === 'number' ? min : null,
		max: typeof max === 'number' ? max : null,
		step: typeof step === 'number' ? step : null
	};
}

export function editorKindFor(
	type: string,
	config: RawField,
	hasPluginEditor: AxisEditorProbe = () => false
): { editor: AxisEditorKind | null; options: AxisOption[] } {
	if (CHIP_TYPES.has(type)) {
		const options = optionList(config.options ?? config.configuration?.options);
		return { editor: options.length > 0 ? 'chips' : null, options };
	}
	if (type === 'tags') {
		const options = tagOptions(config, config.value);
		return { editor: options.length > 0 ? 'chips' : null, options };
	}
	if (NUMBER_TYPES.has(type)) return { editor: 'number', options: [] };
	if (type === 'seed') return { editor: 'seed', options: [] };
	if (type === 'model' || type === 'models') return { editor: 'model', options: [] };
	if (type === 'lora_picker') return { editor: 'lora', options: [] };
	if (type === 'resolution') {
		const options = optionList(config.options ?? config.configuration?.options);
		return { editor: options.length > 0 ? 'resolution' : null, options };
	}
	if (CHECKBOX_TYPES.has(type)) return { editor: 'checkbox', options: [] };
	if (hasPluginEditor(type)) return { editor: 'plugin', options: [] };
	return { editor: null, options: [] };
}

function labelOf(field: RawField | undefined, name: string): string {
	return String(field?.title || field?.label || name);
}

function describeValue(value: unknown): string {
	if (value === true) return 'on';
	if (value === false) return 'off';
	if (Array.isArray(value)) return value.map(describeValue).join(', ');
	return String(value);
}

function firstCondition(when: Reaction['when']): Condition | null {
	if (Array.isArray(when)) return when[0] ?? null;
	if ('logic' in when && 'conditions' in when) {
		const inner = (when as LogicalCondition).conditions[0];
		return inner ? firstCondition(inner as Reaction['when']) : null;
	}
	return when as Condition;
}

function conditionValue(condition: Condition): unknown {
	for (const [key, value] of Object.entries(condition)) {
		if (key !== 'field' && key !== 'operator' && key !== 'value') return value;
	}
	return condition.value;
}

function reasonFor(
	field: RawField,
	data: Record<string, unknown>,
	labels: Map<string, RawField>
): string | null {
	for (const reaction of (field.reactions ?? []) as Reaction[]) {
		const hides = reaction.then?.set_visibility === false;
		const locks = reaction.then?.set_disabled === true;
		if (!hides && !locks) continue;
		let matched = false;
		try {
			matched = evaluateCondition(reaction.when, data);
		} catch {
			matched = false;
		}
		if (!matched) continue;
		const condition = firstCondition(reaction.when);
		if (!condition) return hides ? 'Hidden by another field' : 'Set by another field';
		const source = labelOf(labels.get(condition.field), condition.field);
		const value = describeValue(conditionValue(condition));
		if (hides) return `Hidden while ${source} is ${value}`;
		return `Set by ${source}: ${value}`;
	}
	return null;
}

type WalkContext = { tab: string | null; section: string | null };

function collectLeaves(
	nodes: RawField[],
	ctx: WalkContext,
	into: Array<{ field: RawField; group: string }>
): void {
	for (const node of nodes) {
		if (!node) continue;
		const type = String(node.type ?? '');
		const label = labelOf(node, '');
		if (type === 'tab') {
			collectLeaves(node.children ?? [], { tab: label || ctx.tab, section: null }, into);
			continue;
		}
		if (type === 'section') {
			collectLeaves(node.children ?? [], { ...ctx, section: label || ctx.section }, into);
			continue;
		}
		if (CONTAINER_TYPES.has(type)) {
			collectLeaves(node.children ?? [], ctx, into);
			continue;
		}
		if (!node.name) continue;
		into.push({ field: node, group: ctx.section || ctx.tab || 'General' });
	}
}

function rootNodes(schema: any): RawField[] {
	if (!schema?.properties) return [];
	return Object.values(schema.properties) as RawField[];
}

export function buildAxisCandidates(
	schema: any,
	formData: Record<string, unknown>,
	options: { hasPluginEditor?: AxisEditorProbe; includePrompt?: boolean } = {}
): AxisCandidate[] {
	if (!schema) return [];
	const hasPluginEditor = options.hasPluginEditor ?? (() => false);
	const originals: Array<{ field: RawField; group: string }> = [];
	collectLeaves(rootNodes(schema), { tab: null, section: null }, originals);

	const labels = new Map<string, RawField>();
	for (const { field } of originals) labels.set(field.name, field);

	const processed = processSchemaWithReactions(schema, formData).processedSchema;
	const processedLeaves: Array<{ field: RawField; group: string }> = [];
	collectLeaves(rootNodes(processed), { tab: null, section: null }, processedLeaves);
	const processedByName = new Map(processedLeaves.map((entry) => [entry.field.name, entry.field]));

	const out: AxisCandidate[] = [];
	const seen = new Set<string>();
	for (const { field, group } of originals) {
		const type = String(field.type ?? '');
		if (EXCLUDED_TYPES.has(type) || seen.has(field.name)) continue;
		seen.add(field.name);
		const live = processedByName.get(field.name) ?? field;
		const probe = editorKindFor(type, { ...live, value: formData[field.name] }, hasPluginEditor);
		const hidden = live.visible === false;
		const locked = live.disabled === true && field.disabled !== true;
		let unavailableReason: string | null = null;
		if (hidden || locked) unavailableReason = reasonFor(field, formData, labels) ?? (hidden ? 'Hidden right now' : 'Set by another field');
		else if (probe.editor === null) unavailableReason = 'Not comparable';
		const bounds = numberBounds(type, live);
		const cfg = (live.configuration ?? {}) as Record<string, unknown>;
		out.push({
			field: field.name,
			label: labelOf(field, field.name),
			type,
			group,
			editor: probe.editor,
			unavailableReason,
			options: probe.options,
			min: probe.editor === 'number' ? bounds.min : null,
			max: probe.editor === 'number' ? bounds.max : null,
			step: probe.editor === 'number' ? bounds.step : null,
			currentValue: formData[field.name],
			modelType: typeof cfg.model_type === 'string' ? cfg.model_type : typeof live.model_type === 'string' ? live.model_type : null,
			config: live
		});
	}

	if (options.includePrompt !== false) {
		out.push({
			field: PROMPT_AXIS_FIELD,
			label: 'Prompt: find and replace',
			type: 'prompt',
			group: 'Prompt',
			editor: 'prompt',
			unavailableReason: null,
			options: [],
			min: null,
			max: null,
			step: null,
			currentValue: undefined,
			modelType: null,
			config: {}
		});
	}
	return out;
}

export function groupCandidates(candidates: AxisCandidate[]): Array<{ group: string; items: AxisCandidate[] }> {
	const available = candidates.filter((c) => c.unavailableReason === null);
	const unavailable = candidates.filter((c) => c.unavailableReason !== null);
	const order: string[] = [];
	const byGroup = new Map<string, AxisCandidate[]>();
	for (const candidate of available) {
		if (!byGroup.has(candidate.group)) {
			byGroup.set(candidate.group, []);
			order.push(candidate.group);
		}
		byGroup.get(candidate.group)!.push(candidate);
	}
	const groups = order.map((group) => ({ group, items: byGroup.get(group)! }));
	if (unavailable.length > 0) groups.push({ group: 'Unavailable now', items: unavailable });
	return groups;
}

export function filterCandidates(candidates: AxisCandidate[], query: string): AxisCandidate[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return candidates;
	return candidates.filter(
		(candidate) =>
			candidate.label.toLowerCase().includes(needle) ||
			candidate.field.toLowerCase().includes(needle) ||
			candidate.type.toLowerCase().includes(needle)
	);
}

export function editorTypeLabel(candidate: Pick<AxisCandidate, 'type' | 'editor'>): string {
	if (candidate.editor === null) return candidate.type;
	if (candidate.editor === 'chips') return candidate.type === 'checkbox_group' ? 'checkboxes' : 'select';
	if (candidate.editor === 'number') return 'number';
	return candidate.editor;
}
