import { api } from '$lib/services/api/index';
import { tagCatalog } from '$lib/stores/tagCatalog';
import { fetchCloudCapabilities } from '$lib/services/cloudCapabilities';
import { getCachedSchema } from './schemaCache';
import { buildModelSearchRequest } from '$lib/utils/modelSearchParams';
import { findModelForValue, MODEL_REF_PREFIX } from '$lib/utils/modelRef';
import { sharedCapabilityCache } from './capabilityTracker';
import {
	resolveSlotTagIds,
	slotSearchRequest,
	type AutopickSearchResult,
	type AutopickSlot,
	type SlotVerdict
} from './modelSlotAutopick';

export function loadPresetFormSchema(
	presetId: string,
	mode: string,
	variant: string | undefined,
	force = false
): Promise<any> {
	return getCachedSchema(
		presetId,
		mode,
		async () => {
			const response = await api.getPresetFormSchema(presetId, mode, variant);
			if (!response.success || !response.data?.form_schema) {
				throw new Error(response.error || 'The preset did not return a form schema.');
			}
			return response.data.form_schema;
		},
		force,
		variant
	);
}

export async function searchAutopickSlot(slot: AutopickSlot): Promise<AutopickSearchResult | null> {
	let tagIds: string[] = [];
	if (slot.tagFilters.length > 0) {
		const tags = await tagCatalog.get('MODEL');
		const resolved = resolveSlotTagIds(slot.tagFilters, tags);
		if (!resolved) return null;
		tagIds = resolved;
	}
	const request = slotSearchRequest(slot, tagIds);
	if (request.kind !== 'preset') return null;
	const response = await api.getPresetModels(request.presetId, request.modelType, request.search, request.opts);
	if (!response.success || !response.data) return null;
	return { models: response.data.models ?? [], total: response.data.total };
}

export async function loadCloudCapabilities(modelId: string): Promise<void> {
	if (sharedCapabilityCache.isFresh(modelId)) return;
	try {
		sharedCapabilityCache.set(modelId, await fetchCloudCapabilities(modelId));
	} catch {
		return;
	}
}

export async function findInstalledModel(slot: AutopickSlot, value: string): Promise<{ id?: string | null } | null> {
	const filename = value.split('/').pop() ?? value;
	const request = buildModelSearchRequest({
		modelType: slot.modelType,
		presetId: slot.presetId,
		searchQuery: filename,
		limit: 10,
		modelTypes: slot.modelTypes
	});
	if (request.kind !== 'preset') return null;
	const response = await api.getPresetModels(request.presetId, request.modelType, request.search, request.opts);
	if (!response.success || !response.data) return null;
	return findModelForValue(value, response.data.models ?? []) ?? null;
}

export async function checkSlotValue(slot: AutopickSlot, wire: string): Promise<SlotVerdict> {
	try {
		let name = wire;
		if (wire.startsWith(MODEL_REF_PREFIX)) {
			const response = await api.getModelById(wire.slice(MODEL_REF_PREFIX.length));
			if (!response?.success) return response?.error === 'model_not_found' ? 'unusable' : 'unknown';
			const filename = response.data?.model?.filename;
			if (!filename) return 'unknown';
			name = filename;
		}
		const request = buildModelSearchRequest({
			modelType: slot.modelType,
			presetId: slot.presetId,
			searchQuery: name.split('/').pop() ?? name,
			limit: 10,
			modelTypes: slot.modelTypes
		});
		if (request.kind !== 'preset') return 'unknown';
		const response = await api.getPresetModels(request.presetId, request.modelType, request.search, request.opts);
		if (!response?.success || !response.data) return 'unknown';
		return findModelForValue(wire, response.data.models ?? []) ? 'usable' : 'unusable';
	} catch {
		return 'unknown';
	}
}
