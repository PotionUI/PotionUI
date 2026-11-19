import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot, selectRailTool } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-fix-faces';
const GENERATION_ID = 'gen-e2e-fix-faces';
const RUN_GENERATION = 'gen-e2e-fix-faces-run';
const FILE_ID = 505;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_fix_faces';

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'steps', type: 'slider', title: 'Steps', default: 20 },
				{ name: 'denoise', type: 'number', title: 'Denoise', default: 0.35 },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

const OFFER: Json = { preset_id: 'sdxl', preset_name: 'SDXL Base', mode: 'editor:fix_faces', from_mode: 'fix_faces', mode_label: 'Fix faces', label: null, short_description: null, order: 0, variant: null, region_strategy: 'crop', via: null, qualifies: true, reason: null, owned: [], strength: { default: 0.35, min: 0.1, max: 0.8 } };

const FIX_FACES_TASK: Json = { id: 'fix_faces', label: 'Fix faces and hands', icon: 'mode-refine-faces', editors: ['image'], region: 'required', prompt: 'none', fallback: null, offers: [OFFER], default: null };

const DETECTIONS: Json = {
	picture: { width: 640, height: 480 },
	detections: [
		{ id: 'd1', kind: 'face', box: { x: 0.18, y: 0.2, width: 0.18, height: 0.26 }, score: 0.97 },
		{ id: 'd2', kind: 'face', box: { x: 0.58, y: 0.16, width: 0.16, height: 0.24 }, score: 0.91 },
		{ id: 'd3', kind: 'hand', box: { x: 0.42, y: 0.62, width: 0.14, height: 0.16 }, score: 0.78 }
	]
};

interface Server {
	runs: Json[];
	detections: Json[];
	sockets: WebSocketRoute[];
	live: Json;
	edits: Json[];
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function runBody(status: string, extra: Json = {}): Json {
	return {
		id: 'er_ff_1',
		task: 'fix_faces',
		status,
		edit_id: EDIT_ID,
		step_id: 's1',
		generation_id: RUN_GENERATION,
		progress: status === 'done' ? 1 : 0,
		error: null,
		soft: false,
		rect: { x: 0, y: 0, width: 1, height: 1 },
		parts: { done: 0, total: 3 },
		candidates: [],
		...extra
	};
}

const doneBody = (): Json =>
	runBody('done', { progress: 1, parts: { done: 3, total: 3 }, candidates: [{ patch: { item_id: 'patch-1' }, url: '/api/e2e-art/patch-ff.png', seed: 77 }, { patch: { item_id: 'patch-2' }, url: '/api/e2e-art/patch-ff.png', seed: 78 }] });

async function mockAll(page: Page, detector: 'ok' | 'unavailable'): Promise<Server> {
	const server: Server = { runs: [], detections: [], sockets: [], live: {}, edits: [] };
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);
	await mockMediaTools(page);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => {
		const now = new Date().toISOString();
		const generation = { id: GENERATION_ID, preset_id: 'krea2', preset_name: 'Krea 2', mode: 'txt2img', form_data: {}, status: 'completed', progress: 1, created_at: now, updated_at: now, completed_at: now, files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }] };
		return route.request().method() === 'GET' ? ok(route, { generations: [generation], total: 1 }) : route.fallback();
	});
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) => ok(route, { id: 'art-1', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art.png', width: 640, height: 480, duration_seconds: null, fps: null, size: 2048 }));
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media\/upload$/, (route) => ok(route, { path: 'uploads/mask.png', relative_path: 'uploads/mask.png', filename: 'mask.png', url: '/api/e2e-art/mask.png', width: 480, height: 480, size: 4096 }));
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
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks: [FIX_FACES_TASK] }));
	await page.route(/\/api\/editor\/tasks\/fix_faces\/detections$/, async (route) => {
		server.detections.push(route.request().postDataJSON() as Json);
		if (detector === 'unavailable') {
			return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'detector_unavailable', message: 'No detector is installed' } }) });
		}
		await ok(route, DETECTIONS);
	});
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		server.runs.push(route.request().postDataJSON() as Json);
		server.live = {};
		await ok(route, { run: runBody('queued') }, 202);
	});
	await page.route(/\/api\/editor\/tasks\/[^/]+\/default$/, (route) => ok(route, { task: FIX_FACES_TASK }));
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

async function start(page: Page, detector: 'ok' | 'unavailable'): Promise<Server> {
	const server = await mockAll(page, detector);
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await openHarbor(page);
	await selectRailTool(page, 'fix_faces');
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'fix_faces');
	return server;
}

async function lasso(page: Page) {
	const path: Array<[number, number]> = [[0.1, 0.7], [0.3, 0.7], [0.32, 0.9], [0.12, 0.92], [0.1, 0.7]];
	for (let attempt = 0; attempt < 4; attempt++) {
		await page.waitForTimeout(500);
		const box = await page.locator('[data-mask-layer]').boundingBox();
		if (!box) continue;
		const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
		await page.mouse.move(at(path[0]).x, at(path[0]).y);
		await page.mouse.down();
		for (const point of path.slice(1)) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
		await page.mouse.up();
		if (await page.locator('[data-mask-area]').isVisible()) break;
	}
	await expect(page.locator('[data-mask-area]')).toBeVisible();
}

const layerRow = (page: Page) => page.locator('[data-edit-stack] [data-stack-section="layers"] [data-stack-row="s1"]');
const mark = (page: Page) => page.locator('[data-floating-prompt] [data-prompt-mark] button');
const panel = (page: Page) => page.locator('[data-fix-faces-list]');

test.describe('Fix faces and hands on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('finds faces and hands, skips one, runs in parts and lands a layer', async ({ page }) => {
		const server = await start(page, 'ok');
		await expect.poll(() => server.detections.length).toBe(1);
		expect(server.detections[0]).toMatchObject({ input: { generation_file_id: String(FILE_ID) } });
		expect(server.detections[0]).toHaveProperty('steps');

		await expect(page.locator('[data-detection-box]')).toHaveCount(3);
		await expect(page.locator('[data-detection-row]')).toHaveCount(3);
		await expect(panel(page).locator('[data-detection-row="d1"]')).toContainText('Face');
		await expect(panel(page).locator('[data-detection-row="d3"]')).toContainText('Hand');
		await expect(page.locator('[data-fix-faces-count]')).toHaveText('3 of 3 ticked');
		await expect(page.locator('[data-fix-faces-strength-value]')).toHaveText('0.35');
		await screenshot(page, JOURNEY, '01-found-dark-1440');

		await panel(page).locator('[data-detection-check="d3"]').uncheck();
		await expect(page.locator('[data-fix-faces-count]')).toHaveText('2 of 3 ticked');
		await expect(page.locator('[data-detection-box="d3"]')).toHaveAttribute('data-detection-on', 'false');
		await page.locator('[data-detection-tag="d3"]').click();
		await expect(page.locator('[data-fix-faces-count]')).toHaveText('3 of 3 ticked');
		await page.locator('[data-detection-tag="d3"]').click();
		await expect(page.locator('[data-fix-faces-count]')).toHaveText('2 of 3 ticked');

		await page.locator('[data-fix-faces-strength-input]').fill('0.5');
		await expect(page.locator('[data-fix-faces-strength-value]')).toHaveText('0.50');

		await expect(mark(page)).toBeEnabled();
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0]).toMatchObject({ strength: 0.5, prompt: null, region: { item_id: 'art-1' }, edit: { id: EDIT_ID } });
		await expect(layerRow(page)).toBeVisible();

		update(server, 'running', { progress: 0.4, parts: { done: 1, total: 3 } });
		await expect(layerRow(page)).toContainText('Fixing 2 of 3');
		await expect(page.locator('[data-floating-prompt] [data-prompt-mark]')).toHaveAttribute('data-prompt-mark', 'running');
		await screenshot(page, JOURNEY, '02-running-dark-1440');

		update(server, 'done', doneBody());
		await expect(layerRow(page)).toContainText('1 of 2');
		await expect(page.locator('[data-status-size]')).toContainText('640×480');
		await page.locator('[data-stack-hide="s1"]').first().click();
		await page.locator('[data-stack-hide="s1"]').first().click();
		await expect(layerRow(page)).toBeVisible();
		await screenshot(page, JOURNEY, '03-done-dark-1440');
	});

	test('shows an absent detector inline and still runs on an area marked by hand', async ({ page }) => {
		const server = await start(page, 'unavailable');
		await expect(page.locator('[data-fix-faces-unavailable]')).toContainText('not available');
		await expect(page.locator('[data-fix-faces-unavailable]')).toContainText('by hand');
		await expect(page.locator('[data-detection-box]')).toHaveCount(0);
		await expect(mark(page)).toBeDisabled();
		await expect(mark(page)).toHaveAccessibleName('Tick a face or mark an area first');
		await expect(page.locator('[data-mask-layer]')).toBeVisible();

		await lasso(page);
		await expect(mark(page)).toBeEnabled();
		await screenshot(page, JOURNEY, '04-unavailable-dark-1440');
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0]).toMatchObject({ prompt: null, region: { item_id: 'art-1' } });
		expect(server.runs[0]).not.toHaveProperty('strength');
	});
});
