import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export type AutomationsLibrarySection = 'automations' | 'templates';

export const AUTOMATIONS_LIBRARY_SECTIONS: readonly LibrarySectionMeta<AutomationsLibrarySection>[] = [
	{ id: 'automations', label: 'Automations', icon: 'bolt' },
	{ id: 'templates', label: 'Templates', icon: 'copy' }
];
