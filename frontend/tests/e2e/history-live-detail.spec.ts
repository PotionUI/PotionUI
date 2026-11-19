import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { dirname, join } from 'node:path';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';
import { generationStatusMessage } from './generationMocks';

const JOURNEY = 'history-live-detail';
const RUN = '01E2ELIVEDETAIL000000000001';
const QUEUED = '01E2ELIVEDETAIL000000000002';
const DONE = ['01E2ELIVEDETAIL000000000003', '01E2ELIVEDETAIL000000000004', '01E2ELIVEDETAIL000000000005'];
const PROMPT = 'A concrete observatory dome at dusk, low clouds rolling over the ridge';
const KEY_NAME = 'claude desktop';

const svg = (from: string, to: string, blur = false) =>
	`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${from}"/><stop offset="1" stop-color="${to}"/></linearGradient>${blur ? '<filter id="b"><feGaussianBlur stdDeviation="14"/></filter>' : ''}</defs><rect width="640" height="400" fill="url(#g)"/><rect x="190" y="120" width="260" height="200" rx="8" fill="#e8ecf2" opacity="0.8"${blur ? ' filter="url(#b)"' : ''}/></svg>`;
const PICTURE = svg('#26354d', '#6f87a8');
const PREVIEW = `data:image/svg+xml;base64,${Buffer.from(svg('#3b4d6a', '#8fa3c2', true)).toString('base64')}`;

const mcpOrigin = { kind: 'mcp', key_id: 'k-e2e', key_name: KEY_NAME, key_current_name: KEY_NAME };
const FORM = { prompt: PROMPT, steps: 28, cfg: 3.5, seed: -1, width: 1920, height: 1080, quantity: 4, sampler: 'euler' };

function file(id: string, index: number) {
	return { id: Number(id.slice(-3)) * 10 + index, file_path: `/outputs/${id}/${index}.png`, file_type: 'image', width: 640, height: 400, is_final: true, is_derived: false };
}

function record(id: string, status: string, extra: Record<string, unknown> = {}) {
	return {
		id,
		preset_id: 'e2e-preset',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_name: null,
		form_data: FORM,
		status,
		progress: status === 'running' ? 0.4 : status === 'pending' ? 0 : 1,
		created_at: new Date(Date.now() - 30000).toISOString(),
		started_at: status === 'pending' ? null : new Date(Date.now() - 24000).toISOString(),
		completed_at: status === 'completed' ? new Date().toISOString() : undefined,
		run_seconds: status === 'completed' ? 41 : null,
		queued_seconds: 3,
		updated_at: new Date().toISOString(),
		files: [],
		segments: [],
		tags: [],
		rating: 0,
		is_favorite: false,
		origin: null,
		...extra
	};
}

interface Server {
	subscribes: string[];
	subscribers: Map<string, WebSocketRoute[]>;
	records: Record<string, Record<string, unknown>>;
}

const ok = (route: Route, data: unknown) => route.fulfill({ json: { success: true, data } });
const push = (server: Server, id: string, message: object) =>
	(server.subscribers.get(id) ?? []).forEach((ws) => {
		try {
			ws.send(JSON.stringify(message));
		} catch {
			return;
		}
	});

async function install(page: Page, runOrigin: unknown = mcpOrigin): Promise<Server> {
	const server: Server = {
		subscribes: [],
		subscribers: new Map(),
		records: {
			[RUN]: record(RUN, 'running', { origin: runOrigin }),
			[QUEUED]: record(QUEUED, 'pending'),
			...Object.fromEntries(DONE.map((id) => [id, record(id, 'completed', { files: [file(id, 0)], rating: 4 })]))
		}
	};

	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-mock' }));
		ws.onMessage((raw) => {
			const message = JSON.parse(raw as string);
			if (message.type !== 'subscribe_generation') return;
			const id = message.generation_id as string;
			server.subscribes.push(id);
			server.subscribers.set(id, [...(server.subscribers.get(id) ?? []), ws]);
			ws.send(JSON.stringify({ type: 'subscribed', generation_id: id }));
			if (id === RUN) {
				ws.send(
					JSON.stringify({
						type: 'status_update',
						data: { id, status: 'running', progress: 0.4, current_step: '<<PIPE:generator:bolt>> <<PROGRESS:17/28>>', message: '' }
					})
				);
				ws.send(JSON.stringify({ type: 'workbench_update', generation_id: id, pipe_id: 1, pipe_name: 'generator', output_type: 'image', image: PREVIEW, replayed: true }));
			}
		});
	});

	await page.route(/\/api\/media\//, (route) => route.fulfill({ status: 200, contentType: 'image/svg+xml', body: PICTURE }));
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		ok(route, { generations: Object.values(server.records), total: Object.keys(server.records).length, limit: 24, offset: 0 })
	);
	await page.route(/\/api\/generations\/history\/(01E2ELIVEDETAIL[A-Z0-9]+)(\?.*)?$/, (route) => {
		const id = route.request().url().match(/history\/([A-Z0-9]+)/)![1];
		return server.records[id] ? ok(route, server.records[id]) : route.fulfill({ status: 404, json: { success: false } });
	});
	await page.route(/\/api\/generations\/01E2ELIVEDETAIL[A-Z0-9]+\/params\/\d+$/, (route) =>
		ok(route, { parameters: { prompt: PROMPT, steps: 28, cfg: 3.5, sampler: 'euler' }, models: [], parameter_meta: {}, parameter_order: ['prompt', 'steps', 'cfg', 'sampler'] })
	);
	await page.route(/\/api\/generations\/01E2ELIVEDETAIL[A-Z0-9]+\/cancel$/, (route) => ok(route, { cancel_notice: null }));
	return server;
}

const tile = (page: Page, id: string) => page.locator(`[data-generation-id="${id}"]`);
const dialog = (page: Page) => page.getByRole('dialog', { name: 'Generation Details' });

async function openRunning(page: Page) {
	await page.goto('/history');
	await expect(tile(page, RUN)).toBeVisible({ timeout: 20000 });
	await tile(page, RUN).locator('.media-zoom').click();
	await expect(dialog(page).locator('[data-live-stage]')).toBeVisible({ timeout: 10000 });
}

test.describe('a running generation opens from History and turns into the finished view', () => {
	test.beforeEach(async ({ page }) => {
		await loginAsOwner(page);
	});

	test('tiles are alive, the detail shows the live state and finishes in place', async ({ page }) => {
		test.setTimeout(120000);
		await page.setViewportSize({ width: 1440, height: 900 });
		const server = await install(page);

		await page.goto('/history');
		await expect(tile(page, RUN)).toBeVisible({ timeout: 20000 });
		await expect(tile(page, RUN).locator('[data-live-tile-preview]')).toBeVisible({ timeout: 10000 });
		await expect(tile(page, RUN).locator('[data-live-tile-chip]')).toContainText('Generating · 17/28');
		await expect(tile(page, RUN).locator('[data-live-tile-elapsed]')).toBeVisible();
		expect(server.subscribes.filter((id) => DONE.includes(id))).toHaveLength(0);
		await screenshot(page, JOURNEY, '01-history-live-1440');

		await tile(page, RUN).locator('.media-zoom').click();
		const detail = dialog(page);
		await expect(detail.locator('[data-live-stage]')).toBeVisible({ timeout: 10000 });
		await expect(detail.locator('[data-live-step]')).toContainText('step 17/28');
		await expect(detail.locator('[data-live-preview]')).toBeVisible();
		await expect(detail).toContainText(PROMPT);
		await expect(detail).not.toContainText('Chosen when sampling starts');
		await expect(detail).not.toContainText('Information');
		await expect(detail.locator('[data-details-origin]')).toContainText(`Started over MCP · ${KEY_NAME}`);
		await expect(detail.locator('[data-live-action="open-in-tab"]')).toBeVisible();
		await expect(detail.locator('[data-live-disabled="download"] button')).toBeDisabled();
		await screenshot(page, JOURNEY, '02-running-image-1440');

		push(server, RUN, generationStatusMessage({ generationId: RUN, pipeId: 1, pipeName: 'generator', currentStep: '<<PIPE:generator:bolt>> <<PROGRESS:20/28>>', progress: 0.7, stepNum: 20, totalSteps: 28 }));
		await expect(detail.locator('[data-live-step]')).toContainText('step 20/28');

		server.records[RUN] = record(RUN, 'completed', { origin: mcpOrigin, files: [0, 1, 2, 3].map((i) => file(RUN, i)) });
		push(server, RUN, { type: 'gallery_update', generation_id: RUN, pipe_id: 2, pipe_name: 'gallery', output_type: 'gallery', images: [PREVIEW], image_urls_list: [{ derived: false, seed: 1 }] });
		push(server, RUN, { type: 'generation_complete', data: { id: RUN, generation_id: RUN, status: 'completed', progress: 1 } });
		await expect(detail.locator('[data-live-stage]')).toHaveCount(0, { timeout: 15000 });
		await expect(detail.locator('[data-live-finished-notice]')).toHaveCount(0);
		await expect(detail.locator('[data-live-stage]')).toHaveCount(0);
		await expect(detail.locator('[data-details-origin]')).toContainText('Started over MCP');
		await screenshot(page, JOURNEY, '04-finished-1440');
		const shot = await screenshot(page, JOURNEY, 'origin-header-1440');
		await page.screenshot({ path: join(dirname(shot), 'origin-header-1440.png'), clip: { x: 104, y: 68, width: 1224, height: 72 } });
	});

	test('a failure shows the error where the media was and keeps the parameters', async ({ page }) => {
		test.setTimeout(90000);
		await page.setViewportSize({ width: 1440, height: 900 });
		const server = await install(page);
		await openRunning(page);

		server.records[RUN] = record(RUN, 'failed', { origin: mcpOrigin, error_message: 'cuda out of memory', error_user_message: 'The GPU ran out of memory. Try a smaller size.', error_contact_admin: true, error_id: 'e7c2f10a' });
		push(server, RUN, { type: 'generation_error', generation_id: RUN, message: 'cuda out of memory', error_code: 'cuda_oom' });
		const detail = dialog(page);
		await expect(detail.locator('[data-live-failure]')).toContainText('Generation failed', { timeout: 15000 });
		await expect(detail).toContainText(PROMPT);
		await expect(detail.locator('[data-live-action="reuse"]')).toContainText('Try again in a tab');
		await screenshot(page, JOURNEY, '05-failed-1440');
	});

	test('Reuse settings in the details of a finished generation opens a Generate tab with its settings', async ({ page }) => {
		test.setTimeout(90000);
		await page.setViewportSize({ width: 1440, height: 900 });
		await install(page);
		await page.goto('/history');
		await expect(tile(page, DONE[0])).toBeVisible({ timeout: 20000 });
		await tile(page, DONE[0]).locator('.media-zoom').click();
		const detail = dialog(page);
		await expect(detail).toBeVisible({ timeout: 10000 });
		await detail.getByRole('button', { name: 'Reuse settings' }).click();
		await page.waitForURL(/\/generate/, { timeout: 20000 });
		await expect(dialog(page)).toHaveCount(0);
		const key = await scopedStorageKey(page, 'potionui_tabs_state');
		await expect
			.poll(() =>
				page.evaluate((k) => (JSON.parse(localStorage.getItem(k) ?? 'null')?.tabs ?? []).map((tab: { name: string }) => tab.name), key)
			)
			.toContain('Reused: Krea 2');
	});

	test('phone width', async ({ page }) => {
		test.setTimeout(90000);
		await page.setViewportSize({ width: 390, height: 844 });
		await install(page);
		await openRunning(page);
		await expect(dialog(page).locator('[data-live-step]')).toContainText('step 17/28');
		await screenshot(page, JOURNEY, '07-phone-running-image-390');
	});
});
