import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot, selectRailTool } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-expand';
const GENERATION_ID = 'gen-e2e-expand';
const FILE_ID = 611;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_expand';

type Json = Record<string, unknown>;

const FORM_SCHEMA = { properties: { generation: { type: 'group', title: 'Generation', children: [{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }] } } };

const FORMATS: Json[] = [
	{ id: 'square', label: 'Square', ratio: '1:1', source: 'built_in', group_id: null },
	{ id: 'portrait', label: 'Portrait', ratio: '4:5', source: 'built_in', group_id: null },
	{ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null },
	{ id: 'landscape', label: 'Landscape', ratio: '16:9', source: 'built_in', group_id: null },
	{ id: 'wide_banner', label: 'Wide banner', ratio: '3:1', source: 'built_in', group_id: null },
	{ id: 'house.team-banner', label: 'Team banner', ratio: '5:2', source: 'house', group_id: 'marketing' }
];

const offer: Json = { preset_id: 'zimage', preset_name: 'Z-Image', mode: 'editor:expand', from_mode: 'inpaint', mode_label: 'Expand', label: null, short_description: null, order: 0, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, quantity_field: 'quantity', owned: ['source_image'] };

const EXPAND_TASK: Json = {
	id: 'expand',
	label: 'Expand',
	icon: 'expand',
	region: 'none',
	prompt: 'optional',
	grows: true,
	fallback: { task: 'edit_region', prompt: 'Continue the picture beyond its edges so the new area matches it.' },
	offers: [offer],
	default: null
};

interface Server {
	runs: Json[];
	saves: Json[];
	created: Json[];
	puts: Array<{ id: string; body: Json }>;
	live: Record<number, Json>;
	sockets: WebSocketRoute[];
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

const generationOf = (n: number) => `gen-e2e-expand-run-${n}`;

function runBody(n: number, status: string, extra: Json = {}): Json {
	return { id: `er_${n}`, task: 'expand', status, edit_id: EDIT_ID, step_id: 's1', generation_id: generationOf(n), progress: status === 'done' ? 1 : 0, error: null, soft: false, rect: { x: 0, y: 0, width: 1, height: 1 }, candidates: [], ...extra };
}

async function mock(page: Page): Promise<Server> {
	const server: Server = { runs: [], saves: [], created: [], puts: [], live: {}, sockets: [] };
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);
	let revision = 1;
	await mockMediaTools(page);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => {
		const now = new Date().toISOString();
		const generation = { id: GENERATION_ID, preset_id: 'krea2', preset_name: 'Krea 2', mode: 'txt2img', form_data: {}, status: 'completed', progress: 1, created_at: now, updated_at: now, completed_at: now, files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }] };
		return route.request().method() === 'GET' ? ok(route, { generations: [generation], total: 1 }) : route.fallback();
	});
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) => ok(route, { id: 'art-1', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 }));
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		const now = new Date().toISOString();
		const record = (recordId: string) => ({ id: recordId, tool: 'image', name: null, source: { kind: 'history', id: String(FILE_ID) }, thumbnail_upload_id: null, revision, created_at: now, updated_at: now });
		if (request.method() === 'POST' && !id) {
			const body = request.postDataJSON() as Json;
			if (server.saves.length === 0) {
				server.saves.push(body);
				return ok(route, { edit: { ...record(EDIT_ID), state: body.state } });
			}
			server.created.push(body);
			return ok(route, { edit: { ...record(`ed_spawn_${server.created.length}`), state: body.state } });
		}
		if (request.method() === 'GET' && id) return ok(route, { edit: { ...record(id), state: (server.saves.at(-1)?.state as Json) ?? {} } });
		if (request.method() === 'PUT' && id) {
			const body = request.postDataJSON() as Json;
			if (id === EDIT_ID) server.saves.push(body);
			else server.puts.push({ id, body });
			revision += 1;
			return ok(route, { edit: { ...record(id), revision } });
		}
		return route.fallback();
	});
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks: [EXPAND_TASK] }));
	await page.route(/\/api\/editor\/expand\/formats$/, (route) => ok(route, { formats: FORMATS }));
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		server.runs.push(route.request().postDataJSON() as Json);
		await ok(route, { run: runBody(server.runs.length, 'queued') }, 202);
	});
	await page.route(/\/api\/editor\/runs\/[^/]+$/, async (route) => {
		const n = Number((route.request().url().split('/').pop() as string).replace('er_', ''));
		await ok(route, { run: runBody(n, (server.live[n]?.status as string) ?? 'running', server.live[n] ?? {}) });
	});
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

const prompt = (page: Page) => page.locator('[data-floating-prompt]');
const generateButton = (page: Page) => prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button');
const row = (page: Page, id = 's1') => page.locator(`[data-edit-stack] [data-stack-row="${id}"]`);
const format = (page: Page, id: string) => page.locator(`[data-expand-format="${id}"]`);

async function openHarbor(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
}

test.describe('Expand a picture to other formats', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('three formats from one picture: one step in this edit, one edit for each of the others, and a finished step that changes the canvas', async ({ page }) => {
		const server = await mock(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
		await openHarbor(page);

		await selectRailTool(page, 'expand');
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'expand');
		await expect(page.locator('[data-expand-options]')).toBeVisible();
		await expect(page.locator('[data-expand-format]')).toHaveCount(7);
		await expect(format(page, 'story')).toContainText('640×1138');
		await expect(format(page, 'landscape')).toContainText('854×480');
		await expect(format(page, 'wide_banner')).toContainText('1440×480');
		await expect(prompt(page).locator('[data-testid="preset-picker-trigger"]')).toHaveText('Z-Image');
		await expect(generateButton(page)).toBeDisabled();
		await expect(generateButton(page)).toHaveAccessibleName('Choose a format first');

		await format(page, 'story').click();
		await format(page, 'landscape').click();
		await format(page, 'wide_banner').click();
		await expect(page.locator('[data-expand-count]')).toContainText('3 of 6');
		await expect(page.locator('[data-expand-picture]')).toBeVisible();
		await expect(page.locator('[data-expand-passes]')).toContainText('2 passes');
		await expect(generateButton(page)).toBeEnabled();
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, '01-formats-dark-1440');

		await generateButton(page).click();

		await expect.poll(() => server.runs.length).toBe(3);
		const bodies = server.runs as Array<{ edit: { id: string }; expand: Json; region: unknown; candidates: number; prompt: string | null; setup: Json }>;
		expect(bodies.map((body) => body.expand)).toEqual([
			{ width: 640, height: 1138, x: 0, y: 329 },
			{ width: 854, height: 480, x: 107, y: 0 },
			{ width: 1440, height: 480, x: 400, y: 0 }
		]);
		expect(bodies.map((body) => body.edit.id)).toEqual([EDIT_ID, 'ed_spawn_1', 'ed_spawn_2']);
		expect(bodies.every((body) => body.region === null && body.candidates === 1 && body.prompt === null)).toBe(true);
		expect(bodies[0].setup).toMatchObject({ preset_id: 'zimage', mode: 'editor:expand' });
		await expect(page.locator('[data-expand-spawned]')).toContainText('Also started 2 more edits: Landscape, Wide banner');
		await expect(row(page)).toContainText('Expand');
		await expect.poll(() => server.puts.length).toBe(2);
		for (const put of server.puts) {
			const stack = (put.body.state as { stack: { steps: Array<{ tool: string; lane?: string; params: { expand: Json } }> } }).stack;
			expect(stack.steps).toHaveLength(1);
			expect(stack.steps[0]).toMatchObject({ tool: 'generate', lane: 'geometry' });
		}
		expect(server.puts.map((put) => (put.body.state as { stack: { steps: Array<{ params: { expand: Json } }> } }).stack.steps[0].params.expand)).toMatchObject([{ label: 'Landscape', width: 854, height: 480 }, { label: 'Wide banner', width: 1440, height: 480 }]);
		await screenshot(page, JOURNEY, '02-started-dark-1440');

		server.live[1] = { status: 'done', candidates: [{ patch: { item_id: 'patch-1' }, url: '/api/e2e-art/patch-1.png', seed: 7, generation_file_id: '900' }] };
		for (const socket of server.sockets) socket.send(JSON.stringify({ type: 'editor_run_update', generation_id: generationOf(1), run: runBody(1, 'done', server.live[1]) }));

		await expect(row(page)).toContainText('Story · 640×1138');
		await expect(page.locator('[data-image-editor]')).toContainText('640×1138');
		await screenshot(page, JOURNEY, '03-done-dark-1440');
	});

	test('a custom size is read as typed and never smaller than the picture', async ({ page }) => {
		const server = await mock(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
		await openHarbor(page);
		await selectRailTool(page, 'expand');

		await format(page, 'custom').click();
		await page.locator('[data-expand-custom] input[aria-label="Width"]').fill('2000');
		await page.locator('[data-expand-custom] input[aria-label="Width"]').blur();
		await page.locator('[data-expand-custom] input[aria-label="Height"]').fill('300');
		await page.locator('[data-expand-custom] input[aria-label="Height"]').blur();
		await expect(format(page, 'custom')).toContainText('2000×480');
		await prompt(page).locator('[data-prompt-field]').fill('more of the quiet harbour');
		await generateButton(page).click();

		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0]).toMatchObject({ prompt: 'more of the quiet harbour', expand: { width: 2000, height: 480, x: 680, y: 0 }, region: null });
	});
});
