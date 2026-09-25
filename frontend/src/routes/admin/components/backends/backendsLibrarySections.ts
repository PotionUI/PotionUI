import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export type BackendLibrarySection = 'all';

export const BACKENDS_LIBRARY_SECTIONS: readonly LibrarySectionMeta<BackendLibrarySection>[] = [
	{ id: 'all', label: 'All backends', icon: 'server' }
];
