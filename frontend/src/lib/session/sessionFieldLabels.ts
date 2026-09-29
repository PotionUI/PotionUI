import { api } from '$lib/services/api/index';
import { getCachedSchema } from '$lib/form/schemaCache';
import { extractAllFields } from '$lib/form/reactions';
import type { FieldLabels } from '$lib/session/sessionDrawerModel';

export function fieldLabelsFromSchema(schema: unknown): FieldLabels {
	const labels: FieldLabels = {};
	for (const field of extractAllFields(schema)) {
		const title = typeof field.title === 'string' ? field.title.trim() : '';
		if (field.name && title && !(field.name in labels)) labels[field.name] = title;
	}
	return labels;
}

export async function loadFieldLabels(presetId: string | null, mode: string | null): Promise<FieldLabels> {
	if (!presetId || !mode) return {};
	try {
		const schema = await getCachedSchema(presetId, mode, async () => {
			const response = await api.getPresetFormSchema(presetId, mode);
			if (!response.success || !response.data?.form_schema) {
				throw new Error(response.error || 'The preset did not return a form schema.');
			}
			return response.data.form_schema;
		});
		return fieldLabelsFromSchema(schema);
	} catch {
		return {};
	}
}
