import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { downloaderWebSocket } from './downloaderWebsocket';
import { logger } from '$lib/utils/logger';
import type { DownloadStatus } from '$lib/stores/downloads';

const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../tests/contracts/download_ws_messages.json', import.meta.url)),
		'utf8'
	)
) as { status_events: string[]; other_types: string[]; ignored_by_frontend: string[] };

const VALID_STORE_STATUSES: DownloadStatus[] = [
	'pending',
	'downloading',
	'paused',
	'completed',
	'failed',
	'cancelled'
];

const send = (m: object) =>
	(downloaderWebSocket as unknown as { onMessage(m: object): void }).onMessage(m);

describe('downloaderWebSocket backend message contract', () => {
	const statusTypes = CONTRACT.status_events.map((s) => [`download_${s}`, s] as const);

	it.each(statusTypes)('%s reaches the status callbacks', (type, status) => {
		const cb = vi.fn();
		const off = downloaderWebSocket.onDownloadStatus(cb);
		send({ type, download_id: 'dl-1', status, filename: 'm.bin' });
		off();
		expect(cb).toHaveBeenCalledTimes(1);
		expect(VALID_STORE_STATUSES).toContain(cb.mock.calls[0][0].status);
	});

	it('every non-status type is handled or explicitly ignored', () => {
		for (const type of CONTRACT.other_types) {
			const debug = vi.spyOn(logger, 'debug').mockImplementation(() => {});
			const status = vi.fn();
			const progress = vi.fn();
			const offS = downloaderWebSocket.onDownloadStatus(status);
			const offP = downloaderWebSocket.onDownloadProgress(progress);
			send({ type, download_id: 'dl-1', filename: 'm.bin', progress: 0.1 });
			offS();
			offP();
			const handled = status.mock.calls.length + progress.mock.calls.length > 0;
			if (CONTRACT.ignored_by_frontend.includes(type)) {
				expect(handled, type).toBe(false);
			} else {
				expect(handled, type).toBe(true);
			}
			expect(debug, type).not.toHaveBeenCalled();
			debug.mockRestore();
		}
	});

	it('maps started to downloading and retrying to pending', () => {
		const cb = vi.fn();
		const off = downloaderWebSocket.onDownloadStatus(cb);
		send({ type: 'download_started', download_id: 'a', status: 'started', filename: 'f' });
		send({ type: 'download_retrying', download_id: 'a', status: 'retrying', filename: 'f' });
		off();
		expect(cb.mock.calls.map((c) => c[0].status)).toEqual(['downloading', 'pending']);
	});
});

describe('downloaderWebSocket status message handling', () => {
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
