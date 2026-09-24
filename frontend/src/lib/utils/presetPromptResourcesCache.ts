import { api } from '$lib/services/api/index';
import type { PromptResourceSpec } from './promptResources';

export interface PresetPromptResourcesResult {
	specs: PromptResourceSpec[];
	fieldLabels: Record<string, string>;
}

const EMPTY: PresetPromptResourcesResult = { specs: [], fieldLabels: {} };

const cache = new Map<string, Promise<PresetPromptResourcesResult>>();

function cacheKey(presetId: string, mode: string, formName?: string): string {
	return `${presetId}::${mode}::${formName ?? ''}`;
}

function extractFieldLabels(formSchema: unknown, specs: PromptResourceSpec[]): Record<string, string> {
	const labels: Record<string, string> = {};
	const properties =
		formSchema && typeof formSchema === 'object' ? (formSchema as Record<string, unknown>).properties : undefined;
	if (!properties || typeof properties !== 'object') return labels;
	for (const spec of specs) {
		const config = (properties as Record<string, unknown>)[spec.field];
		const title = config && typeof config === 'object' ? (config as Record<string, unknown>).title : undefined;
		if (typeof title === 'string' && title) labels[spec.field] = title;
	}
	return labels;
}

export function getPresetPromptResources(
	presetId: string | null | undefined,
	mode: string | null | undefined,
	formName?: string
): Promise<PresetPromptResourcesResult> {
	if (!presetId || !mode) return Promise.resolve(EMPTY);

	const key = cacheKey(presetId, mode, formName);
	let entry = cache.get(key);
	if (!entry) {
		entry = api
			.getPresetFormSchema(presetId, mode, formName)
			.then((response) => {
				if (!response.success || !response.data) return EMPTY;
				const specs = response.data.prompt_resources ?? [];
				return { specs, fieldLabels: extractFieldLabels(response.data.form_schema, specs) };
			})
			.catch((error) => {
				if (cache.get(key) === entry) cache.delete(key);
				throw error;
			});
		cache.set(key, entry);
	}
	return entry.catch(() => EMPTY);
}

export function invalidatePresetPromptResourcesCache(): void {
	cache.clear();
}
