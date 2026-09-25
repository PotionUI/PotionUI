import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
import { modelTypePresentation } from '$lib/utils/modelPresentation';

export const MODELS_ALL_SECTION = 'all';
export const MODELS_ATTRIBUTES_SECTION = 'attributes';

export interface ModelTypeCount {
	type: string;
	count: number;
}

export interface ModelTypeRow {
	type: string;
	label: string;
	count: number;
}

export const MODEL_LIBRARY_SECTIONS: readonly LibrarySectionMeta<string>[] = [
	{ id: MODELS_ALL_SECTION, label: 'All models', icon: 'cube' },
	{ id: MODELS_ATTRIBUTES_SECTION, label: 'Attributes', icon: 'sliders' }
];

export function modelTypeRows(modelTypes: readonly ModelTypeCount[]): ModelTypeRow[] {
	return modelTypes.map((entry) => ({
		type: entry.type,
		label: modelTypePresentation(entry.type).label,
		count: entry.count
	}));
}

export function modelLibraryShellSection(section: string): string {
	return section === MODELS_ATTRIBUTES_SECTION ? MODELS_ATTRIBUTES_SECTION : MODELS_ALL_SECTION;
}

export function modelLibrarySectionCounts(
	modelTypes: readonly ModelTypeCount[],
	attributesCount: number
): Record<string, number> {
	return {
		[MODELS_ALL_SECTION]: modelTypes.reduce((sum, entry) => sum + entry.count, 0),
		[MODELS_ATTRIBUTES_SECTION]: attributesCount
	};
}
