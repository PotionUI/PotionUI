import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export const MODELS_ALL_SECTION = 'all';
export const MODELS_ATTRIBUTES_SECTION = 'attributes';

export interface ModelTypeCount {
	type: string;
	count: number;
}

export function buildModelLibrarySections(modelTypes: readonly ModelTypeCount[]): LibrarySectionMeta<string>[] {
	return [
		{ id: MODELS_ALL_SECTION, label: 'All models', icon: 'cube' },
		...modelTypes.map((entry) => ({ id: entry.type, label: entry.type.toUpperCase(), icon: 'model' })),
		{ id: MODELS_ATTRIBUTES_SECTION, label: 'Attributes', icon: 'sliders' }
	];
}

export function modelLibrarySectionCounts(
	modelTypes: readonly ModelTypeCount[],
	attributesCount: number
): Record<string, number> {
	const counts: Record<string, number> = {
		[MODELS_ALL_SECTION]: modelTypes.reduce((sum, entry) => sum + entry.count, 0),
		[MODELS_ATTRIBUTES_SECTION]: attributesCount
	};
	for (const entry of modelTypes) counts[entry.type] = entry.count;
	return counts;
}
