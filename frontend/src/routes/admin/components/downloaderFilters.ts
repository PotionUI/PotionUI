import type { SortOption } from '$lib/components/library/librarySection';
import type { Download } from '$lib/stores/downloads';
import type { DownloadLibrarySection } from './downloads/downloadLibrarySections';

export type DownloadSortBy = 'created_at' | 'filename';

export interface DownloaderFilters {
	q: string;
	sortBy: DownloadSortBy;
}

export const DEFAULT_DOWNLOADER_FILTERS: DownloaderFilters = {
	q: '',
	sortBy: 'created_at'
};

export const DOWNLOADER_SORT_OPTIONS: readonly SortOption<DownloadSortBy>[] = [
	{ value: 'created_at', label: 'Newest' },
	{ value: 'filename', label: 'Name A–Z' }
];

const NON_ALL_SECTIONS: readonly DownloadLibrarySection[] = ['active', 'pending', 'completed', 'failed'];

export function matchesDownloadSection(
	download: Pick<Download, 'status'>,
	section: DownloadLibrarySection
): boolean {
	switch (section) {
		case 'active':
			return download.status === 'downloading' || download.status === 'paused';
		case 'pending':
			return download.status === 'pending';
		case 'completed':
			return download.status === 'completed';
		case 'failed':
			return download.status === 'failed' || download.status === 'cancelled';
		default:
			return true;
	}
}

export function downloadSectionCounts(
	downloads: readonly Download[]
): Partial<Record<DownloadLibrarySection, number>> {
	const counts: Partial<Record<DownloadLibrarySection, number>> = { all: downloads.length };
	for (const section of NON_ALL_SECTIONS) {
		counts[section] = downloads.filter((download) => matchesDownloadSection(download, section)).length;
	}
	return counts;
}

export function applyDownloaderFilters(
	downloads: readonly Download[],
	filters: DownloaderFilters,
	section: DownloadLibrarySection
): Download[] {
	const query = filters.q.trim().toLowerCase();
	const rows = downloads.filter((download) => {
		if (!matchesDownloadSection(download, section)) return false;
		if (!query) return true;
		return (
			download.filename.toLowerCase().includes(query) || download.url.toLowerCase().includes(query)
		);
	});
	if (filters.sortBy === 'filename') {
		return rows.sort((a, b) => a.filename.localeCompare(b.filename));
	}
	return rows;
}
