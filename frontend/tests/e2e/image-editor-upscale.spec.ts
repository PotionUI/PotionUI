import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot, selectRailTool } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-upscale';
const GENERATION_ID = 'gen-e2e-upscale';
const RUN_GENERATION = 'gen-e2e-upscale-run';
const FILE_ID = 504;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_upscale';

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'steps', type: 'slider', title: 'Steps', default: 20 },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

const OFFER: Json = { preset_id: 'upres', preset_name: 'Tile Upscaler', mode: 'editor:upscale', from_mode: 'upscale', mode_label: 'Upscale', label: null, short_description: null, order: 0, variant: null, region_strategy: 'whole', via: null, qualifies: true, reason: null, owned: [] };

const UPSCALE_TASK: Json = { id: 'upscale', label: 'Upscale', icon: 'mode-upscale', editors: ['image'], region: 'none', prompt: 'none', resizes: true, fallback: null, offers: [OFFER], default: null };

interface Server {
	runs: Json[];
	sockets: WebSocketRoute[];
	live: Json;
	edits: Json[];
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function runBody(status: string, extra: Json = {}): Json {
	return {
		id: 'er_up_1',
		task: 'upscale',
		status,
		edit_id: EDIT_ID,
		step_id: 's1',
		generation_id: RUN_GENERATION,
		progress: status === 'done' ? 1 : 0,
		error: null,
		soft: false,
		rect: null,
		parts: { done: 0, total: 4 },
		candidates: [],
		...extra
	};
}

const doneBody = (size: { width: number; height: number }): Json =>
	runBody('done', { progress: 1, parts: { done: 4, total: 4 }, size, candidates: [{ patch: { item_id: 'patch-1' }, url: '/api/e2e-art/patch-up.png', seed: 321, size }] });

async function mockAll(page: Page): Promise<Server> {
	const server: Server = { runs: [], sockets: [], live: {}, edits: [] };
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);
	const enlarged = solidPng(320, 240);
	await mockMediaTools(page);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => {
		const now = new Date().toISOString();
		const generation = { id: GENERATION_ID, preset_id: 'krea2', preset_name: 'Krea 2', mode: 'txt2img', form_data: {}, status: 'completed', progress: 1, created_at: now, updated_at: now, completed_at: now, files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }] };
		return route.request().method() === 'GET' ? ok(route, { generations: [generation], total: 1 }) : route.fallback();
	});
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: route.request().url().includes('patch-up') ? enlarged : small }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) => ok(route, { id: 'art-1', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 }));
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		const now = new Date().toISOString();
		const record = { id: id ?? EDIT_ID, tool: 'image', name: null, source: { kind: 'history', id: String(FILE_ID) }, thumbnail_upload_id: null, revision: 1, created_at: now, updated_at: now };
		if (request.method() === 'POST' && !id) {
			server.edits.push(request.postDataJSON() as Json);
			return ok(route, { edit: { ...record, id: EDIT_ID } });
		}
		if (request.method() === 'GET' && id) return ok(route, { edit: { ...record, state: (server.edits.at(-1)?.state as Json) ?? {} } });
		if (request.method() === 'PUT' && id) {
			server.edits.push(request.postDataJSON() as Json);
			return ok(route, { edit: record });
		}
		return route.fallback();
	});
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks: [UPSCALE_TASK] }));
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		server.runs.push(route.request().postDataJSON() as Json);
		server.live = {};
		await ok(route, { run: runBody('queued') }, 202);
	});
	await page.route(/\/api\/editor\/tasks\/[^/]+\/default$/, (route) => ok(route, { task: UPSCALE_TASK }));
	await page.route(/\/api\/editor\/runs\/[^/]+$/, (route) => ok(route, { run: runBody((server.live.status as string) ?? 'queued', server.live) }));
	await page.route(/\/api\/sessions\/preset\/[^/?]+(\?.*)?$/, (route) => ok(route, []));
	await page.route(/\/api\/formulas(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, []) : route.fallback()));
	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		server.sockets.push(ws);
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-mock' }));
		ws.onMessage((raw) => {
			const message = JSON.parse(raw as string);
			if (message.type === 'subscribe_generation') ws.send(JSON.stringify({ type: 'subscribed', generation_id: message.generation_id }));
		});
	});
	return server;
}

function update(server: Server, status: string, extra: Json = {}) {
	server.live = { status, ...extra };
	for (const socket of server.sockets) socket.send(JSON.stringify({ type: 'editor_run_update', generation_id: RUN_GENERATION, run: runBody(status, extra) }));
}

async function openHarbor(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
}

const stackRow = (page: Page) => page.locator('[data-edit-stack] [data-stack-row="s1"]');
const size = (page: Page) => page.locator('[data-status-size]');

test.describe('Upscale on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('pick 4x, run in parts, the picture grows, hiding the step brings the original back', async ({ page }) => {
		const server = await mockAll(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
		await openHarbor(page);
		await expect(size(page)).toContainText('640×480');

		await selectRailTool(page, 'upscale');
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'upscale');
		await expect(page.locator('[data-mask-layer]')).toHaveCount(0);
		await expect(page.getByRole('group', { name: 'Marking tool' })).toHaveCount(0);

		await expect(page.locator('[role="dialog"][aria-label="Choose preset"]')).toHaveCount(0);
		await expect(page.locator('[data-floating-prompt] [data-testid="preset-picker-trigger"]')).toContainText('Tile Upscaler');

		const panel = page.locator('[data-upscale-options]');
		await expect(panel).toBeVisible();
		await expect(panel.locator('[data-upscale-source]')).toHaveText('640×480');
		await expect(panel.locator('[data-upscale-result]')).toHaveText('1280×960');
		await panel.locator('[data-upscale-factor="4"]').click();
		await expect(panel.locator('[data-upscale-result]')).toHaveText('2560×1920');
		await expect(panel.locator('[data-upscale-factor="4"]')).toHaveAttribute('aria-pressed', 'true');
		await expect(panel.locator('[data-upscale-width]')).toHaveValue('2560');
		await expect(panel.locator('[data-upscale-height]')).toHaveValue('1920');

		await panel.locator('[data-upscale-width]').fill('1000');
		await expect(panel.locator('[data-upscale-height]')).toHaveValue('750');
		await expect(panel.locator('[data-upscale-result]')).toHaveText('1000×750');
		await panel.locator('[data-upscale-width]').fill('300');
		await expect(panel.locator('[data-upscale-reason]')).toHaveText('Pick a size larger than the picture.');
		await expect(panel.locator('[data-upscale-run]')).toBeDisabled();
		await panel.locator('[data-upscale-factor="4"]').click();
		await expect(panel.locator('[data-upscale-reason]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '01-options-dark-1440');

		await panel.locator('[data-upscale-run]').click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0]).toMatchObject({ scale: 4, region: null, prompt: null, edit: { id: EDIT_ID } });
		expect(server.runs[0]).not.toHaveProperty('target');
		await expect(stackRow(page)).toBeVisible();

		update(server, 'running', { progress: 0.5, parts: { done: 2, total: 4 } });
		await expect(stackRow(page)).toContainText('Upscaling 3 of 4');
		await expect(panel.locator('[data-upscale-run]')).toBeDisabled();
		await expect(page.locator('[data-floating-prompt] [data-prompt-mark]')).toHaveAttribute('data-prompt-mark', 'running');
		await screenshot(page, JOURNEY, '02-running-dark-1440');

		const finished = doneBody({ width: 2560, height: 1920 });
		update(server, 'done', finished);
		await expect(stackRow(page)).toContainText('4× · 2560×1920');
		await expect(size(page)).toContainText('2560×1920', { timeout: 15000 });
		await expect(panel.locator('[data-upscale-source]')).toHaveText('2560×1920');
		await expect(panel.locator('[data-upscale-reason]')).toHaveText('That size is too large. Pick a smaller factor or size.');
		await panel.locator('[data-upscale-factor="2"]').click();
		await expect(panel.locator('[data-upscale-result]')).toHaveText('5120×3840');
		await expect(panel.locator('[data-upscale-run]')).toBeEnabled();
		await screenshot(page, JOURNEY, '03-done-dark-1440');

		await page.locator('[data-stack-hide="s1"]').first().click();
		await expect(size(page)).toContainText('640×480', { timeout: 15000 });
		await page.locator('[data-stack-hide="s1"]').first().click();
		await expect(size(page)).toContainText('2560×1920', { timeout: 15000 });
	});
});
