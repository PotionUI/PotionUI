import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
import { MODEL_TYPE_UNDEFINED, modelTypePresentation } from '$lib/utils/modelPresentation';

export const MODELS_ALL_SECTION = 'all';
export const MODELS_ATTRIBUTES_SECTION = 'attributes';
export const MODELS_FOLDERS_SECTION = 'folders';

export interface ModelTypeCount {
	type: string;
	count: number;
}

export interface ModelTypeRow {
	type: string;
	label: string;
	count: number;
	attention: boolean;
}

export const MODEL_LIBRARY_SECTIONS: readonly LibrarySectionMeta<string>[] = [
	{ id: MODELS_ALL_SECTION, label: 'All models', icon: 'cube' },
	{ id: MODELS_ATTRIBUTES_SECTION, label: 'Attributes', icon: 'sliders' },
	{ id: MODELS_FOLDERS_SECTION, label: 'Folders', icon: 'folder' }
];

export function modelTypeRows(modelTypes: readonly ModelTypeCount[], selectedType?: string): ModelTypeRow[] {
	const rows = modelTypes
		.filter((entry) => entry.type !== MODEL_TYPE_UNDEFINED || entry.count > 0 || entry.type === selectedType)
		.map((entry) => ({
			type: entry.type,
			label: modelTypePresentation(entry.type).label,
			count: entry.count,
			attention: entry.type === MODEL_TYPE_UNDEFINED
		}));
	return [...rows.filter((row) => row.attention), ...rows.filter((row) => !row.attention)];
}

export function modelLibraryShellSection(section: string): string {
	if (section === MODELS_ATTRIBUTES_SECTION) return MODELS_ATTRIBUTES_SECTION;
	if (section === MODELS_FOLDERS_SECTION) return MODELS_FOLDERS_SECTION;
	return MODELS_ALL_SECTION;
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
