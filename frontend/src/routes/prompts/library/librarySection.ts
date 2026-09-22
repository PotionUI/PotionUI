export type LibrarySection = 'prompts' | 'segments' | 'templates' | 'categories';

export interface LibrarySectionMeta {
	id: LibrarySection;
	label: string;
	icon: string;
	primaryLabel: string;
	searchPlaceholder: string;
}

export interface SortOption<T extends string = string> {
	value: T;
	label: string;
}

export interface FilterChip {
	key: string;
	label: string;
}

export const LIBRARY_SECTIONS: readonly LibrarySectionMeta[] = [
	{ id: 'prompts', label: 'Prompts', icon: 'document', primaryLabel: 'New prompt', searchPlaceholder: 'Search prompts…' },
	{ id: 'segments', label: 'Segments', icon: 'list', primaryLabel: 'New segment', searchPlaceholder: 'Search segments…' },
	{
		id: 'templates',
		label: 'Templates',
		icon: 'layout-template',
		primaryLabel: 'New template',
		searchPlaceholder: 'Search templates…'
	},
	{
		id: 'categories',
		label: 'Categories',
		icon: 'folder',
		primaryLabel: 'New category',
		searchPlaceholder: 'Search categories…'
	}
];

const SECTION_IDS = LIBRARY_SECTIONS.map((section) => section.id);

export function isLibrarySection(value: string | null | undefined): value is LibrarySection {
	return !!value && (SECTION_IDS as string[]).includes(value);
}

export function sectionFromSearchParams(params: URLSearchParams): LibrarySection {
	const value = params.get('section');
	return isLibrarySection(value) ? value : 'prompts';
}

export function sectionMeta(section: LibrarySection): LibrarySectionMeta {
	return LIBRARY_SECTIONS.find((entry) => entry.id === section) ?? LIBRARY_SECTIONS[0];
}

export function sectionHref(section: LibrarySection, params: Record<string, string> = {}): string {
	const search = new URLSearchParams();
	if (section !== 'prompts') search.set('section', section);
	for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
	const query = search.toString();
	return query ? `/prompts?${query}` : '/prompts';
}

export function withSection(params: URLSearchParams, section: LibrarySection): URLSearchParams {
	const next = new URLSearchParams(params);
	if (section === 'prompts') next.delete('section');
	else next.set('section', section);
	return next;
}

export function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
	return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}
