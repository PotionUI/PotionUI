import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import type { LibrarySection } from './librarySection';

export type LibraryCounts = Partial<Record<LibrarySection, number>>;

export const libraryCounts = writable<LibraryCounts>({});

export function setLibraryCount(section: LibrarySection, count: number) {
	libraryCounts.update((counts) => (counts[section] === count ? counts : { ...counts, [section]: count }));
}

let primed = false;

export async function primeLibraryCounts(force = false) {
	if (primed && !force) return;
	primed = true;
	const [segments, templates, categories, prompts] = await Promise.allSettled([
		api.listSavedSegments(),
		api.listSegmentTemplates(),
		api.listSegmentCategories(),
		api.listPrompts({ limit: 1, offset: 0 })
	]);
	if (segments.status === 'fulfilled' && segments.value.data)
		setLibraryCount('segments', segments.value.data.segments.length);
	if (templates.status === 'fulfilled' && templates.value.data)
		setLibraryCount('templates', templates.value.data.templates.length);
	if (categories.status === 'fulfilled' && categories.value.data)
		setLibraryCount('categories', categories.value.data.categories.length);
	if (prompts.status === 'fulfilled' && prompts.value.data)
		setLibraryCount('prompts', prompts.value.data.total ?? 0);
}
