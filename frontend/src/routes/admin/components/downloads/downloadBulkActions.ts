import type { Download } from '$lib/stores/downloads';

export type DownloadBulkAction = 'retry' | 'cancel' | 'remove';

export function availableDownloadBulkActions(
	downloads: readonly Download[],
	selectedIds: ReadonlySet<string>
): Set<DownloadBulkAction> {
	const actions = new Set<DownloadBulkAction>();
	for (const download of downloads) {
		if (!selectedIds.has(download.id)) continue;
		if (download.status === 'failed') actions.add('retry');
		if (download.status === 'pending' || download.status === 'downloading' || download.status === 'paused') {
			actions.add('cancel');
		}
		if (download.status === 'completed' || download.status === 'failed' || download.status === 'cancelled') {
			actions.add('remove');
		}
	}
	return actions;
}
