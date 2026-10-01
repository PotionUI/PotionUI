import { api } from '$lib/services/api/index';
import { getCachedSchema } from '$lib/form/schemaCache';

export function loadFormSchema(presetId: string, mode: string, variant?: string | null, force = false): Promise<unknown> {
	const form = variant ?? undefined;
	return getCachedSchema(
		presetId,
		mode,
		async () => {
			const response = await api.getPresetFormSchema(presetId, mode, form);
			if (!response.success || !response.data?.form_schema) {
				throw new Error(response.error || 'The preset did not return a form schema.');
			}
			return response.data.form_schema;
		},
		force,
		form
	);
}
