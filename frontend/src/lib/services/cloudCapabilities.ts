import { api } from '$lib/services/api/index';
import type { CloudCapabilities } from '$lib/form/capabilityBinder';
import { modelIsCloud } from '$lib/utils/modelPresentation';

export class NotCloudModelError extends Error {
	constructor(modelId: string) {
		super(`Model ${modelId} is not a cloud model`);
		this.name = 'NotCloudModelError';
	}
}

const cloudKindById = new Map<string, boolean>();

export function noteModelKind(model: { id?: string | null; model_type?: string | null } | null | undefined): void {
	if (model?.id) cloudKindById.set(model.id, modelIsCloud(model));
}

export function forgetModelKinds(): void {
	cloudKindById.clear();
}

async function isCloudModel(modelId: string): Promise<boolean> {
	const known = cloudKindById.get(modelId);
	if (known !== undefined) return known;
	const response = await api.getModelById(modelId);
	const model = response?.success ? response.data?.model : null;
	if (!model) throw new Error(`Model ${modelId} could not be resolved`);
	noteModelKind({ ...model, id: modelId });
	return modelIsCloud(model);
}

export async function fetchCloudCapabilities(modelId: string): Promise<CloudCapabilities> {
	if (!(await isCloudModel(modelId))) throw new NotCloudModelError(modelId);
	const response = await api.getClient().get(`/api/cloud/models/${encodeURIComponent(modelId)}/capabilities`);
	const body = response.data;
	const data = body && typeof body === 'object' && 'data' in body && body.data ? body.data : body;
	if (!data || !Array.isArray(data.params)) throw new Error('Unexpected capabilities response');
	return { ...data, inputs: Array.isArray(data.inputs) ? data.inputs : [] } as CloudCapabilities;
}
