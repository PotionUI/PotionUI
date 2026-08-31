import type { RemoteModelSyncRow, RemoteModelSyncStatus, WorkerModelTransfer } from '$lib/services/admin-api';

/** A transfer's `relative_path` carries a model-type directory prefix
 * (e.g. `loras/foo.safetensors`) a sync row's bare `filename` doesn't -
 * match on the trailing path segment, never a raw substring, so
 * `odel.safetensors` cannot match `model.safetensors`. */
export function transferMatchesFilename(transfer: WorkerModelTransfer, filename: string): boolean {
	return transfer.relative_path === filename || transfer.relative_path.endsWith(`/${filename}`);
}

/** Last matching transfer in array order - the worker's own list is
 * chronological, so this is the most recent one for `filename`. */
export function findTransferForFilename(
	transfers: WorkerModelTransfer[],
	filename: string
): WorkerModelTransfer | undefined {
	let match: WorkerModelTransfer | undefined;
	for (const transfer of transfers) {
		if (transferMatchesFilename(transfer, filename)) match = transfer;
	}
	return match;
}

export function hasRunningTransfer(transfers: WorkerModelTransfer[]): boolean {
	return transfers.some((t) => t.state === 'running');
}

export function transferProgressPercent(transfer: WorkerModelTransfer): number {
	if (!transfer.total_bytes || transfer.total_bytes <= 0) return 0;
	return Math.min(100, Math.round((transfer.received_bytes / transfer.total_bytes) * 100));
}

export interface RemoteModelSyncFilters {
	search: string;
	modelType: string;
	status: RemoteModelSyncStatus | 'all';
}

export function filterSyncRows(rows: RemoteModelSyncRow[], filters: RemoteModelSyncFilters): RemoteModelSyncRow[] {
	const q = filters.search.trim().toLowerCase();
	return rows.filter((row) => {
		if (filters.modelType !== 'all' && row.model_type !== filters.modelType) return false;
		if (filters.status !== 'all' && row.status !== filters.status) return false;
		if (q && !row.filename.toLowerCase().includes(q)) return false;
		return true;
	});
}

export function countByStatus(rows: RemoteModelSyncRow[]): Record<RemoteModelSyncStatus, number> {
	const counts: Record<RemoteModelSyncStatus, number> = { on_worker: 0, missing: 0, digest_mismatch: 0 };
	for (const row of rows) counts[row.status]++;
	return counts;
}

export function sumSizeBytes(rows: RemoteModelSyncRow[]): number {
	return rows.reduce((total, row) => total + (row.size_bytes ?? 0), 0);
}

/** Shape `ModelAssignmentPicker`'s card grid expects (`id`/`file_size`) -
 * `status`/`providers_can_fetch` ride along for the sync-specific card extras. */
export interface RemoteModelPickerModel {
	id: string;
	filename: string;
	model_type: string;
	file_size: number | null;
	status: RemoteModelSyncStatus;
	providers_can_fetch: boolean;
}

export function toPickerModel(row: RemoteModelSyncRow): RemoteModelPickerModel {
	return {
		id: row.model_id,
		filename: row.filename,
		model_type: row.model_type,
		file_size: row.size_bytes,
		status: row.status,
		providers_can_fetch: row.providers_can_fetch
	};
}
