import { api } from '$lib/services/api/index';
import { parseCloudEstimate, type CloudEstimate, type EstimateShot } from '$lib/utils/cloudDirector';

export async function fetchCloudEstimate(modelId: string, shots: EstimateShot[]): Promise<CloudEstimate | null> {
	const response = await api.getClient().post(`/api/cloud/models/${encodeURIComponent(modelId)}/estimate`, { shots });
	return parseCloudEstimate(response.data);
}
