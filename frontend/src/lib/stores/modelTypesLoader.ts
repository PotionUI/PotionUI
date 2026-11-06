import { api } from '$lib/services/api/index';
import { setModelTypes } from '$lib/stores/modelTypes.svelte';
import { logger } from '$lib/utils/logger';

export async function refreshModelTypes(): Promise<void> {
	try {
		const response = await api.getModelTypeDefinitions();
		if (!response?.success) throw new Error(response?.message || 'Model types request was unsuccessful');
		setModelTypes(response.data?.types ?? []);
	} catch (err) {
		logger.error('Failed to load model types:', err);
	}
}
