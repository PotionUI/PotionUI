import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';
import { EFFECT_CATALOG } from '../component/fixtures/effectCatalog';
import { openTool } from './video-helpers';

const JOURNEY = 'video-editor-generate';
const CLIP = readFileSync(fileURLToPath(new URL('./fixtures/preset-cover.webm', import.meta.url)));
const FRAME = solidPng(160, 90);
const SOURCE = {
	path: 'uploads/coast-drive.webm',
	relative_path: 'uploads/coast-drive.webm',
	url: '/api/e2e-media/coast-drive.webm',
	name: 'coast-drive.webm',
	type: 'video',
	item_id: 'item-1'
};
const runGeneration = (n: number) => `gen-e2e-video-run-${n}`;

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'quantity', type: 'stepper', title: 'Quantity', default: 2, configuration: { min: 1, max: 4, step: 1 } },
				{ name: 'duration', type: 'slider', title: 'Added length (seconds)', default: 5, configuration: { min: 1, max: 15, step: 0.5 } },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

function offer(task: string, label: string, over: Json = {}): Json {
	return { preset_id: 'ltx25', preset_name: 'LTX Video', mode: `editor:${task}`, from_mode: task, mode_label: label, mode_icon: `mode-${task}`, label: null, short_description: null, order: 0, variant: null, region_strategy: 'whole', via: null, qualifies: true, reason: null, quantity_field: 'quantity', owned: [], ...over };
}

function task(id: string, label: string, icon: string, over: Json = {}): Json {
	return {
		id,
		label,
		icon,
		editors: ['video'],
		region: 'none',
		prompt: 'optional',
		range: 'optional',
		result: 'replace',
		audio: 'keep',
		resizes: false,
		fallback: null,
		offers: [offer(id, label)],
		default: { preset_id: 'ltx25', mode: `editor:${id}`, setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: label } },
		...over
	};
}

const TASKS: Json[] = [
	task('extend', 'Extend', 'mode-extend', { result: 'append', audio: 'result', offers: [offer('extend', 'Extend')] }),
	task('upscale', 'Upscale', 'mode-upscale', { prompt: 'none', resizes: true }),
	task('restore', 'Replace a section', 'mode-restore'),
	task('relight', 'Day to night', 'mode-day-night', { offers: [offer('relight', 'Day to night', { qualifies: false, reason: 'models_missing' })], default: null }),
	task('colorize', 'Colorize', 'mode-colorize', { offers: [], default: null })
];

interface Server {
	runs: Json[];
	mediaRuns: Json[];
	sockets: WebSocketRoute[];
	runCount: number;
	live: Json;
	cancels: string[];
}

function runBody(server: Server, status: string, extra: Json = {}, n = server.runCount): Json {
	const body = server.runs[n - 1] ?? {};
	return {
		id: `er_${n}`,
		task: 'restore',
		status,
		edit_id: ((body.edit as Json | undefined)?.id as string) ?? 'video-video-tab-1',
		step_id: ((body.edit as Json | undefined)?.step_id as string) ?? 's1',
		generation_id: runGeneration(n),
		progress: status === 'done' ? 1 : 0,
		error: null,
		soft: false,
		rect: null,
		range: { start_s: 0, end_s: 1, whole: false },
		candidates: [],
		...extra
	};
}

function candidates(count: number, extra: Json = {}): Json[] {
	return Array.from({ length: count }, (_, i) => ({ clip: { item_id: `clip-${'ab'[i]}` }, url: `/api/e2e-media/cand-${'ab'[i]}.webm`, seed: 100 + i, generation_file_id: String(900 + i), range: { start_s: 0, end_s: 1 }, duration_s: 2, ...extra }));
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockAll(page: Page, tasks: Json[] = TASKS): Promise<Server> {
	const server: Server = { runs: [], mediaRuns: [], sockets: [], runCount: 0, live: {}, cancels: [] };
	await page.route('**/api/e2e-media/**', (route) =>
		route.request().url().endsWith('.png') ? route.fulfill({ status: 200, contentType: 'image/png', body: FRAME }) : route.fulfill({ status: 200, contentType: 'video/webm', body: CLIP })
	);
	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, { tools: EFFECT_CATALOG }) : route.fallback()));
	await page.route('**/api/media-tools/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (path.endsWith('/preview/frame')) return route.fulfill({ status: 200, contentType: 'image/png', body: FRAME });
		if (path.endsWith('/runs') && request.method() === 'POST') {
			server.mediaRuns.push(request.postDataJSON() as Json);
			return ok(route, { run_id: `render-${server.mediaRuns.length}` }, 202);
		}
		const match = /\/runs\/([^/]+)$/.exec(path);
		if (match) return ok(route, { run_id: match[1], kind: 'run', status: 'running', progress: 0.3, label: 'Rendering', items: [], replaced: false, preview: null, error: null });
		return route.fallback();
	});
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks }));
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		server.runs.push(route.request().postDataJSON() as Json);
		server.runCount += 1;
		server.live = {};
		await ok(route, { run: runBody(server, 'queued') }, 202);
	});
	await page.route(/\/api\/editor\/runs\/[^/]+$/, async (route) => {
		if (route.request().method() === 'DELETE') {
			server.cancels.push(route.request().url().split('/').pop() as string);
			return ok(route, { run: runBody(server, 'cancelled') });
		}
		await ok(route, { run: runBody(server, (server.live.status as string) ?? 'queued', server.live) });
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

function update(server: Server, status: string, extra: Json = {}) {
	server.live = { status, ...extra };
	const message = JSON.stringify({ type: 'editor_run_update', generation_id: runGeneration(server.runCount), run: runBody(server, status, extra) });
	for (const socket of server.sockets) socket.send(message);
}

async function openEditor(page: Page) {
	await loginAsOwner(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container:visible, .studio-topbar:visible').first()).toBeVisible({ timeout: 15000 });
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	await page.addInitScript(
		({ k, source }) => {
			if (sessionStorage.getItem('e2e-video-generate-seeded')) return;
			sessionStorage.setItem('e2e-video-generate-seeded', '1');
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool');
			const tool = { id: 'video-tab-1', kind: 'tool', tool: 'video', name: 'coast-drive', color: null, source, state: null, run: null, activeTool: 'trim', dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			if (generation) saved.lastGenerationTabId = generation.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source: SOURCE }
	);
	await page.reload();
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

async function trackGeometry(page: Page) {
	const track = page.locator('[data-scrub-track]');
	const box = (await track.boundingBox())!;
	const duration = Number(await track.getAttribute('aria-valuemax'));
	return { box, duration, x: (seconds: number) => box.x + (seconds / duration) * box.width, y: box.y + box.height / 2 };
}

async function markRange(page: Page, from: number, to: number) {
	const geo = await trackGeometry(page);
	await page.mouse.move(geo.x(geo.duration * from), geo.y);
	await page.mouse.down();
	await page.mouse.move(geo.x(geo.duration * to), geo.y, { steps: 6 });
	await page.mouse.up();
	return geo;
}

const dock = (page: Page) => page.locator('[data-floating-prompt]');
const generateButton = (page: Page) => dock(page).locator('[data-prompt-mark] button');
const stackRow = (page: Page, id: string) => page.locator(`[data-edit-stack] [data-stack-row="${id}"]`);

async function ready(page: Page, id: string) {
	const editor = page.getByTestId('video-editor');
	await expect(editor.locator(`[data-tool-rail] button[data-tool="${id}"], [data-tool-rail] button[data-group-members~="${id}"]`).first()).toBeVisible({ timeout: 15000 });
	await openTool(page, editor, id);
	await expect(editor.locator('[data-range-options]')).toBeVisible();
	return editor;
}

test.describe('the AI tools in the Video editor', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('puts a Generate group first on the rail with one tool per task that can run', async ({ page }) => {
		await mockAll(page);
		const editor = await openEditor(page);
		const rail = editor.locator('[data-tool-rail]');
		await expect(rail.locator('[data-tool="extend"], [data-group-members~="extend"]').first()).toBeVisible({ timeout: 15000 });
		const first = await rail.locator('button').first().evaluate((node) => `${node.getAttribute('data-tool') ?? ''} ${node.getAttribute('data-group-members') ?? ''}`);
		expect(first).toMatch(/extend/);
		await expect(rail.locator('[data-tool="relight"], [data-group-members~="relight"]')).toHaveCount(0);
		await expect(rail.locator('[data-tool="colorize"], [data-group-members~="colorize"]')).toHaveCount(0);
		for (const id of ['upscale', 'restore']) await expect(rail.locator(`[data-tool="${id}"], [data-group-members~="${id}"]`).first()).toBeVisible();
	});

	test('marks a range on the timeline, runs the model on it, and turns the settled clip into a step that plays', async ({ page }) => {
		const server = await mockAll(page);
		await openEditor(page);
		const editor = await ready(page, 'restore');
		await expect(editor.locator('[data-range-card="whole"]')).toBeVisible();
		await expect(editor.locator('[data-scrub-wave]')).toHaveCount(0);
		await expect(dock(page)).toBeVisible();
		await expect(dock(page).locator('[data-prompt-status]')).toHaveCount(0);

		const geo = await markRange(page, 0.25, 0.6);
		await expect(editor.locator('[data-range-card="marked"]')).toBeVisible();
		const section = editor.locator('[data-scrub-section-box]');
		await expect(section).toBeVisible();
		const startField = editor.locator('[data-range-field="start"]');
		const endField = editor.locator('[data-range-field="end"]');
		expect(Math.abs(Number(await startField.inputValue()) - geo.duration * 0.25)).toBeLessThan(geo.duration * 0.05);
		expect(Math.abs(Number(await endField.inputValue()) - geo.duration * 0.6)).toBeLessThan(geo.duration * 0.05);

		await startField.fill('0.2');
		await startField.press('Enter');
		await expect(editor.locator('[data-scrub-handle="range-start"]')).toHaveAttribute('aria-valuenow', '0.2');
		await endField.fill('0.9');
		await endField.press('Enter');
		await expect(editor.locator('[data-scrub-handle="range-end"]')).toHaveAttribute('aria-valuenow', '0.9');
		await expect(editor.locator('[data-range-length]')).toHaveText('0.70 s');

		const handle = (await editor.locator('[data-scrub-handle="range-start"]').boundingBox())!;
		await page.mouse.move(handle.x + handle.width / 2, geo.y);
		await page.mouse.down();
		await page.mouse.move(geo.x(0.3), geo.y, { steps: 4 });
		await page.mouse.up();
		expect(Math.abs(Number(await startField.inputValue()) - 0.3)).toBeLessThan(0.08);
		await startField.fill('0.25');
		await startField.press('Enter');

		await dock(page).locator('[data-prompt-field]').fill('Rain begins to fall, the light goes cold and grey');
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, 'ai-rest-dark-1440');

		await generateButton(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		const body = server.runs[0];
		expect(body).toMatchObject({
			edit: { id: expect.stringMatching(/^ed_/), step_id: 's1' },
			input: { item_id: 'item-1' },
			steps: [],
			region: null,
			range: { start_s: 0.25, end_s: 0.9 },
			prompt: 'Rain begins to fall, the light goes cold and grey',
			setup: { preset_id: 'ltx25', mode: 'editor:restore' },
			candidates: 2
		});
		await expect(stackRow(page, 's1')).toBeVisible();

		update(server, 'running', { progress: 0.4 });
		await expect(dock(page).locator('[data-border-progress="running"]')).toHaveCount(1);
		await expect(dock(page).locator('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '40');
		await expect(stackRow(page, 's1')).toContainText('Generating');
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, 'ai-running-dark-1440');

		update(server, 'done', { candidates: candidates(2), range: { start_s: 0.25, end_s: 0.9, whole: false } });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await expect(page.locator('[data-candidate]')).toHaveCount(2);
		await expect(stackRow(page, 's1')).toContainText('1 of 2');
		await expect(page.locator('[data-video-element]')).toHaveAttribute('src', /cand-a\.webm/);

		await page.locator('[data-candidate="1"]').click();
		await expect(page.locator('[data-candidate="1"]')).toHaveAttribute('aria-pressed', 'true');
		await expect(stackRow(page, 's1')).toContainText('2 of 2');
		await expect(page.locator('[data-video-element]')).toHaveAttribute('src', /cand-b\.webm/);
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, 'ai-results-dark-1440');

		await editor.locator('[data-frame-save-menu]').click();
		await page.getByRole('menuitem', { name: /Save as new/ }).click();
		await expect.poll(() => server.mediaRuns.length).toBe(1);
		expect(server.mediaRuns[0].inputs).toEqual([{ item_id: 'clip-b' }]);
		expect((server.mediaRuns[0].steps as Array<{ tool: string }>).map((step) => step.tool)).toEqual(['trim']);

		await stackRow(page, 's1').hover();
		await stackRow(page, 's1').getByRole('button', { name: /^Remove / }).click();
		await expect(stackRow(page, 's1')).toHaveCount(0);
		await expect(page.locator('[data-video-element]')).toHaveAttribute('src', /coast-drive\.webm/);
	});

	test('runs on the whole clip when nothing is marked, and a step beneath makes the result out of date', async ({ page }) => {
		const server = await mockAll(page);
		await openEditor(page);
		const editor = await ready(page, 'upscale');
		await expect(dock(page).locator('[data-prompt-note]')).toBeVisible();
		await generateButton(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0].range).toBeNull();
		expect(server.runs[0].prompt).toBeNull();
		update(server, 'done', { candidates: candidates(1), range: { start_s: 0, end_s: 2, whole: true } });
		await expect(page.locator('[data-video-element]')).toHaveAttribute('src', /cand-a\.webm/);

		await openTool(page, editor, 'mute');
		await editor.locator('[data-options-apply]').click();
		await expect(editor.getByRole('button', { name: /Reopen Mute/ })).toBeVisible();
		await expect(page.locator('[data-video-element]')).toHaveAttribute('src', /cand-a\.webm/);
		await editor.locator('[data-frame-undo]').click();
	});

	test('extends: a seconds field, the marker at the end of the range, and the seconds reach the run', async ({ page }) => {
		const server = await mockAll(page);
		await openEditor(page);
		const editor = await ready(page, 'extend');
		await expect(editor.locator('[data-extend-options]')).toBeVisible();
		const seconds = editor.locator('[data-extend-seconds]');
		await expect(seconds).toHaveValue('5');
		await expect(editor.locator('[data-extend-marker]')).toBeVisible();
		await expect(editor.locator('[data-scrub-extend-marker]')).toContainText('+5.00 s');
		await markRange(page, 0.1, 0.5);
		await seconds.fill('8');
		await seconds.press('Enter');
		await expect(editor.locator('[data-scrub-extend-marker]')).toContainText('+8.00 s');
		await dock(page).locator('[data-prompt-field]').fill('The road turns toward the sea');
		await generateButton(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		const body = server.runs[0];
		expect((body.setup as { form_data: Json }).form_data.duration).toBe(8);
		expect((body.range as Json).start_s).toBeGreaterThan(0);
		expect((body.range as Json).end_s).toBeGreaterThan((body.range as Json).start_s as number);
		expect(body.prompt).toBe('The road turns toward the sea');
	});
});

test.describe('the AI tools in the Video editor on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('shows the phone dock with the range and the prompt', async ({ page }) => {
		await mockAll(page);
		await openEditor(page);
		await page.locator('[data-tool-rail] [data-group-button="generate"]').click();
		await page.locator('[data-rail-sheet="generate"] [data-sheet-item="restore"]').click();
		await expect(page.locator('[data-floating-prompt="phone"]')).toBeVisible();
		await expect(page.locator('[data-task-range-bar] [data-range-options]')).toBeVisible();
		await expect(page.locator('[data-floating-prompt="phone"] [data-prompt-status="range"]')).toHaveText('Whole clip');
		const scroll = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(scroll).toBeLessThanOrEqual(0);
		await screenshot(page, JOURNEY, 'ai-phone-dark-390');
	});
});
