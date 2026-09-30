import { api } from '$lib/services/api/index';
import type { CloudCapabilities } from '$lib/form/capabilityBinder';

export async function fetchCloudCapabilities(modelId: string): Promise<CloudCapabilities> {
	const response = await api.getClient().get(`/api/cloud/models/${encodeURIComponent(modelId)}/capabilities`);
	const body = response.data;
	const data = body && typeof body === 'object' && 'data' in body && body.data ? body.data : body;
	if (!data || !Array.isArray(data.params)) throw new Error('Unexpected capabilities response');
	return { ...data, inputs: Array.isArray(data.inputs) ? data.inputs : [] } as CloudCapabilities;
}
