import { api } from '$lib/services/api/index';
import type { PromptSyntaxSpec } from './promptSyntax';

const EMPTY: PromptSyntaxSpec[] = [];

const cache = new Map<string, Promise<PromptSyntaxSpec[]>>();

function cacheKey(presetId: string, mode: string, formName?: string): string {
	return `${presetId}::${mode}::${formName ?? ''}`;
}

export function getPresetPromptSyntax(
	presetId: string | null | undefined,
	mode: string | null | undefined,
	formName?: string
): Promise<PromptSyntaxSpec[]> {
	if (!presetId || !mode) return Promise.resolve(EMPTY);

	const key = cacheKey(presetId, mode, formName);
	let entry = cache.get(key);
	if (!entry) {
		entry = api
			.getPresetFormSchema(presetId, mode, formName)
			.then((response) => (response.success && response.data ? response.data.prompt_syntax ?? EMPTY : EMPTY))
			.catch((error) => {
				if (cache.get(key) === entry) cache.delete(key);
				throw error;
			});
		cache.set(key, entry);
	}
	return entry.catch(() => EMPTY);
}

export function invalidatePresetPromptSyntaxCache(): void {
	cache.clear();
}
