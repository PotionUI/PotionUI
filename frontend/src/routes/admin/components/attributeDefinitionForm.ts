// Pure form-shaping logic for AttributeDefinitionForm.svelte / AttributesTab.svelte,
// kept separate so it's unit-testable without mounting a component.

import type { AttributeDefinition, AttributeFieldType, AttributeSelectOption } from '$lib/types/models';

/** Edit-buffer shape: HTML controls only ever produce string|boolean|string[],
 * never the definition's actual wire type. */
export type DraftValue = string | boolean | string[];

export interface AttributeDraft {
	id?: string;
	key: string;
	label: string;
	field_type: AttributeFieldType;
	model_types: string[];
	config: { min?: number; max?: number; step?: number; options: AttributeSelectOption[] };
	default_value: DraftValue;
	description: string;
	per_user: boolean;
	admin_only: boolean;
}

function defaultValueToDraft(fieldType: AttributeFieldType, value: unknown): DraftValue {
	if (fieldType === 'checkbox') return !!value;
	if (fieldType === 'tags') return Array.isArray(value) ? (value as string[]) : [];
	return value === undefined || value === null ? '' : String(value);
}

export function emptyAttributeDraft(): AttributeDraft {
	return {
		key: '',
		label: '',
		field_type: 'text',
		model_types: [],
		config: { options: [] },
		default_value: '',
		description: '',
		per_user: false,
		admin_only: false
	};
}

export function draftFromDefinition(definition: AttributeDefinition): AttributeDraft {
	return {
		id: definition.id,
		key: definition.key,
		label: definition.label,
		field_type: definition.field_type,
		model_types: [...definition.model_types],
		config: {
			min: definition.config.min,
			max: definition.config.max,
			step: definition.config.step,
			options: (definition.config.options ?? []).map((option) => ({ ...option }))
		},
		default_value: defaultValueToDraft(definition.field_type, definition.default_value),
		description: definition.description ?? '',
		per_user: definition.per_user,
		admin_only: definition.admin_only
	};
}

/** Slugs a human label into a machine key: lowercase, every run of
 * non-alphanumerics collapses to one underscore, no leading/trailing underscore. */
export function slugifyAttributeKey(label: string): string {
	return label
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_+|_+$/g, '');
}

export function addSelectOption(options: AttributeSelectOption[]): AttributeSelectOption[] {
	return [...options, { value: '', label: '' }];
}

export function removeSelectOption(options: AttributeSelectOption[], index: number): AttributeSelectOption[] {
	return options.filter((_, i) => i !== index);
}

/** Resets config/default_value for a newly-picked field type, so switching
 * e.g. select -> checkbox doesn't leave stale options/min/max behind. */
export function resetDraftForFieldType(draft: AttributeDraft, fieldType: AttributeFieldType): AttributeDraft {
	return {
		...draft,
		field_type: fieldType,
		config: fieldType === 'select' ? { options: [{ value: '', label: '' }] } : { options: [] },
		default_value: defaultValueToDraft(fieldType, undefined)
	};
}

function coerceDraftDefaultValue(fieldType: AttributeFieldType, raw: DraftValue): unknown {
	if (fieldType === 'checkbox') return !!raw;
	if (fieldType === 'tags') return Array.isArray(raw) ? raw : [];
	if (fieldType === 'slider' || fieldType === 'number' || fieldType === 'range') {
		const num = typeof raw === 'string' ? parseFloat(raw) : typeof raw === 'number' ? raw : NaN;
		if (!Number.isNaN(num)) return num;
		// A range attribute states what a model declares, so "nothing entered"
		// has to stay expressible - falling back to 0 the way a slider does
		// would have every new range claim a recommended band of 0.
		return fieldType === 'range' ? null : 0;
	}
	return typeof raw === 'string' ? raw : String(raw ?? '');
}

function numberOrUndefined(value: number | undefined): number | undefined {
	return value === undefined || value === null || Number.isNaN(value) ? undefined : value;
}

/** Builds the API payload from a draft - strips config keys the field type
 * doesn't use (a stray `min` surviving a slider -> text switch would otherwise
 * ride along on the wire) and coerces the default value back to its wire shape. */
export function buildAttributeDefinitionPayload(
	draft: AttributeDraft
): Omit<AttributeDefinition, 'id' | 'system' | 'source'> {
	const config: AttributeDefinition['config'] = {};
	if (draft.field_type === 'slider' || draft.field_type === 'number' || draft.field_type === 'range') {
		const min = numberOrUndefined(draft.config.min);
		const max = numberOrUndefined(draft.config.max);
		const step = numberOrUndefined(draft.config.step);
		if (min !== undefined) config.min = min;
		if (max !== undefined) config.max = max;
		if (step !== undefined) config.step = step;
	}
	if (draft.field_type === 'select') {
		config.options = draft.config.options.filter((option) => option.value.trim() !== '');
	}
	return {
		key: draft.key.trim(),
		label: draft.label.trim(),
		field_type: draft.field_type,
		model_types: draft.model_types,
		config,
		default_value: coerceDraftDefaultValue(draft.field_type, draft.default_value),
		description: draft.description.trim() || undefined,
		per_user: draft.per_user,
		admin_only: draft.admin_only
	};
}
