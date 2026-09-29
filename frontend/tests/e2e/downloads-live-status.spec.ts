import { test, expect, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'downloads-live-status';
const FILENAME = 'live-status-model.safetensors';

const row = {
	id: 'live-dl-1',
	type: 'model',
	url: 'https://example.com/live-status-model.safetensors',
	destination_path: '/models/checkpoint/live-status-model.safetensors',
	filename: FILENAME,
	status: 'pending',
	progress: 0,
	total_bytes: 1000000,
	downloaded_bytes: 0,
	speed_bytes_per_sec: null,
	error_message: null,
	provider_id: null,
	tags: [],
	checksum_sha256: null,
	retry_count: 0,
	created_at: '2026-01-01T00:00:00Z',
	started_at: null,
	completed_at: null,
	created_by: null
};

test('a download moves from Pending to Downloading to Completed without a reload', async ({ page }) => {
	await loginAsOwner(page);

	await page.route(/\/api\/downloads(\?.*)?$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		await route.fulfill({
			json: {
				success: true,
				data: { downloads: [row], counts: { pending: 1 } }
			}
		});
	});

	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket('**/ws/downloads**', (ws) => {
		socket = ws;
		ws.send(JSON.stringify({ type: 'connection_established' }));
	});

	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/admin?tab=downloads');

	const line = page.getByRole('row').filter({ hasText: FILENAME });
	await expect(line).toBeVisible({ timeout: 20000 });
	await expect(line.getByText('Pending', { exact: true })).toBeVisible();
	await expect.poll(() => socket !== null, { timeout: 15000 }).toBe(true);
	await screenshot(page, JOURNEY, '00-pending');

	const push = (message: object) => (socket as WebSocketRoute | null)!.send(JSON.stringify(message));

	push({ type: 'download_started', download_id: row.id, status: 'started', filename: FILENAME });
	await expect(line.getByText('Downloading', { exact: true })).toBeVisible({ timeout: 10000 });
	await expect(line.getByText('Pending', { exact: true })).toHaveCount(0);
	push({
		type: 'download_progress',
		download_id: row.id,
		progress: 0.45,
		downloaded_bytes: 450000,
		total_bytes: 1000000,
		speed_bytes_per_sec: 250000,
		filename: FILENAME
	});
	await screenshot(page, JOURNEY, '01-downloading');

	push({ type: 'download_completed', download_id: row.id, status: 'completed', filename: FILENAME });
	await expect(line.getByText('Completed', { exact: true })).toBeVisible({ timeout: 10000 });
	await expect(line.getByText('Downloading', { exact: true })).toHaveCount(0);
	await screenshot(page, JOURNEY, '02-completed');
});
