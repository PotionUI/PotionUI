import type { LibrarySectionMeta } from '$lib/components/library/librarySection';

export type DownloadLibrarySection = 'all' | 'active' | 'pending' | 'completed' | 'failed';

export const DOWNLOAD_LIBRARY_SECTIONS: readonly LibrarySectionMeta<DownloadLibrarySection>[] = [
	{ id: 'all', label: 'All downloads', icon: 'download' },
	{ id: 'active', label: 'Active', icon: 'bolt' },
	{ id: 'pending', label: 'Pending', icon: 'clock' },
	{ id: 'completed', label: 'Completed', icon: 'check-circle' },
	{ id: 'failed', label: 'Failed', icon: 'warning' }
];
