import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import type { Download } from '$lib/stores/downloads';

export type DownloadStatusFilter = 'all' | 'active' | 'pending' | 'completed' | 'failed';
export type DownloadSortBy = 'created_at' | 'filename';

export interface DownloaderFilters {
	q: string;
	status: DownloadStatusFilter;
	sortBy: DownloadSortBy;
}

export const DEFAULT_DOWNLOADER_FILTERS: DownloaderFilters = {
	q: '',
	status: 'all',
	sortBy: 'created_at'
};

export const DOWNLOAD_STATUS_OPTIONS: ReadonlyArray<{ value: DownloadStatusFilter; label: string }> = [
	{ value: 'all', label: 'All' },
	{ value: 'active', label: 'Active' },
	{ value: 'pending', label: 'Pending' },
	{ value: 'completed', label: 'Done' },
	{ value: 'failed', label: 'Failed' }
];

export const DOWNLOADER_SORT_OPTIONS: readonly SortOption<DownloadSortBy>[] = [
	{ value: 'created_at', label: 'Newest' },
	{ value: 'filename', label: 'Name A–Z' }
];

const STATUS_FIELD: FilterFieldDescriptor<DownloaderFilters> = {
	kind: 'enum',
	key: 'status',
	param: 'status',
	label: 'Status',
	values: ['active', 'pending', 'completed', 'failed'],
	default: 'all',
	chipLabel: (value) => DOWNLOAD_STATUS_OPTIONS.find((option) => option.value === value)?.label ?? value
};

const codec = createFilterCodec<DownloaderFilters>({
	defaults: DEFAULT_DOWNLOADER_FILTERS,
	fields: [STATUS_FIELD],
	sortValues: ['created_at', 'filename']
});

export function downloaderFilterChips(filters: DownloaderFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearDownloaderFilterChip(filters: DownloaderFilters, key: string): DownloaderFilters {
	return codec.clearChip(filters, key);
}

export function clearAllDownloaderFilters(filters: DownloaderFilters): DownloaderFilters {
	return codec.clearAll(filters);
}

export function downloaderFilterActiveCount(filters: DownloaderFilters): number {
	return codec.activeCount(filters);
}

function matchesStatus(download: Pick<Download, 'status'>, status: DownloadStatusFilter): boolean {
	switch (status) {
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

export function applyDownloaderFilters(
	downloads: readonly Download[],
	filters: DownloaderFilters
): Download[] {
	const query = filters.q.trim().toLowerCase();
	const rows = downloads.filter((download) => {
		if (!matchesStatus(download, filters.status)) return false;
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
