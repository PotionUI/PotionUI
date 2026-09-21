import type { FieldConfig } from '$lib/form/reactions';
import { fromComponentValue } from '$lib/utils/presetFormOverrides';

const MODEL_FIELD_TYPES = new Set(['model', 'models']);

export interface MissingModelField {
	name: string;
	label: string;
}

export function missingRequiredModelField(
	fields: FieldConfig[],
	formData: Record<string, unknown> | null | undefined
): MissingModelField | null {
	const data = formData ?? {};

	for (const field of fields ?? []) {
		if (field.name && field.type && MODEL_FIELD_TYPES.has(field.type)) {
			if (field.required && !field.readonly) {
				const wireValue = fromComponentValue(field.type, data[field.name]);
				if (typeof wireValue !== 'string' || wireValue.trim() === '') {
					return { name: field.name, label: field.title || field.name };
				}
			}
		}

		if (Array.isArray(field.children)) {
			const nested = missingRequiredModelField(field.children, data);
			if (nested) return nested;
		}
	}

	return null;
}
