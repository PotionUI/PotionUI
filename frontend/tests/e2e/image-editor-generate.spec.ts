import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot } from './helpers';
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
				{ name: 'prompt', type: 'string', title: 'Prompt', default: '' },
				{ name: 'source_image', type: 'image', title: 'Picture' },
				{ name: 'steps', type: 'slider', title: 'Steps', default: 28 },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

const qwen = (over: Json = {}): Json => ({ preset_id: 'qwen21', preset_name: 'Qwen-Image 2.1', mode: 'edit', mode_label: 'Edit', label: null, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over });
const flux = (over: Json = {}): Json => ({ preset_id: 'flux-fill', preset_name: 'Flux Fill', mode: 'inpaint', mode_label: 'Inpaint', label: null, variant: null, region_strategy: 'crop', via: null, qualifies: true, reason: null, ...over });

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
	runGets: string[];
	cancels: string[];
	defaults: Json[];
	startFails: { status: number; body: Json } | null;
	saves: Json[];
	mediaRuns: Json[];
	sockets: WebSocketRoute[];
	runCount: number;
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
	const server: Server = { tasks, runs: [], runStatus: {}, runGets: [], cancels: [], defaults: [], startFails: null, saves: [], mediaRuns: [], sockets: [], runCount: 0 };
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
		await ok(route, { run: runBody(server, (server.runStatus.status as string) ?? 'running', server.runStatus) });
	});
	await page.route(/\/api\/sessions\/preset\/[^/?]+(\?.*)?$/, (route) =>
		ok(route, options.sessions ? [{ id: 'sess-1', preset_id: 'qwen21', name: 'Soft portrait', data: { edit: { formData: { steps: 20 } } }, updated_at: new Date(Date.now() - 2 * 86400000).toISOString() }] : [])
	);
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

function push(server: Server, message: Json) {
	for (const socket of server.sockets) socket.send(JSON.stringify(message));
}

const update = (server: Server, status: string, extra: Json = {}, n = server.runCount) =>
	push(server, { type: 'editor_run_update', generation_id: runGeneration(n), run: runBody(server, status, extra, n) });

const rail = (page: Page, tool: string) => page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
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

async function drag(page: Page, path: Array<[number, number]>) {
	const box = (await page.locator('[data-mask-layer]').boundingBox())!;
	const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
	const [first, ...rest] = path;
	await page.mouse.move(at(first).x, at(first).y);
	await page.mouse.down();
	for (const point of rest) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
	await page.mouse.up();
}

async function markArea(page: Page) {
	await expect(page.locator('[data-mask-layer]')).toBeVisible();
	await expect(page.getByRole('group', { name: 'Marking tool' }).getByRole('button', { name: 'Lasso', exact: true })).toHaveAttribute('aria-pressed', 'true');
	await drag(page, [[0.55, 0.3], [0.85, 0.3], [0.88, 0.7], [0.58, 0.72], [0.5, 0.5]]);
	await expect(page.locator('[data-mask-area]')).toBeVisible();
	await expect(prompt(page).locator('[data-prompt-status="area"]')).toBeVisible();
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
		await expect(page.locator('[data-clear-area]')).toBeDisabled();
		await screenshot(page, JOURNEY, '07-remove-dark-1440');
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

	test('every row state of a generated step: done, failed, cancelled, waiting and generating', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		await markArea(page);
		await type(page, 'red sailboat');
		await prompt(page).getByRole('group', { name: 'Results' }).getByRole('button', { name: '3', exact: true }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(3) }, 1);
		await expect(row(page, 's1')).toContainText('1 of 3');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(2);
		update(server, 'failed', { error: 'timed out' }, 2);
		await expect(row(page, 's2')).toContainText('Did not finish: timed out');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(3);
		update(server, 'cancelled', {}, 3);
		await expect(row(page, 's3')).toContainText('Cancelled');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(4);
		await expect(row(page, 's4')).toContainText('Waiting');
		await page.locator('[data-stack-open="s1"]').click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(5);
		update(server, 'running', { progress: 0.4 }, 5);
		push(server, { type: 'workbench_update', generation_id: runGeneration(5), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(row(page, 's5')).toContainText('Generating 40%');
		await page.locator('[data-options-collapse]').click();
		await screenshot(page, JOURNEY, '09c-row-states-dark-1440');
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
		await screenshot(page, JOURNEY, '09b-save-menu-dark-1440');
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

	test('the Settings button is present and says where the settings will open', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await rail(page, 'edit_region').click();
		const settings = prompt(page).locator('[data-prompt-settings]');
		await expect(settings).toBeDisabled();
		await settings.locator('xpath=..').hover();
		await expect(page.getByText('Settings open here soon')).toBeVisible();
	});
});

test.describe('Edit a region on the phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the prompt sheet, the picker sheet and the run', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object')], true);
		await openHarbor(page);
		await page.locator('[data-tool-rail] button[data-tool="edit_region"]').click();
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

	test('Remove an object on the phone', async ({ page }) => {
		await begin(page, [taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
		await openHarbor(page);
		await page.locator('[data-tool-rail] button[data-tool="remove_object"]').click();
		await expect(page.locator('[data-editor-layer]')).toBeVisible();
		await expect(prompt(page).locator('[data-prompt-status="warn"]')).toBeVisible();
		await screenshot(page, JOURNEY, '07-remove-dark-390');
	});
});
