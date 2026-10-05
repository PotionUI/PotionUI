import type {
	OrganizeAction,
	OrganizeActionSpec,
	OrganizeCatalog,
	OrganizeCondition,
	OrganizeFactSpec,
	OrganizeMatch,
	OrganizeRule,
	OrganizeRuleInput,
	OrganizeSubject
} from '$lib/types/organize';

export interface DraftCondition {
	uid: string;
	fact: string;
	operator: string;
	value: unknown;
}

export interface DraftAction {
	uid: string;
	action: string;
	config: Record<string, unknown>;
}

export interface RuleDraft {
	id: string | null;
	name: string;
	subject: OrganizeSubject;
	match: OrganizeMatch;
	conditions: DraftCondition[];
	actions: DraftAction[];
	enabled: boolean;
	stop_after: boolean;
}

export const KNOWN_KINDS = ['model_ref', 'enum', 'size', 'number', 'text', 'tag_list', 'bool'] as const;

let uidCounter = 0;
export function nextUid(): string {
	uidCounter += 1;
	return `u${uidCounter}`;
}

export function effectiveKind(spec: Pick<OrganizeFactSpec, 'kind'> | undefined): string {
	if (!spec) return 'text';
	return (KNOWN_KINDS as readonly string[]).includes(spec.kind) ? spec.kind : 'text';
}

export function isListOperator(operator: string): boolean {
	return operator === 'is_any_of';
}

export function defaultValue(kind: string, operator: string, spec?: OrganizeFactSpec): unknown {
	switch (kind) {
		case 'model_ref':
		case 'enum':
			return isListOperator(operator) ? [] : '';
		case 'size': {
			const first = spec?.picker?.presets?.[0];
			return first ? { width: first.width, height: first.height } : { width: 1024, height: 1024 };
		}
		case 'number':
			return typeof spec?.picker?.min === 'number' ? spec.picker.min : 0;
		case 'tag_list':
			return [];
		case 'bool':
			return true;
		default:
			return '';
	}
}

export function coerceValue(kind: string, nextOperator: string, value: unknown, spec?: OrganizeFactSpec): unknown {
	if (kind === 'model_ref' || kind === 'enum') {
		if (isListOperator(nextOperator)) {
			if (Array.isArray(value)) return value;
			return value ? [value] : [];
		}
		if (Array.isArray(value)) return value[0] ?? '';
		return value ?? '';
	}
	return value ?? defaultValue(kind, nextOperator, spec);
}

export function newCondition(spec: OrganizeFactSpec): DraftCondition {
	const operator = spec.operators[0] ?? 'is';
	return {
		uid: nextUid(),
		fact: spec.key,
		operator,
		value: defaultValue(effectiveKind(spec), operator, spec)
	};
}

export function newAction(spec: OrganizeActionSpec): DraftAction {
	const config: Record<string, unknown> = {};
	for (const field of spec.config_schema) {
		if (field.default !== undefined) config[field.key] = field.default;
		else if (field.kind === 'tag_list') config[field.key] = [];
		else if (field.kind === 'bool') config[field.key] = false;
		else if (field.kind === 'text' || field.kind === 'collection') config[field.key] = field.kind === 'text' ? '' : null;
	}
	return { uid: nextUid(), action: spec.key, config };
}

export function emptyDraft(subject: OrganizeSubject): RuleDraft {
	return {
		id: null,
		name: '',
		subject,
		match: 'all',
		conditions: [],
		actions: [],
		enabled: true,
		stop_after: false
	};
}

export function draftFromRule(rule: OrganizeRule): RuleDraft {
	return {
		id: rule.id,
		name: rule.name,
		subject: rule.subject,
		match: rule.match,
		conditions: rule.conditions.map((c) => ({ uid: nextUid(), ...c })),
		actions: rule.actions.map((a) => ({ uid: nextUid(), action: a.action, config: { ...a.config } })),
		enabled: rule.enabled,
		stop_after: rule.stop_after
	};
}

export function draftFromParts(
	subject: OrganizeSubject,
	parts: Partial<{
		name: string;
		match: OrganizeMatch;
		conditions: OrganizeCondition[];
		actions: OrganizeAction[];
	}>
): RuleDraft {
	return {
		...emptyDraft(subject),
		name: parts.name ?? '',
		match: parts.match ?? 'all',
		conditions: (parts.conditions ?? []).map((c) => ({ uid: nextUid(), ...c })),
		actions: (parts.actions ?? []).map((a) => ({ uid: nextUid(), action: a.action, config: { ...a.config } }))
	};
}

function stripUid<T extends { uid: string }>(item: T): Omit<T, 'uid'> {
	const { uid: _uid, ...rest } = item;
	return rest;
}

export function draftConditions(draft: RuleDraft): OrganizeCondition[] {
	return draft.conditions.map((c) => stripUid(c) as OrganizeCondition);
}

export function draftActions(draft: RuleDraft): OrganizeAction[] {
	return draft.actions.map((a) => stripUid(a) as OrganizeAction);
}

export function draftToInput(draft: RuleDraft): OrganizeRuleInput {
	return {
		name: draft.name.trim(),
		subject: draft.subject,
		match: draft.match,
		conditions: draftConditions(draft),
		actions: draftActions(draft),
		enabled: draft.enabled,
		stop_after: draft.stop_after
	};
}

export function draftSignature(draft: RuleDraft): string {
	return JSON.stringify([
		draft.name,
		draft.match,
		draft.enabled,
		draft.stop_after,
		draftConditions(draft),
		draftActions(draft)
	]);
}

export function conditionValueFilled(kind: string, value: unknown): boolean {
	switch (kind) {
		case 'model_ref':
		case 'enum':
			return Array.isArray(value) ? value.length > 0 : typeof value === 'string' && value !== '';
		case 'size': {
			const v = value as { width?: number; height?: number } | null;
			return !!v && Number(v.width) > 0 && Number(v.height) > 0;
		}
		case 'number':
			return typeof value === 'number' && Number.isFinite(value);
		case 'tag_list':
			return Array.isArray(value) && value.length > 0;
		case 'bool':
			return typeof value === 'boolean';
		default:
			return typeof value === 'string' && value.trim() !== '';
	}
}

export function actionTargetFilled(action: DraftAction): boolean {
	if (action.action === 'add_to_collection') {
		const id = action.config.collection_id;
		const name = String(action.config.collection_name ?? '').trim();
		return (typeof id === 'string' && id !== '') || name !== '';
	}
	if (action.action === 'add_tags') {
		const tags = action.config.tags;
		return Array.isArray(tags) && tags.length > 0;
	}
	return true;
}

export function draftProblems(draft: RuleDraft, catalog: OrganizeCatalog | null): string[] {
	const problems: string[] = [];
	if (!draft.name.trim()) problems.push('Give the rule a name.');
	if (draft.actions.length === 0) problems.push('Choose what the rule should do.');
	else if (draft.actions.some((a) => !actionTargetFilled(a))) problems.push('Finish what the rule should do.');
	if (catalog) {
		for (const cond of draft.conditions) {
			const spec = catalog.facts.find((f) => f.key === cond.fact);
			if (!conditionValueFilled(effectiveKind(spec), cond.value)) {
				problems.push('Finish every condition.');
				break;
			}
		}
	}
	return problems;
}

export function factsForSubject(catalog: OrganizeCatalog, subject: OrganizeSubject): OrganizeFactSpec[] {
	return catalog.facts.filter((f) => f.subjects.includes(subject));
}

export function actionsForSubject(catalog: OrganizeCatalog, subject: OrganizeSubject): OrganizeActionSpec[] {
	return catalog.actions.filter((a) => a.subjects.includes(subject));
}

export function supportsTags(catalog: OrganizeCatalog, subject: OrganizeSubject): boolean {
	return catalog.subjects.find((s) => s.key === subject)?.supports_tags ?? false;
}

export function operatorLabel(catalog: OrganizeCatalog | null, operator: string): string {
	return catalog?.operators[operator] ?? operator.replace(/_/g, ' ');
}

export function sizeLabel(value: { width?: number; height?: number } | null | undefined): string {
	if (!value) return '';
	return `${value.width ?? '?'} x ${value.height ?? '?'}`;
}

export interface ValueLabels {
	models: Record<string, string>;
	options: Record<string, Record<string, string>>;
}

export function describeValue(spec: OrganizeFactSpec | undefined, value: unknown, labels: ValueLabels): string {
	const kind = effectiveKind(spec);
	const key = spec?.key ?? '';
	const one = (v: unknown): string => {
		if (kind === 'model_ref') return labels.models[String(v)] ?? 'a model';
		if (kind === 'enum') {
			const fromStatic = spec?.options?.find((o) => o.value === v)?.label;
			return fromStatic ?? labels.options[key]?.[String(v)] ?? String(v);
		}
		return String(v);
	};
	if (kind === 'size') return sizeLabel(value as { width?: number; height?: number });
	if (kind === 'bool') return value ? 'yes' : 'no';
	if (Array.isArray(value)) return value.map(one).join(', ');
	if (kind === 'number') return `${value}${spec?.picker?.unit ? ` ${spec.picker.unit}` : ''}`;
	return one(value);
}
