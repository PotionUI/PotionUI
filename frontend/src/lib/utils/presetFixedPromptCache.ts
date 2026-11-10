import { api } from '$lib/services/api/index';
import type { PresetFixedPrompt } from '$lib/types/api';
import { normalizeFixedPrompt } from './presetFixedPrompt';

type Loader = (presetId: string) => Promise<{ success: boolean; data?: PresetFixedPrompt }>;

const cache = new Map<string, Promise<PresetFixedPrompt | null>>();

export function getPresetFixedPrompt(
	presetId: string | null | undefined,
	load: Loader = (id) => api.getPresetFixedPrompt(id)
): Promise<PresetFixedPrompt | null> {
	if (!presetId) return Promise.resolve(null);
	let entry = cache.get(presetId);
	if (!entry) {
		const pending: Promise<PresetFixedPrompt | null> = load(presetId).then(
			(response) => (response?.success && response.data ? normalizeFixedPrompt(response.data) : null),
			(error) => {
				const status = (error as { response?: { status?: number } } | null)?.response?.status;
				if (status !== 403 && status !== 404 && cache.get(presetId) === pending) cache.delete(presetId);
				return null;
			}
		);
		entry = pending;
		cache.set(presetId, entry);
	}
	return entry;
}

export function invalidatePresetFixedPromptCache(presetId?: string): void {
	if (presetId) cache.delete(presetId);
	else cache.clear();
}
