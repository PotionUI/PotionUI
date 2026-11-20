import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, closeEditorMenu, editorMenu, editorMenuItem, expectNoBoxOverlap, loginAsOwner, openEditorMenu, scopedStorageKey, screenshot, selectRailTool, railButton } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-menu-bar';
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

const qwen = (over: Json = {}): Json => ({ preset_id: 'qwen21', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, owned: OWNED, ...over });
const flux = (over: Json = {}): Json => ({ preset_id: 'flux-fill', preset_name: 'Flux Fill', mode: 'editor:fill', from_mode: 'inpaint', mode_label: 'Inpaint', label: null, short_description: null, order: 0, variant: null, region_strategy: 'crop', via: null, qualifies: true, reason: null, owned: OWNED, ...over });

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
		ok(route, options.sessions ? [{ id: 'sess-1', preset_id: 'qwen21', name: 'Soft portrait', data: { 'editor:edit': { formData: { steps: 20 } } }, created_at: new Date(Date.now() - 9 * 86400000).toISOString(), updated_at: new Date(Date.now() - 2 * 86400000).toISOString(), pinned: false, version: 3, kind: 'editor' }] : [])
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

const sessionDefault: Json = { preset_id: 'qwen21', mode: 'editor:edit', setup: { kind: 'session', id: 'sess-1' }, health: { ok: true, reason: null, name: null, mode_label: null } };

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
		await page.getByTestId('phone-tool-tab').locator('[data-start-source="history"]').click();
		await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
		await expect(page.locator('[data-editor-frame="phone"]')).toBeVisible();
		return;
	}
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
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
}

async function shape(page: Page, aspect: string) {
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	const scope = phone ? page.getByTestId('phone-tool-tab').locator('[data-options-panel]') : page.locator('[data-options-panel]');
	await scope.locator('[data-crop-aspects] button', { hasText: aspect }).click();
	await page.locator('[data-options-apply]').click();
}

async function type(page: Page, text: string) {
	await prompt(page).locator('[data-prompt-field]').fill(text);
}

async function generate(page: Page) {
	await prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button').click();
}

async function prepare(page: Page, taskTool = true): Promise<Server> {
	const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] }), taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
	await openHarbor(page);
	await shape(page, '1:1');
	await expect(page.locator('[data-edit-stack] [data-stack-row]')).toHaveCount(2);
	if (taskTool) {
		await selectRailTool(page, 'edit_region');
		await expect(prompt(page).locator('[data-testid="session-trigger"]')).toHaveText(/No saved session/);
	}
	return server;
}

const tab = (page: Page, id: string) => page.locator(`[data-editor-menubar] [data-menu-tab="${id}"]`);
const menuRowById = (page: Page, id: string) => page.locator(`[data-menu-row][data-row-id="${id}"]`);
const statusLine = (page: Page) => page.locator('[data-canvas-status]');

async function setTheme(page: Page, theme: 'dark' | 'light') {
	await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
}

async function seedGenerationTab(page: Page) {
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const raw = (await list.json()).data;
	const presets = (Array.isArray(raw) ? raw : raw?.presets || []) as Array<{ id: string }>;
	for (const preset of presets) {
		const res = await page.request.get(`/api/presets/${preset.id}/modes`, { headers });
		const body = await res.json();
		const mode = body?.data?.default_mode || body?.data?.modes?.[0]?.name;
		if (mode) return { id: preset.id, mode };
	}
	return null;
}

test.describe('the editor menu bar on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the bar at rest: a title block as wide as the rail and panel, the four menus, history, Save and only the autosave state on the right', async ({ page }) => {
		await prepare(page);
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		const title = page.locator('[data-title-block]');
		await expect(title.locator('[data-title-name]')).toHaveText('Edit a region');
		const [titleBox, railBox, panelBox] = await Promise.all([title.boundingBox(), page.locator('[data-tool-rail]').boundingBox(), page.locator('[data-frame-panel]').boundingBox()]);
		expect(titleBox!.width).toBeCloseTo(railBox!.width + panelBox!.width, 0);
		expect(titleBox!.x).toBeCloseTo(railBox!.x, 0);
		await expect(page.locator('[data-editor-menubar] [data-menu-tab]')).toHaveText(['File', 'Edit', 'View', 'Tools']);
		await expect(page.locator('[data-editor-menubar]')).toHaveAttribute('role', 'menubar');
		await expect(page.locator('[data-frame-undo]')).toBeEnabled();
		await expect(page.locator('[data-frame-redo]')).toBeDisabled();
		await expect(page.locator('[data-frame-save]')).toBeVisible();
		await expect(page.locator('[data-save-state-slot] [data-edit-save-state]')).toHaveText('Saved');
		await expect(page.locator('[data-save-state-slot] > *')).toHaveCount(1);
		const [menuBox, stateBox, undoBox, redoBox, saveBox] = await Promise.all([
			page.locator('[data-editor-menubar]').boundingBox(),
			page.locator('[data-save-state-slot]').boundingBox(),
			page.locator('[data-frame-undo]').boundingBox(),
			page.locator('[data-frame-redo]').boundingBox(),
			page.locator('[data-frame-save-split]').boundingBox()
		]);
		expect(stateBox!.x).toBeGreaterThan(menuBox!.x + menuBox!.width);
		expect(undoBox!.x).toBeGreaterThan(stateBox!.x + stateBox!.width - 1);
		expect(redoBox!.x).toBeGreaterThan(undoBox!.x);
		expect(saveBox!.x).toBeGreaterThan(redoBox!.x);
		expect(saveBox!.x + saveBox!.width).toBeGreaterThan(1440 - 40);
		await expect(page.locator('[data-options-panel] h2')).toHaveCount(0);
		await expect(page.locator('[data-options-collapse]')).toHaveCount(0);
		await expect(page.locator('[data-editor-toolbar]')).not.toContainText('480×480');
		await expect(statusLine(page)).toHaveText(/^480×480 · Marked \d+×\d+$/);
		await expect(statusLine(page)).toHaveClass(/font-mono/);
		await screenshot(page, JOURNEY, '01-bar-rest-dark-1440');
		await setTheme(page, 'light');
		await screenshot(page, JOURNEY, '01-bar-rest-light-1440');
		await setTheme(page, 'dark');
	});

	test('File opens under its label, lists the sources and recent edits, saves as new, and shows why Replace original is off', async ({ page }) => {
		const server = await prepare(page);
		await openEditorMenu(page, 'File');
		const open = page.locator('[data-editor-menu="file"]');
		await expect(tab(page, 'file')).toHaveAttribute('aria-expanded', 'true');
		const menuBox = await open.boundingBox();
		const labelBox = await tab(page, 'file').boundingBox();
		expect(menuBox!.y).toBeCloseTo(labelBox!.y + labelBox!.height, 0);
		expect(menuBox!.x).toBeCloseTo(labelBox!.x, 0);
		await expect(open.locator('[data-menu-row] .truncate')).toHaveText(['Replace picture', 'Recent edits', 'Save as new', 'Replace original', 'Download', 'Copy image', 'Close']);
		await menuRowById(page, 'file.replace').hover();
		await expect(page.locator('[data-editor-submenu="file.replace"] [data-menu-row]')).toHaveCount(4);
		await expect(page.locator('[data-editor-submenu="file.replace"] [data-recent-history]')).toBeVisible();
		await menuRowById(page, 'file.recent').hover();
		await expect(page.locator('[data-editor-submenu="file.recent"] [data-menu-row][data-row-id="recent-all"]')).toBeVisible();
		await screenshot(page, JOURNEY, '02-file-open-dark-1440');
		const replace = menuRowById(page, 'file.replace-original');
		await replace.hover();
		await expect(replace).toHaveAttribute('aria-disabled', 'true');
		await expect(page.getByRole('tooltip')).toContainText('Files from History stay as generated');
		await screenshot(page, JOURNEY, '07-disabled-reason-dark-1440');
		await replace.click({ force: true });
		await expect(open).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(open).toHaveCount(0);
		await expect(tab(page, 'file')).toBeFocused();
		await editorMenu(page, 'File', 'Save as new');
		await expect.poll(() => server.mediaRuns.length).toBe(1);
		await expect(page.locator('[data-editor-menu]')).toHaveCount(0);
	});

	test('Send to lists the generation tabs that take a picture and sends from the menu', async ({ page }) => {
		await loginAsOwner(page);
		const found = await seedGenerationTab(page);
		test.skip(!found, 'no preset with a mode on this instance');
		const key = await scopedStorageKey(page, 'potionui_tabs_state');
		await page.addInitScript(
			({ key, preset }) => {
				try {
					if (sessionStorage.getItem('menu-bar-seeded')) return;
					sessionStorage.setItem('menu-bar-seeded', '1');
					const tab = { id: 'portrait-study', name: 'Portrait study', selectedPreset: preset.id, selectedMode: preset.mode, selectedVariant: null, selectedSessionId: null, activeGenerationId: null, prompt: '', negativePrompt: '', promptSegments: [], negativePromptSegments: [], formData: {} };
					localStorage.setItem(key, JSON.stringify({ tabs: [tab], activeTabId: tab.id }));
				} catch {
					return;
				}
			},
			{ key, preset: found! }
		);
		await prepare(page, false);
		await openEditorMenu(page, 'File');
		await menuRowById(page, 'file.send').hover();
		const sub = page.locator('[data-editor-submenu="file.send"]');
		await expect(sub).toBeVisible();
		await expect(sub.locator('[data-menu-row]').first()).toContainText('Portrait study');
		await screenshot(page, JOURNEY, '06-send-to-dark-1440');
		await sub.locator('[data-menu-row]').first().click();
		await expect(page.locator('[data-editor-menu]')).toHaveCount(0);
	});

	test('Edit names the step, hides it, clears the marked area, undoes and offers Apply for the tool', async ({ page }) => {
		await prepare(page);
		await markArea(page);
		await openEditorMenu(page, 'Edit');
		await expect(menuRowById(page, 'edit.undo')).toContainText('Undo');
		await expect(menuRowById(page, 'edit.redo')).toHaveAttribute('aria-disabled', 'true');
		await expect(menuRowById(page, 'edit.reset')).toContainText('Clear area');
		await expect(menuRowById(page, 'edit.apply')).toHaveCount(0);
		await expect(menuRowById(page, 'edit.remove-all')).toHaveClass(/text-danger/);
		await screenshot(page, JOURNEY, '03-edit-open-dark-1440');
		await page.keyboard.press('Escape');
		await editorMenu(page, 'Edit', 'Clear area');
		await expect(page.locator('[data-mask-area]')).toBeHidden();
		await editorMenu(page, 'Edit', 'Hide step');
		await expect(page.locator('[data-edit-stack] [data-stack-row][data-hidden]')).toHaveCount(1);
		await expect(await editorMenuItem(page, 'Edit', 'Show step')).toBeVisible();
		await page.keyboard.press('Escape');
		await openEditorMenu(page, 'Edit');
		await expect(menuRowById(page, 'edit.undo')).toContainText('Undo marked area');
		await menuRowById(page, 'edit.undo').click();
		await expect(page.locator('[data-mask-area]')).toBeVisible();
		await openEditorMenu(page, 'Edit');
		await menuRowById(page, 'edit.undo').click();
		await expect(page.locator('[data-mask-area]')).toBeHidden();
		await openEditorMenu(page, 'Edit');
		await menuRowById(page, 'edit.undo').click();
		await expect(page.locator('[data-edit-stack] [data-stack-row][data-status="undone"]')).toHaveCount(1);
		await expect(page.locator('[data-frame-redo]')).toBeEnabled();
	});

	test('Remove all edits asks first, Keep them changes nothing, confirming removes them and one Undo brings them back', async ({ page }) => {
		await prepare(page, false);
		const rows = page.locator('[data-edit-stack] [data-stack-row]');
		await editorMenu(page, 'Edit', 'Remove all edits');
		await expect(page.getByText('Remove all edits?')).toBeVisible();
		await expect(page.getByText('Undo brings it back while this editor stays open.')).toBeVisible();
		await page.getByRole('button', { name: 'Keep them' }).click();
		await expect(rows).toHaveCount(2);
		await editorMenu(page, 'Edit', 'Remove all edits');
		await page.getByRole('button', { name: 'Remove all edits', exact: true }).click();
		await expect(rows).toHaveCount(1);
		await expect(page.locator('[data-frame-undo]')).toBeEnabled();
		await page.locator('[data-frame-undo]').click();
		await expect(rows).toHaveCount(2);
	});

	test('View toggles zoom, fit and the Edits list', async ({ page }) => {
		await prepare(page);
		await openEditorMenu(page, 'View');
		await expect(menuRowById(page, 'view.fit')).toHaveAttribute('aria-checked', 'true');
		await expect(menuRowById(page, 'view.compare')).toHaveAttribute('aria-disabled', 'true');
		await expect(menuRowById(page, 'view.compare').locator('span').filter({ hasText: '\\' })).toHaveCount(1);
		await screenshot(page, JOURNEY, '04-view-open-dark-1440');
		await page.keyboard.press('Escape');
		const zoom = page.locator('[data-canvas-zoom-value]');
		const before = Number.parseInt((await zoom.textContent()) ?? '0', 10);
		await editorMenu(page, 'View', 'Zoom in');
		await expect.poll(async () => Number.parseInt((await zoom.textContent()) ?? '0', 10)).toBeGreaterThan(before);
		await expect(await editorMenuItem(page, 'View', 'Fit')).toHaveAttribute('aria-checked', 'false');
		await page.keyboard.press('Escape');
		await editorMenu(page, 'View', 'Fit');
		await expect(await editorMenuItem(page, 'View', 'Fit')).toHaveAttribute('aria-checked', 'true');
		await page.keyboard.press('Escape');
		await editorMenu(page, 'View', 'Hide edits');
		await expect(page.locator('[data-edit-stack]')).toHaveAttribute('data-folded', 'true');
		await expect(await editorMenuItem(page, 'View', 'Show edits')).toBeVisible();
		await page.keyboard.press('Escape');
		await editorMenu(page, 'View', 'Show edits');
		await expect(page.locator('[data-edit-stack]')).not.toHaveAttribute('data-folded', 'true');
	});

	test('Tools mirrors the rail groups as submenus with their entries, marks the current one and selects like the rail', async ({ page }) => {
		await prepare(page);
		await openEditorMenu(page, 'Tools');
		const menu = page.locator('[data-editor-menu="tools"]');
		const rows = await menu.locator('[data-menu-row]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-row-id')));
		expect(rows).toEqual(['toolgroup:generate', 'toolgroup:shape', 'toolgroup:paint', 'toolgroup:select', 'toolgroup:look', 'toolgroup:fx', 'tool:effects', 'toolgroup:tools']);
		await expect(menu.locator('[role="separator"]').first()).toBeVisible();
		await menuRowById(page, 'toolgroup:generate').hover();
		await expect(page.locator('[data-editor-submenu="toolgroup:generate"]')).toBeVisible();
		await expect(menuRowById(page, 'tool:edit_region')).toHaveAttribute('aria-checked', 'true');
		await expect(menuRowById(page, 'tool:edit_region')).toHaveClass(/text-signal/);
		await screenshot(page, JOURNEY, '05-tools-open-dark-1440');
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		await editorMenu(page, 'Tools', 'Transform', 'Resize');
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'resize');
		await expect(page.locator('[data-title-name]')).toHaveText('Resize');
		await expect(statusLine(page)).toContainText('Output');
		await expect(railButton(page, 'resize')).toHaveAttribute('aria-pressed', 'true');
	});

	test('Apply and Reset live in the panel footer, Enter still applies, and Edit offers Apply with its key', async ({ page }) => {
		await prepare(page, false);
		await selectRailTool(page, 'crop');
		const footer = page.locator('[data-options-panel] [data-options-footer]');
		await expect(footer.locator('[data-options-apply]')).toContainText('Apply crop');
		await expect(footer.locator('[data-options-reset]')).toBeVisible();
		await expect(statusLine(page)).toContainText('Output');
		await openEditorMenu(page, 'Edit');
		const apply = menuRowById(page, 'edit.apply');
		await expect(apply).toContainText('Apply crop');
		await expect(apply).toContainText('Enter');
		await apply.hover();
		await screenshot(page, JOURNEY, '10-crop-apply-dark-1440');
		await page.keyboard.press('Escape');
		await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '16:9' }).click();
		await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-edit-stack] [data-stack-row]')).toHaveCount(3);
	});

	test('while a step is generating and when its results are in, the status line stays clear of the prompt, the run chip and the result strip', async ({ page }) => {
		const server = await prepare(page);
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'running', { progress: 0.4 });
		push(server, { type: 'workbench_update', generation_id: runGeneration(server.runCount), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(page.locator('[data-floating-prompt] [role="progressbar"]')).toHaveAttribute('aria-valuenow', '40');
		await expect(statusLine(page)).toContainText('Marked');
		await expectNoBoxOverlap([
			{ name: 'status', locator: statusLine(page) },
			{ name: 'zoom', locator: page.locator('[data-canvas-zoom]') },
			{ name: 'hint', locator: page.locator('[data-canvas-hint]') }
		]);
		await page.locator('[data-frame-save-menu]').click();
		await expect(page.locator('[data-save-row="save-new"]')).toBeVisible();
		await screenshot(page, JOURNEY, '08-generating-dark-1440');
		await page.keyboard.press('Escape');
		update(server, 'done', { candidates: candidates(2) });
		await expect(page.locator('[data-candidate-strip]')).toBeVisible();
		await expect(statusLine(page)).toBeVisible();
		await expectNoBoxOverlap([
			{ name: 'status', locator: statusLine(page) },
			{ name: 'strip', locator: page.locator('[data-candidate-strip]') },
			{ name: 'zoom', locator: page.locator('[data-canvas-zoom]') },
			{ name: 'hint', locator: page.locator('[data-canvas-hint]') }
		]);
		await screenshot(page, JOURNEY, '08b-done-dark-1440');
	});

	test('the keyboard opens a menu, moves, opens a submenu, and Escape closes the menu before the prompt', async ({ page }) => {
		await prepare(page);
		await tab(page, 'file').focus();
		await page.keyboard.press('ArrowDown');
		await expect(menuRowById(page, 'file.replace')).toBeFocused();
		await page.keyboard.press('ArrowDown');
		await expect(menuRowById(page, 'file.recent')).toBeFocused();
		await page.keyboard.press('ArrowRight');
		await expect(page.locator('[data-editor-submenu="file.recent"]')).toBeVisible();
		await page.keyboard.press('ArrowLeft');
		await expect(page.locator('[data-editor-submenu]')).toHaveCount(0);
		await expect(menuRowById(page, 'file.recent')).toBeFocused();
		await page.keyboard.press('ArrowUp');
		await page.keyboard.press('ArrowUp');
		await expect(menuRowById(page, 'file.close')).toBeFocused();
		await page.keyboard.press('Escape');
		await expect(page.locator('[data-editor-menu]')).toHaveCount(0);
		await expect(tab(page, 'file')).toBeFocused();
		await expect(prompt(page)).toBeVisible();
		await tab(page, 'edit').click();
		await tab(page, 'view').hover();
		await expect(page.locator('[data-editor-menu="view"]')).toBeVisible();
		await page.locator('[data-title-block]').click();
		await expect(page.locator('[data-editor-menu]')).toHaveCount(0);
	});

	test('a menu is not clipped by the canvas or the panel', async ({ page }) => {
		await prepare(page);
		await openEditorMenu(page, 'Tools');
		const box = await page.locator('[data-editor-menu="tools"]').boundingBox();
		const inside = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('[data-editor-menu]') !== null, { x: box!.x + 40, y: box!.y + box!.height - 10 });
		expect(inside).toBe(true);
	});
});

test.describe('the editor menu bar at 1024', () => {
	test.use({ viewport: { width: 1024, height: 768 } });

	test('Save stays visible, the autosave state collapses to its dot and the marked part leaves the status line', async ({ page }) => {
		await prepare(page, false);
		await selectRailTool(page, 'crop');
		const save = page.locator('[data-frame-save]');
		await expect(save).toBeVisible();
		const saveBox = await save.boundingBox();
		expect(saveBox!.x + saveBox!.width).toBeLessThanOrEqual(1024);
		const slot = page.locator('[data-save-state-slot]');
		await expect(slot).toBeVisible();
		const slotBox = await slot.boundingBox();
		expect(slotBox!.x + slotBox!.width).toBeLessThanOrEqual(1024);
		const indicator = await page.locator('[data-edit-save-state]').boundingBox();
		expect(indicator!.width).toBeLessThan(20);
		await expect(statusLine(page).locator('[data-status-size]')).toBeVisible();
		await expect(statusLine(page).locator('[data-status-extra]')).toBeHidden();
		await screenshot(page, JOURNEY, '01b-bar-rest-dark-1024');
	});
});

test.describe('the editor menu on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('one Menu button opens a bottom sheet of accordions, and the size sits in the edits row', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await expect(page.locator('[data-frame-edits]')).toContainText('1 edit');
		await expect(page.locator('[data-frame-edits] [data-frame-size]')).toHaveText('480×480');
		const button = page.locator('[data-phone-menu]');
		await expect(button).toBeVisible();
		await expect(page.locator('[data-editor-menubar]')).toHaveCount(0);
		await button.click();
		const sheet = page.getByRole('dialog', { name: 'Menu' });
		await expect(sheet.locator('[data-sheet-section] > button')).toHaveText(['File', 'Edit', 'View', 'Tools']);
		await screenshot(page, JOURNEY, '09-phone-menu-dark-390');
		await sheet.locator('[data-sheet-section="file"] > button').click();
		const rows = sheet.locator('[data-sheet-section="file"] [data-sheet-row]');
		expect(await rows.count()).toBeGreaterThan(5);
		for (const row of await rows.all()) expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
		await expect(sheet.locator('[data-sheet-section="file"]')).not.toContainText('Ctrl');
		await screenshot(page, JOURNEY, '02-file-open-dark-390');
		await sheet.locator('[data-sheet-row="file.replace"]').click();
		await expect(sheet.locator('[data-sheet-child="library"]')).toBeVisible();
		await screenshot(page, JOURNEY, '06-send-to-dark-390');
		await sheet.locator('[data-sheet-section="edit"] > button').click();
		await expect(sheet.locator('[data-sheet-row="edit.redo"]')).toContainText('Nothing to redo');
		await screenshot(page, JOURNEY, '03-edit-open-dark-390');
		await sheet.locator('[data-sheet-section="tools"] > button').click();
		await sheet.locator('[data-sheet-row="toolgroup:shape"]').click();
		await expect(sheet.locator('[data-sheet-child="tool:crop"]')).toHaveAttribute('aria-checked', 'true');
		await screenshot(page, JOURNEY, '05-tools-open-dark-390');
		await sheet.locator('[data-sheet-child="tool:resize"]').click();
		await expect(page.getByRole('dialog', { name: 'Menu' })).toHaveCount(0);
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'resize');
	});
});
