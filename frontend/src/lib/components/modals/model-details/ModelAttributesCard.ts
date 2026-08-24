// Pure definition-driven logic for ModelAttributesCard.svelte, kept separate
// so it's unit-testable without mounting a component.

import type { AttributeDefinition, AttributeFieldType } from '$lib/types/models';

export type AttributeInputType = 'number' | 'text' | 'checkbox' | 'select' | 'tags';

export interface AttributeInputConfig {
	type: AttributeInputType;
	min?: number;
	max?: number;
	step?: number;
	options?: { value: string; label: string }[];
}

/** Decides the HTML input shape for a definition from its `field_type`.
 * `slider` renders as a plain number input honoring the declared min/max/step
 * (see ModelAttributesCard.svelte) - there is no drag-slider widget here. */
export function inputConfigForAttribute(definition: AttributeDefinition): AttributeInputConfig {
	switch (definition.field_type) {
		case 'checkbox':
			return { type: 'checkbox' };
		case 'select':
			return { type: 'select', options: definition.config.options ?? [] };
		case 'tags':
			return { type: 'tags' };
		case 'slider':
		case 'number':
			return {
				type: 'number',
				min: definition.config.min,
				max: definition.config.max,
				step: definition.config.step
			};
		default:
			return { type: 'text' };
	}
}

/** Definitions applicable to a model type: an empty `model_types` means "every
 * type", otherwise the model's type must be listed explicitly. */
export function definitionsForModelType(
	definitions: AttributeDefinition[],
	modelType: string
): AttributeDefinition[] {
	return definitions.filter(
		(definition) => definition.model_types.length === 0 || definition.model_types.includes(modelType)
	);
}

/** Effective value for a definition: the requesting user's own overlay wins,
 * then the shared (admin-set) value, then the definition's declared default. */
export function resolveEffectiveAttributeValue(
	definition: AttributeDefinition,
	sharedValues: Record<string, unknown> | null | undefined,
	userValues: Record<string, unknown> | null | undefined
): unknown {
	const userValue = userValues?.[definition.key];
	if (userValue !== undefined) return userValue;
	const sharedValue = sharedValues?.[definition.key];
	if (sharedValue !== undefined) return sharedValue;
	return definition.default_value;
}

function coerceNumberInput(raw: unknown, fallback: unknown): unknown {
	const num = typeof raw === 'string' ? parseFloat(raw) : typeof raw === 'number' ? raw : NaN;
	return Number.isNaN(num) ? fallback : num;
}

/** Coerces a raw edit-buffer value back to the shape its `field_type` puts on
 * the wire. Edit buffers are always string|boolean|string[] (whatever the
 * matching HTML control produces). */
export function coerceAttributeInput(
	definition: AttributeDefinition,
	raw: string | boolean | string[]
): unknown {
	switch (definition.field_type) {
		case 'checkbox':
			return !!raw;
		case 'tags':
			return Array.isArray(raw) ? raw : [];
		case 'slider':
		case 'number':
			return coerceNumberInput(raw, definition.default_value);
		default:
			return String(raw);
	}
}

/** Display string for a definition's read-only (non-editing) value. */
export function formatAttributeValue(definition: AttributeDefinition, value: unknown): string {
	if (definition.field_type === 'checkbox') return value ? 'Yes' : 'No';
	if (definition.field_type === 'tags') {
		return Array.isArray(value) && value.length > 0 ? value.join(', ') : '—';
	}
	if (definition.field_type === 'select') {
		const option = definition.config.options?.find((o) => o.value === value);
		if (option) return option.label;
	}
	if (value === null || value === undefined || value === '') return '—';
	return String(value);
}

/** `PUT /{id}/metadata` and `PUT /{id}/attributes/user` both echo back the
 * server-coerced values - `.../metadata` nests them at `data.model.model_metadata`
 * (same shape the old trigger-words endpoint used), `.../attributes/user` returns
 * them flat at `data.values`. Falls back to the locally-computed `values` only if
 * the server didn't echo anything back. */
export function extractUpdatedSharedMetadata(
	responseData: { model?: { model_metadata?: Record<string, unknown> | null } } | null | undefined,
	values: Record<string, unknown>
): Record<string, unknown> {
	return responseData?.model?.model_metadata ?? values;
}

export function extractUpdatedUserMetadata(
	responseData: { values?: Record<string, unknown> } | null | undefined,
	values: Record<string, unknown>
): Record<string, unknown> {
	return responseData?.values ?? values;
}

/** Which field types render a chip/tags editor rather than a bare input -
 * used by the card to decide when to reach for TagsChipInput. */
export function isTagsAttribute(fieldType: AttributeFieldType): boolean {
	return fieldType === 'tags';
}
