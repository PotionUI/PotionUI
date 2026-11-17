import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, closeEditorMenu, editorMenuItem, loginAsOwner, screenshot } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-generate';
const GENERATION_ID = 'gen-e2e-generate';
const runGeneration = (n: number) => `gen-e2e-run-${n}`;
const FILE_ID = 503;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_generate';
const PREVIEW = solidPng(192, 160).toString('base64');

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'source_image', type: 'image', title: 'Picture' },
				{ name: 'strength', type: 'number', title: 'Strength', default: 0.85 },
				{ name: 'steps', type: 'slider', title: 'Steps', default: 28 },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 },
				{ name: 'quantity', type: 'integer', title: 'Quantity', default: 1 }
			]
		}
	}
};

const OWNED = ['quantity', 'source_image', 'source_image_inpaint_mask'];

const qwen = (over: Json = {}): Json => ({ preset_id: 'qwen21', preset_name: 'Qwen-Image 2.1', mode: 'edit', mode_label: 'Edit', label: null, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, owned: OWNED, ...over });
const flux = (over: Json = {}): Json => ({ preset_id: 'flux-fill', preset_name: 'Flux Fill', mode: 'inpaint', mode_label: 'Inpaint', label: null, variant: null, region_strategy: 'crop', via: null, qualifies: true, reason: null, owned: OWNED, ...over });

function taskEntry(id: string, label: string, over: Json = {}): Json {
	const remove = id === 'remove_object';
	return {
		id,
		label,
		icon: remove ? 'mode-remove-object' : 'sparkles',
		region: remove ? 'required' : 'optional',
		prompt: remove ? 'none' : 'required',
		fallback: remove ? { task: 'edit_region', prompt: 'Remove the marked object.' } : null,
		offers: [qwen(), flux()],
		default: null,
		...over
	};
}

interface Server {
	tasks: Json[];
	runs: Json[];
	runStatus: Json;
	live: Json;
	runGets: string[];
	cancels: string[];
	defaults: Json[];
	startFails: { status: number; body: Json } | null;
	saves: Json[];
	sessionPuts: Json[];
	sessionPosts: Json[];
	sessionFails: { status: number; body: Json } | null;
	mediaRuns: Json[];
	sockets: WebSocketRoute[];
	runCount: number;
	holdSubscribe: boolean;
	held: Array<() => void>;
}

function runBody(server: Server, status: string, extra: Json = {}, n = server.runCount): Json {
	return {
		id: `er_${n}`,
		task: 'edit_region',
		status,
		edit_id: EDIT_ID,
		step_id: 's1',
		generation_id: runGeneration(n),
		progress: status === 'done' ? 1 : 0,
		error: null,
		soft: false,
		rect: { x: 0.55, y: 0.35, width: 0.3, height: 0.4 },
		candidates: [],
		...extra
	};
}

function candidates(count: number): Json[] {
	return Array.from({ length: count }, (_, i) => ({ patch: { item_id: `patch-${i + 1}` }, url: `/api/e2e-art/patch-${i + 1}.png`, seed: 100 + i, generation_file_id: String(900 + i) }));
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockAll(page: Page, tasks: Json[], options: { sessions?: boolean } = {}): Promise<Server> {
	const server: Server = { tasks, runs: [], runStatus: {}, live: {}, runGets: [], cancels: [], defaults: [], startFails: null, saves: [], sessionPuts: [], sessionPosts: [], sessionFails: null, mediaRuns: [], sockets: [], runCount: 0, holdSubscribe: false, held: [] };
	let revision = 1;
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);
	const generation = () => {
		const now = new Date().toISOString();
		return { id: GENERATION_ID, preset_id: 'krea2', preset_name: 'Krea 2', mode: 'txt2img', form_data: {}, status: 'completed', progress: 1, created_at: now, updated_at: now, completed_at: now, files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }] };
	};
	await mockMediaTools(page);
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		server.mediaRuns.push(route.request().postDataJSON() as Json);
		await ok(route, { run_id: `run-${server.mediaRuns.length}` }, 202);
	});
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, { generations: [generation()], total: 1 }) : route.fallback()));
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) => ok(route, { id: `art-${server.runs.length + 1}`, media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 }));
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media\/upload$/, (route) => ok(route, { path: 'uploads/mask.png', relative_path: 'uploads/mask.png', filename: 'mask.png', url: '/api/e2e-art/mask.png', width: 480, height: 480, size: 4096 }));
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		const now = new Date().toISOString();
		const record = { id: id ?? EDIT_ID, tool: 'image', name: null, source: { kind: 'history', id: String(FILE_ID) }, thumbnail_upload_id: null, revision, created_at: now, updated_at: now };
		if (request.method() === 'POST' && !id) {
			server.saves.push(request.postDataJSON() as Json);
			return ok(route, { edit: { ...record, id: EDIT_ID } });
		}
		if (request.method() === 'GET' && id) return ok(route, { edit: { ...record, state: (server.saves.at(-1)?.state as Json) ?? {} } });
		if (request.method() === 'PUT' && id) {
			server.saves.push(request.postDataJSON() as Json);
			revision += 1;
			return ok(route, { edit: { ...record, revision } });
		}
		return route.fallback();
	});
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks: server.tasks }));
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		const body = route.request().postDataJSON() as Json;
		if (server.startFails) return route.fulfill({ status: server.startFails.status, contentType: 'application/json', body: JSON.stringify({ detail: server.startFails.body }) });
		server.runs.push(body);
		server.runCount += 1;
		server.live = {};
		await ok(route, { run: runBody(server, 'queued') }, 202);
	});
	await page.route(/\/api\/editor\/tasks\/[^/]+\/default$/, async (route) => {
		if (route.request().method() === 'PUT') server.defaults.push(route.request().postDataJSON() as Json);
		await ok(route, { task: server.tasks[0] });
	});
	await page.route(/\/api\/editor\/runs\/[^/]+$/, async (route) => {
		const id = route.request().url().split('/').pop() as string;
		if (route.request().method() === 'DELETE') {
			server.cancels.push(id);
			return ok(route, { run: runBody(server, 'cancelled') });
		}
		server.runGets.push(id);
		await ok(route, { run: runBody(server, ((server.runStatus.status ?? server.live.status) as string) ?? 'queued', { ...server.live, ...server.runStatus }) });
	});
	await page.route(/\/api\/sessions\/preset\/[^/?]+(\?.*)?$/, (route) =>
		ok(route, options.sessions ? [{ id: 'sess-1', preset_id: 'qwen21', name: 'Soft portrait', data: { edit: { formData: { steps: 20 } } }, created_at: new Date(Date.now() - 9 * 86400000).toISOString(), updated_at: new Date(Date.now() - 2 * 86400000).toISOString(), pinned: false, version: 3, kind: 'editor' }] : [])
	);
	const sessionBody = (body: Json, id: string, version: number): Json => ({ id, preset_id: 'qwen21', name: body.name, data: body.data, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), pinned: false, version, kind: 'editor' });
	await page.route(/\/api\/sessions\/save$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		const body = route.request().postDataJSON() as Json;
		server.sessionPosts.push(body);
		if (server.sessionFails) return route.fulfill({ status: server.sessionFails.status, contentType: 'application/json', body: JSON.stringify({ detail: server.sessionFails.body }) });
		await ok(route, sessionBody(body, `sess-new-${server.sessionPosts.length}`, 1));
	});
	await page.route(/\/api\/sessions\/sess-1$/, async (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		const body = route.request().postDataJSON() as Json;
		server.sessionPuts.push(body);
		if (server.sessionFails) return route.fulfill({ status: server.sessionFails.status, contentType: 'application/json', body: JSON.stringify({ detail: server.sessionFails.body }) });
		await ok(route, sessionBody(body, 'sess-1', 4));
	});
	await page.route(/\/api\/formulas(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, []) : route.fallback()));
	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		server.sockets.push(ws);
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-mock' }));
		ws.onMessage((raw) => {
			const message = JSON.parse(raw as string);
			if (message.type !== 'subscribe_generation') return;
			const acknowledge = () => ws.send(JSON.stringify({ type: 'subscribed', generation_id: message.generation_id }));
			if (server.holdSubscribe) server.held.push(acknowledge);
			else acknowledge();
		});
	});
	return server;
}

function push(server: Server, message: Json) {
	for (const socket of server.sockets) socket.send(JSON.stringify(message));
}

const update = (server: Server, status: string, extra: Json = {}, n = server.runCount) => {
	if (n === server.runCount) server.live = { status, ...extra };
	push(server, { type: 'editor_run_update', generation_id: runGeneration(n), run: runBody(server, status, extra, n) });
};

const sessionDefault: Json = { preset_id: 'qwen21', mode: 'edit', variant: null, setup: { kind: 'session', id: 'sess-1' }, health: { ok: true, reason: null, name: null, mode_label: null } };

const rail = (page: Page, tool: string) => page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
async function openPhoneTask(page: Page, id: string) {
	await page.locator('[data-tool-rail] [data-group-button="generate"]').click();
	await page.locator(`[data-rail-sheet="generate"] [data-sheet-item="${id}"]`).click();
}
const options = (page: Page) => page.locator('[data-options-panel]');
const prompt = (page: Page) => page.locator('[data-floating-prompt]');
const row = (page: Page, id = 's1') => page.locator(`[data-edit-stack] [data-stack-row="${id}"]`);

async function begin(page: Page, tasks: Json[], sessions = false): Promise<Server> {
	const server = await mockAll(page, tasks, { sessions });
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	await expect(page.locator(phone ? '.studio-topbar' : '.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return server;
}

async function openHarbor(page: Page) {
	if ((page.viewportSize()?.width ?? 1440) < 768) {
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		await page.getByTestId('phone-tool-tab').locator('[data-recent-history] button').first().click();
		await expect(page.locator('[data-editor-frame="phone"]')).toBeVisible();
		return;
	}
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-recent-history] button').first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
}

async function drag(page: Page, path: Array<[number, number]>): Promise<boolean> {
	const box = await page.locator('[data-mask-layer]').boundingBox();
	if (!box) return false;
	const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
	const [first, ...rest] = path;
	await page.mouse.move(at(first).x, at(first).y);
	await page.mouse.down();
	for (const point of rest) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
	await page.mouse.up();
	return true;
}

async function markArea(page: Page) {
	await expect(page.locator('[data-mask-layer]')).toBeVisible();
	await expect(page.getByRole('group', { name: 'Marking tool' }).getByRole('button', { name: 'Lasso', exact: true })).toHaveAttribute('aria-pressed', 'true');
	for (let attempt = 0; attempt < 4; attempt++) {
		await page.waitForTimeout(500);
		if (await drag(page, [[0.55, 0.3], [0.85, 0.3], [0.88, 0.7], [0.58, 0.72], [0.5, 0.5]])) {
			if (await page.locator('[data-mask-area]').isVisible()) break;
		}
	}
	await expect(page.locator('[data-mask-area]')).toBeVisible();
	await expect(prompt(page).locator('[data-prompt-status="area"]')).toBeVisible();
}

async function shape(page: Page, aspect: string) {
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	const scope = phone ? page.getByTestId('phone-tool-tab') : page.locator('[data-options-panel]');
	await scope.locator('[data-crop-aspects] button', { hasText: aspect }).click();
	await page.locator('[data-options-apply]').click();
}

async function scaleTo(page: Page, percent: string) {
	await page.getByRole('button', { name: percent, exact: true }).click();
	await page.locator('[data-options-apply]').click();
}

async function pickDefaults(page: Page) {
	await expect(page.locator('[data-setup-picker]')).toBeVisible();
	await page.locator('[data-picker-option="defaults"]').first().click();
	await page.locator('[data-picker-use]').click();
	await expect(page.locator('[data-setup-picker]')).toHaveCount(0);
}

async function type(page: Page, text: string) {
	await prompt(page).locator('[data-prompt-field]').fill(text);
}

async function generate(page: Page) {
	await prompt(page).locator('[data-prompt-generate]').click();
}

test.describe('Edit a region and Remove an object on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('first use, a lasso, a prompt, a run with two results, choosing the second, and Save', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object')], true);
		await openHarbor(page);
		await expect(rail(page, 'edit_region')).toBeVisible();
		await expect(rail(page, 'remove_object')).toBeVisible();
		await rail(page, 'edit_region').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'edit_region');

		await expect(page.locator('[data-setup-picker]')).toBeVisible();
		await expect(prompt(page).locator('[data-setup-chip]')).toHaveText(/Choose a setup/);
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeDisabled();
		await screenshot(page, JOURNEY, '02-first-use-dark-1440');
		await page.locator('[data-picker-option="session"]').first().click();
		await page.locator('[role="switch"]').click();
		await page.locator('[data-picker-use]').click();
		await expect.poll(() => server.defaults.length).toBe(1);
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Qwen-Image 2.1 · Edit · Soft portrait');

		await screenshot(page, JOURNEY, '08-whole-picture-dark-1440');
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		await prompt(page).getByRole('group', { name: 'Results' }).getByRole('button', { name: '2', exact: true }).click();
		await screenshot(page, JOURNEY, '01-prompt-dark-1440');
		await generate(page);

		await expect.poll(() => server.runs.length).toBe(1);
		const body = server.runs[0];
		expect(body).toMatchObject({ prompt: 'a small red sailboat, one white sail, calm water', candidates: 2, edit: { id: EDIT_ID }, setup: { preset_id: 'qwen21', mode: 'edit', form_data: { steps: 20 } } });
		expect((body.region as Json).item_id).toBeTruthy();
		await expect(row(page)).toContainText('Waiting');
		await expect(page.locator('[data-run-chip="queued"]')).toContainText('Waiting for a free slot');
		await expect(prompt(page).locator('[data-prompt-cancel]')).toBeVisible();
		await screenshot(page, JOURNEY, '05b-waiting-dark-1440');

		update(server, 'running', { progress: 0.4 });
		push(server, { type: 'workbench_update', generation_id: runGeneration(server.runCount), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(page.locator('[data-run-chip="running"]')).toContainText('Generating · 40%');
		await expect(page.locator('[data-run-preview]')).toBeVisible();
		await expect(row(page)).toContainText('Generating 40%');
		await screenshot(page, JOURNEY, '05-running-dark-1440');

		update(server, 'done', { candidates: candidates(2) });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await expect(row(page)).toContainText('of 2');
		await page.locator('[data-candidate="1"]').click();
		await expect(page.locator('[data-candidate="1"]')).toHaveAttribute('aria-pressed', 'true');
		await expect(row(page)).toContainText('2 of 2');
		await expect(row(page).locator('[data-stack-thumb]')).toBeVisible();
		await screenshot(page, JOURNEY, '06-done-dark-1440');
		await screenshot(page, JOURNEY, '09-stack-dark-1440');

		await page.locator('[data-frame-undo]').click();
		await expect(row(page)).toContainText('1 of 2');
		await page.keyboard.press('Control+Shift+Z');
		await expect(row(page)).toContainText('2 of 2');

		await page.locator('[data-frame-save-menu]').click();
		await page.locator('[data-save-row="save-new"]').click();
		await expect.poll(() => server.mediaRuns.length).toBe(1);
		const steps = (server.mediaRuns[0].steps as Json[]).filter((step) => step.tool === 'place_patch');
		expect(steps).toHaveLength(1);
		expect(steps[0].hidden).toBeFalsy();
		expect((steps[0].params as Json).patch).toEqual({ item_id: 'patch-2' });
	});

	test('Try again runs the same settings with a new seed and replaces the results', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Model defaults');
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(2) });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await page.locator('[data-candidate-again]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		expect((server.runs[1].setup as Json).form_data).toMatchObject({ seed: -1, steps: 28 });
		await expect(row(page)).toContainText('Waiting');
	});

	test('Remove an object needs a marked object and sends no prompt', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'remove_object').click();
		await expect(prompt(page).locator('[data-prompt-field]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-prompt-note]')).toHaveText('The marked object will be removed.');
		await expect(prompt(page).locator('[data-prompt-status="warn"]')).toHaveText('Mark the object first');
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeDisabled();
		await expect(await editorMenuItem(page, 'Edit', 'Clear area')).toHaveAttribute('aria-disabled', 'true');
		await closeEditorMenu(page);
		await screenshot(page, JOURNEY, '07-remove-dark-1440');
		const clearArea = page.locator('[data-options-panel] [data-clear-area]');
		await expect(clearArea).toBeDisabled();
		await markArea(page);
		await expect(clearArea).toBeEnabled();
		await clearArea.click();
		await expect(page.locator('[data-mask-area]')).toHaveCount(0);
		await expect(clearArea).toBeDisabled();
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeDisabled();
		await expect(prompt(page).locator('[data-prompt-status="warn"]')).toHaveText('Mark the object first');
		await markArea(page);
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0].prompt).toBeNull();
		update(server, 'done', { candidates: candidates(1) });
		await expect(row(page)).toContainText('Remove an object');
		await expect(row(page)).toContainText('1 of 1');
	});

	test('a failed run shows its reason and Retry starts it again', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'failed', { error: 'timed out' });
		await expect(row(page)).toContainText('Did not finish: timed out');
		await expect(row(page).locator('[data-stack-action="retry"]')).toHaveCount(1);
		await row(page).hover();
		await row(page).locator('[data-stack-action="retry"]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		await expect(row(page)).toContainText('Waiting');
	});

	test('a run that failed before the subscription was acknowledged still shows its reason', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		server.holdSubscribe = true;
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await expect.poll(() => server.held.length).toBe(1);
		await expect(row(page)).toContainText('Waiting');
		server.runStatus = { status: 'failed', error: 'timed out' };
		server.holdSubscribe = false;
		server.held.splice(0).forEach((acknowledge) => acknowledge());
		await expect(row(page)).toContainText('Did not finish: timed out');
	});

	test('every row state of a generated step: done, out of date, failed, cancelled, waiting and generating', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await expect(row(page, 's1')).toContainText('Crop');
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'red sailboat');
		await prompt(page).getByRole('group', { name: 'Results' }).getByRole('button', { name: '3', exact: true }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(3) }, 1);
		await expect(row(page, 's2')).toContainText('1 of 3');
		await page.locator('[data-stack-open="s1"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
		await shape(page, '16:9');
		await expect(row(page, 's2')).toContainText('The picture changed');
		await rail(page, 'edit_region').click();
		await markArea(page);
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(2);
		update(server, 'done', { candidates: candidates(3) }, 2);
		await expect(row(page, 's3')).toContainText('1 of 3');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(3);
		update(server, 'failed', { error: 'timed out' }, 3);
		await expect(row(page, 's4')).toContainText('Did not finish: timed out');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(4);
		update(server, 'cancelled', {}, 4);
		await expect(row(page, 's5')).toContainText('Cancelled');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(5);
		await expect(row(page, 's6')).toContainText('Waiting');
		await page.locator('[data-stack-open="s3"]').click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(6);
		update(server, 'running', { progress: 0.4 }, 6);
		push(server, { type: 'workbench_update', generation_id: runGeneration(6), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(row(page, 's7')).toContainText('Generating 40%');
		await expect(row(page, 's2')).toContainText('The picture changed');
		await screenshot(page, JOURNEY, '09c-row-states-dark-1440');
	});

	test('a step whose picture beneath changed shape is out of date, left out of Save, and up to date again when the change is undone', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await prompt(page).getByRole('group', { name: 'Results' }).getByRole('button', { name: '2', exact: true }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(2) });
		await expect(row(page, 's2')).toContainText('1 of 2');
		await expect(row(page, 's2').locator('[data-stack-action="redo"]')).toHaveCount(0);

		await page.locator('[data-stack-open="s1"]').click();
		await shape(page, '16:9');
		await expect(row(page, 's2')).toContainText('The picture changed');
		await expect(row(page, 's2').locator('[data-stack-action="redo"]')).toHaveCount(1);
		await expect(row(page, 's2').locator('[data-stack-hide="s2"]')).toHaveCount(0);
		await expect(row(page, 's2').locator('[data-stack-thumb]')).toHaveCount(0);

		await page.locator('[data-frame-undo]').click();
		await expect(row(page, 's2')).toContainText('1 of 2');
		await expect(row(page, 's2')).not.toContainText('The picture changed');
		await page.keyboard.press('Control+Shift+Z');
		await expect(row(page, 's2')).toContainText('The picture changed');

		await rail(page, 'edit_region').click();
		await markArea(page);
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(2);
		await expect(row(page, 's3')).toContainText('Waiting');
		await page.locator('[data-frame-save-menu]').click();
		const notices = page.locator('[data-save-notices]');
		await expect(notices).toContainText('1 edit is not included: it is still generating.');
		await expect(notices).toContainText('1 edit is not included: the picture beneath it changed.');
		await screenshot(page, JOURNEY, '09b-save-menu-dark-1440');
		await page.locator('[data-save-row="save-new"]').click();
		await expect.poll(() => server.mediaRuns.length).toBe(1);
		const patches = (server.mediaRuns[0].steps as Json[]).filter((step) => step.tool === 'place_patch');
		expect(patches).toHaveLength(2);
		expect(patches.every((step) => step.hidden === true)).toBe(true);
		expect(patches.some((step) => (step.params as Json).patch && ((step.params as Json).patch as Json).item_id === 'patch-1')).toBe(true);
	});

	test('after a resize beneath, Redo on the new picture runs again with the same area and seed and replaces the results', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'resize').click();
		await scaleTo(page, '75%');
		await expect(row(page, 's1')).toContainText('Resize');
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await prompt(page).getByRole('group', { name: 'Results' }).getByRole('button', { name: '2', exact: true }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(2) });
		await expect(row(page, 's2')).toContainText('1 of 2');

		await page.locator('[data-stack-open="s1"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'resize');
		await scaleTo(page, '50%');
		await expect(row(page, 's2')).toContainText('The picture changed');

		await page.locator('[data-stack-open="s2"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'edit_region');
		await expect(page.locator('[data-stale-outline]')).toBeVisible();
		await expect(page.locator('[data-candidate-strip]')).toHaveCount(0);
		await prompt(page).locator('[data-prompt-field]').press('Escape');
		await expect(page.locator('[data-stale-outline]')).toHaveCount(0);
		await row(page, 's2').hover();
		await row(page, 's2').locator('[data-stack-action="redo"]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		const body = server.runs[1];
		expect(body.region).toEqual(server.runs[0].region);
		expect(body.seed).toBe(100);
		expect(body.candidates).toBe(2);
		expect(body.prompt).toBe('a lantern');
		expect((body.edit as Json).step_id).toBe('s2');
		expect(((body.steps as Json[])[0].params as Json).width).toBe(320);
		await expect(row(page, 's2')).toContainText('Waiting');
		await expect(page.locator('[data-edit-stack] [data-stack-row]')).toHaveCount(3);
		update(server, 'done', { candidates: candidates(1) });
		await expect(row(page, 's2')).toContainText('1 of 1');
		await expect(row(page, 's2')).not.toContainText('The picture changed');
		await expect(page.locator('[data-stale-outline]')).toHaveCount(0);

		await page.locator('[data-frame-undo]').click();
		await expect(row(page, 's2')).toContainText('The picture changed');
	});

	test('after a crop beneath, Redo asks for the area again and Generate replaces the same step', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(1) });
		await expect(row(page, 's2')).toContainText('1 of 1');
		await page.locator('[data-stack-open="s1"]').click();
		await shape(page, '16:9');
		await expect(row(page, 's2')).toContainText('The picture changed');
		await row(page, 's2').hover();
		await row(page, 's2').locator('[data-stack-action="redo"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'edit_region');
		await expect(prompt(page).locator('[data-prompt-status="redo"]')).toHaveText('Mark the area again');
		await expect(prompt(page).locator('[data-prompt-redo]')).toHaveText('The picture beneath changed shape');
		await expect(prompt(page).locator('[data-prompt-field]')).toHaveValue('a lantern');
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeDisabled();
		expect(server.runs).toHaveLength(1);
		await markArea(page);
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeEnabled();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(2);
		expect((server.runs[1].edit as Json).step_id).toBe('s2');
		expect(server.runs[1].seed).toBe(100);
		await expect(row(page, 's2')).toContainText('Waiting');
		await expect(page.locator('[data-edit-stack] [data-stack-row]')).toHaveCount(3);
	});

	test('Cancel stops a waiting run, and removing a running step cancels it too', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await page.locator('[data-run-cancel]').click();
		await expect.poll(() => server.cancels).toEqual(['er_1']);
		await expect(row(page)).toContainText('Cancelled');

		await row(page).hover();
		await row(page).locator('[data-stack-action="retry"]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		await row(page).hover();
		await page.locator('[data-frame-save-menu]').click();
		await expect(page.locator('[data-save-notices]')).toContainText('1 edit is not included: it is still generating.');
		await page.keyboard.press('Escape');
		await row(page).hover();
		await row(page).locator('[data-stack-action="cancel"]').click();
		await expect.poll(() => server.cancels.length).toBe(2);
	});

	test('a default that broke is never replaced silently: the picker names what went away', async ({ page }) => {
		const broken = { preset_id: 'qwen21', mode: 'edit', variant: null, setup: { kind: 'session', id: 'gone', name: 'Soft portrait' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } };
		await begin(page, [taskEntry('edit_region', 'Edit a region', { default: broken })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await expect(page.locator('[data-picker-notice]')).toHaveText('Your default setup "Soft portrait" was deleted. Pick another.');
		await expect(prompt(page).locator('[data-setup-chip]')).toHaveText(/Choose a setup/);
		await expect(prompt(page).locator('[data-prompt-generate]')).toBeDisabled();
		await screenshot(page, JOURNEY, '03-broken-default-dark-1440');
	});

	test('no model: the empty picker, and the greyed picker with its reasons', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await expect(page.locator('[data-picker-empty]')).toContainText('No model can do this yet');
		await screenshot(page, JOURNEY, '11-picker-empty-dark-1440');
	});

	test('no model can run: the modes are greyed with the reason', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen({ qualifies: false, reason: 'models_missing' }), flux({ qualifies: false, reason: 'not_assigned' })] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await expect(page.locator('[data-setup-picker]')).toContainText('Models not installed');
		await expect(page.locator('[data-setup-picker]')).toContainText('Not available to your account');
		await screenshot(page, JOURNEY, '11b-picker-greyed-dark-1440');
	});

	test('reloading in the middle of a run re-attaches to it', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await expect(row(page)).toContainText('Waiting');
		await expect.poll(() => server.saves.length).toBeGreaterThan(1);
		server.runStatus = { status: 'done', candidates: candidates(2) };
		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		await expect.poll(() => server.runGets).toContain('er_1');
		await expect(row(page)).toContainText('of 2');
		expect(server.runs).toHaveLength(1);
	});

	test('Settings: change a value, run with it, save to the setup, save as new, and the discard prompt', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Qwen-Image 2.1 · Edit · Soft portrait');
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);
		await markArea(page);
		await type(page, 'a small red sailboat');

		await prompt(page).locator('[data-prompt-settings]').click();
		const view = page.locator('[data-frame-panel] [data-settings-view="desktop"]');
		await expect(view).toBeVisible();
		await expect(options(page)).toHaveCount(0);
		await expect(view.locator('[data-settings-header]')).toContainText('Edit a region');
		await expect(view.locator('[data-settings-setup]')).toHaveText('Qwen-Image 2.1 · Edit · Soft portrait');
		await expect(view.locator('[data-settings-body]')).toContainText('Strength');
		await expect(view.locator('[data-settings-body]')).not.toContainText('Quantity');
		await expect(view.locator('[data-settings-body]')).not.toContainText('Picture');
		await expect(page.locator('[data-edit-stack]')).toHaveAttribute('data-folded', 'true');
		await expect(view.locator('[data-settings-save-to]')).toHaveCount(0);
		await expect(view.locator('[data-settings-save-new]')).toBeVisible();

		const strength = view.locator('[data-settings-body] input[type="number"]').first();
		await strength.fill('0.6');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toHaveText('Changed');
		await expect(prompt(page).locator('[data-setup-changed]')).toBeVisible();
		await expect(view.locator('[data-settings-save-to]')).toHaveText('Save to “Soft portrait”');
		await screenshot(page, JOURNEY, '04-settings-dark-1440');

		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		const form = (server.runs[0].setup as Json).form_data as Json;
		expect(form).toMatchObject({ strength: 0.6, steps: 20 });
		expect(form).not.toHaveProperty('source_image');
		expect(form).not.toHaveProperty('quantity');

		await view.locator('[data-settings-save-to]').click();
		await expect.poll(() => server.sessionPuts.length).toBe(1);
		expect(server.sessionPuts[0]).toMatchObject({ name: 'Soft portrait', mode: 'edit', base_version: 3, kind: 'editor', data: { edit: { formData: { strength: 0.6, steps: 20 } } } });
		await expect(view.locator('[data-settings-changed]')).toHaveCount(0);
		await expect(view.locator('[data-settings-save-to]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);

		await strength.fill('0.4');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		server.sessionFails = { status: 409, body: { error: 'session_kind_mismatch', message: 'x' } };
		await view.locator('[data-settings-save-new]').click();
		await page.locator('[data-save-setup-name]').fill('Held by Generate');
		await page.getByRole('dialog', { name: 'Save as new setup' }).getByRole('button', { name: /^Save/ }).click();
		await expect(page.locator('[data-save-setup-error]')).toHaveText('That name is already used by one of your sessions on the Generate page. Choose another name.');
		server.sessionFails = null;
		await page.locator('[data-save-setup-name]').fill('Crisp');
		await page.getByRole('dialog', { name: 'Save as new setup' }).getByRole('button', { name: /^Save/ }).click();
		await expect.poll(() => server.sessionPosts.length).toBe(2);
		expect(server.sessionPosts[1]).toMatchObject({ preset_id: 'qwen21', name: 'Crisp', mode: 'edit', kind: 'editor', data: { edit: { formData: { strength: 0.4 } } } });
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Crisp');
		await expect(view.locator('[data-settings-changed]')).toHaveCount(0);
		expect(server.defaults).toHaveLength(0);

		await strength.fill('0.3');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		await prompt(page).locator('[data-setup-chip]').click();
		await page.locator('[data-picker-option="defaults"]').first().click();
		await page.locator('[data-picker-use]').click();
		const discard = page.locator('[role="dialog"], [role="alertdialog"]').filter({ hasText: 'Discard your changes?' }).last();
		await expect(discard.getByText('Discard your changes?')).toBeVisible();
		await expect(discard).toContainText('Your changes to “Crisp” are used only for this edit unless you save them.');
		await screenshot(page, JOURNEY, '04b-discard-dark-1440');
		await discard.getByRole('button', { name: /Keep editing/ }).click();
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Crisp');
		await page.locator('[data-picker-use]').click();
		await discard.getByRole('button', { name: /^Discard/ }).click();
		await expect(prompt(page).locator('[data-setup-chip]')).toContainText('Model defaults');
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);

		await page.keyboard.press('Escape');
		await expect(view).toHaveCount(0);
		await expect(prompt(page)).toBeVisible();
		await expect(options(page)).toBeVisible();
		await expect(page.locator('[data-edit-stack]')).not.toHaveAttribute('data-folded', 'true');
	});

	test('Settings go back with the header button and the prompt button, and the Edits list can be shown while open', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		const settings = prompt(page).locator('[data-prompt-settings]');
		await settings.click();
		await expect(settings).toHaveAttribute('aria-pressed', 'true');
		const view = page.locator('[data-settings-view="desktop"]');
		await expect(view).toBeVisible();
		await page.locator('[data-stack-fold]').click();
		await expect(page.locator('[data-edit-stack]')).not.toHaveAttribute('data-folded', 'true');
		await view.locator('[data-detail-back]').click();
		await expect(view).toHaveCount(0);
		await expect(options(page)).toBeVisible();
		await settings.click();
		await expect(view).toBeVisible();
		await settings.click();
		await expect(view).toHaveCount(0);
	});
});

test.describe('Edit a region on the phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the prompt sheet, the picker sheet and the run', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object')], true);
		await openHarbor(page);
		await openPhoneTask(page, 'edit_region');
		await expect(page.locator('[data-setup-picker="sheet"]')).toBeVisible();
		await screenshot(page, JOURNEY, '02-first-use-dark-390');
		await page.locator('[data-picker-option="session"]').first().click();
		await page.locator('[data-picker-use]').click();
		await expect(prompt(page)).toBeVisible();
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		await screenshot(page, JOURNEY, '01-prompt-dark-390');
		await prompt(page).getByRole('button', { name: 'Collapse' }).click();
		await expect(page.locator('[data-floating-prompt="collapsed"]')).toBeVisible();
		await screenshot(page, JOURNEY, '01b-prompt-collapsed-dark-390');
		await page.locator('[data-floating-prompt="collapsed"]').getByRole('button', { name: 'Expand' }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await screenshot(page, JOURNEY, '05b-waiting-dark-390');
		update(server, 'running', { progress: 0.4 });
		push(server, { type: 'workbench_update', generation_id: runGeneration(server.runCount), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(page.locator('[data-run-chip="running"]')).toBeVisible();
		await screenshot(page, JOURNEY, '05-running-dark-390');
		update(server, 'done', { candidates: candidates(3) });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await screenshot(page, JOURNEY, '06-done-dark-390');
		await page.locator('[data-frame-edits]').click();
		await expect(page.getByRole('dialog', { name: 'Edits' })).toBeVisible();
		await screenshot(page, JOURNEY, '09-stack-dark-390');
	});

	test('an out of date step on the phone', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await openPhoneTask(page, 'edit_region');
		await markArea(page);
		await type(page, 'red sailboat');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(3) });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await page.locator('[data-frame-edits]').click();
		const sheet = page.getByRole('dialog', { name: 'Edits' });
		await expect(sheet).toBeVisible();
		await sheet.locator('[data-stack-open="s1"]').click();
		await page.locator('[data-frame-sheet] button[aria-label="Close"]').click();
		await expect(sheet).toHaveCount(0);
		await shape(page, '16:9');
		await page.locator('[data-frame-edits]').click();
		await expect(page.getByRole('dialog', { name: 'Edits' }).locator('[data-stack-row="s2"]')).toContainText('The picture changed');
		await screenshot(page, JOURNEY, '09c-row-states-dark-390');
	});

	test('Remove an object on the phone', async ({ page }) => {
		await begin(page, [taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
		await openHarbor(page);
		await openPhoneTask(page, 'remove_object');
		await expect(page.locator('[data-editor-layer]')).toBeVisible();
		await expect(prompt(page).locator('[data-prompt-status="warn"]')).toBeVisible();
		await screenshot(page, JOURNEY, '07-remove-dark-390');
	});

	test('Settings replace the prompt in the same sheet, with a back arrow and the discard prompt', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true);
		await openHarbor(page);
		await openPhoneTask(page, 'edit_region');
		await expect(prompt(page)).toBeVisible();
		await prompt(page).locator('[data-prompt-settings]').click();
		const view = page.locator('[data-settings-view="phone"]');
		await expect(view).toBeVisible();
		await expect(prompt(page)).toHaveCount(0);
		await expect(page.locator('[data-frame-sheet]')).toHaveCount(0);
		const strength = view.locator('[data-settings-body] input[type="number"]').first();
		await strength.fill('0.6');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		await expect(view.locator('[data-settings-save-to]')).toBeVisible();
		await screenshot(page, JOURNEY, '04-settings-dark-390');
		await view.locator('[data-detail-back]').click();
		await expect(view).toHaveCount(0);
		await expect(prompt(page)).toBeVisible();
		await expect(prompt(page).locator('[data-setup-changed]')).toBeVisible();
		await prompt(page).locator('[data-setup-chip]').click();
		await page.locator('[data-picker-option="defaults"]').first().click();
		await page.locator('[data-picker-use]').click();
		const discard = page.locator('[role="dialog"], [role="alertdialog"]').filter({ hasText: 'Discard your changes?' }).last();
		await expect(discard.getByText('Discard your changes?')).toBeVisible();
		await screenshot(page, JOURNEY, '04b-discard-dark-390');
	});
});
