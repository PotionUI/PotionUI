import { describe, expect, it, vi } from 'vitest';
import { downloaderWebSocket } from './downloaderWebsocket';

// The server has no fixed list of status message types: it broadcasts
// 'download_' + DownloadStatus enum value (download_connection_hub.py,
// broadcast_status). The client switch must therefore cover every enum
// value — 'download_downloading' being dropped is exactly how "stuck at
// pending until refresh" happened.
const ENUM_STATUS_TYPES = [
	'download_pending',
	'download_downloading',
	'download_paused',
	'download_completed',
	'download_failed',
	'download_cancelled'
] as const;

describe('downloaderWebSocket status message handling', () => {
	it.each(ENUM_STATUS_TYPES)('routes %s to status callbacks', (type) => {
		const cb = vi.fn();
		const off = downloaderWebSocket.onDownloadStatus(cb);
		(downloaderWebSocket as unknown as { onMessage(m: object): void }).onMessage({
			type,
			download_id: 'dl-1',
			status: type.replace('download_', ''),
			filename: 'model.safetensors'
		});
		off();
		expect(cb).toHaveBeenCalledWith(
			expect.objectContaining({
				download_id: 'dl-1',
				status: type.replace('download_', '')
			})
		);
	});

	it('does not treat download_progress as a status change', () => {
		const statusCb = vi.fn();
		const progressCb = vi.fn();
		const offStatus = downloaderWebSocket.onDownloadStatus(statusCb);
		const offProgress = downloaderWebSocket.onDownloadProgress(progressCb);
		(downloaderWebSocket as unknown as { onMessage(m: object): void }).onMessage({
			type: 'download_progress',
			download_id: 'dl-1',
			progress: 42,
			downloaded_bytes: 42,
			total_bytes: 100,
			speed_bytes_per_sec: 10,
			filename: 'model.safetensors'
		});
		offStatus();
		offProgress();
		expect(statusCb).not.toHaveBeenCalled();
		expect(progressCb).toHaveBeenCalledWith(expect.objectContaining({ progress: 42 }));
	});
});
