import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export type LibrarySection = 'prompts' | 'segments' | 'templates' | 'categories';

export const LIBRARY_SECTIONS: readonly LibrarySectionMeta<LibrarySection>[] = [
	{ id: 'prompts', label: 'Prompts', icon: 'document' },
	{ id: 'segments', label: 'Segments', icon: 'list' },
	{ id: 'templates', label: 'Templates', icon: 'layout-template' },
	{ id: 'categories', label: 'Categories', icon: 'folder' }
];

const SECTION_IDS = LIBRARY_SECTIONS.map((section) => section.id);

export function isLibrarySection(value: string | null | undefined): value is LibrarySection {
	return !!value && (SECTION_IDS as string[]).includes(value);
}

export function sectionFromSearchParams(params: URLSearchParams): LibrarySection {
	const value = params.get('section');
	return isLibrarySection(value) ? value : 'prompts';
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
