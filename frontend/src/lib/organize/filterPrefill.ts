import { api } from '$lib/services/api/index';
import { modelDisplayName } from '$lib/utils/modelDisplay';
import type { GenerationHistoryFilters } from '$lib/types/history';
import type { LibraryFilters } from '$lib/library/libraryQuery';
import type { OrganizeCondition } from '$lib/types/organize';
import type { OrganizeStartRule } from './handoff';

export interface FilterPrefillContext {
	tagNames: (ids: string[]) => string[];
	resolveModelId?: (name: string) => Promise<string | null>;
}

export async function resolveModelIdByName(name: string): Promise<string | null> {
	try {
		const response = await api.getModels({ search: name, limit: 25 });
		const models: any[] = response.success ? (response.data?.models ?? []) : [];
		const wanted = name.trim().toLowerCase();
		const hit = models.find(
			(m) =>
				modelDisplayName(m).trim().toLowerCase() === wanted ||
				String(m.filename ?? '').trim().toLowerCase() === wanted
		);
		return hit?.id ?? null;
	} catch {
		return null;
	}
}

export function hasConvertibleHistoryFilters(filters: GenerationHistoryFilters): boolean {
	return (
		!!filters.modelName ||
		filters.mediaType !== 'all' ||
		!!filters.presetId ||
		!!filters.mode ||
		filters.selectedTagIds.length > 0
	);
}

export function hasConvertibleLibraryFilters(filters: LibraryFilters): boolean {
	return filters.mediaType !== 'all' || filters.selectedTagIds.length > 0;
}

function tagCondition(names: string[]): OrganizeCondition[] {
	return names.length > 0 ? [{ fact: 'tags', operator: 'has', value: names }] : [];
}

export async function historyFilterRule(
	filters: GenerationHistoryFilters,
	context: FilterPrefillContext
): Promise<OrganizeStartRule> {
	const conditions: OrganizeCondition[] = [];
	if (filters.modelName) {
		const resolve = context.resolveModelId ?? resolveModelIdByName;
		const id = await resolve(filters.modelName);
		if (id) conditions.push({ fact: 'model', operator: 'is', value: id });
	}
	if (filters.mediaType !== 'all') {
		conditions.push({ fact: 'media_kind', operator: 'is', value: filters.mediaType });
	}
	if (filters.presetId) conditions.push({ fact: 'preset', operator: 'is', value: filters.presetId });
	if (filters.mode) conditions.push({ fact: 'mode', operator: 'is', value: filters.mode });
	conditions.push(...tagCondition(context.tagNames(filters.selectedTagIds)));
	return { subject: 'generation', conditions };
}

export function libraryFilterRule(filters: LibraryFilters, context: FilterPrefillContext): OrganizeStartRule {
	const conditions: OrganizeCondition[] = [];
	if (filters.mediaType !== 'all') {
		conditions.push({ fact: 'media_kind', operator: 'is', value: filters.mediaType });
	}
	conditions.push(...tagCondition(context.tagNames(filters.selectedTagIds)));
	return { subject: 'upload', conditions };
}
