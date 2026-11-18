import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { clearEdits, closeEditorMenu, editorMenuItem, loginAsOwner, screenshot, selectRailTool, shotPath } from './helpers';
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
				{ name: 'strength', type: 'number', title: 'Strength', default: 0.85 },
				{ name: 'steps', type: 'slider', title: 'Steps', default: 28 },
				{ name: 'quantity', type: 'stepper', title: 'Quantity', default: 1, configuration: { min: 1, max: 4, step: 1 } },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

const FORMULA_DECLARATION = { groups: [{ id: 'look', label: 'Look', fields: ['strength', 'steps', 'source_image'] }] };
const FORMULA: Json = { id: 'formula-1', preset_id: 'qwen21', mode: 'editor:edit', variant: null, name: 'Crisp and quick', note: null, groups: [{ id: 'look', label: 'Look', fields: ['strength', 'steps', 'source_image'] }], values: { strength: 0.9, steps: 8, source_image: 'x.png' }, signatures: {}, preset_version: '1', created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z' };
const FORMULA_PLAN: Json = {
	changes: [
		{ name: 'strength', label: 'Strength', group_id: 'look', group_label: 'Look', type: 'number', advanced: false, old: 0.85, new: 0.9 },
		{ name: 'steps', label: 'Steps', group_id: 'look', group_label: 'Look', type: 'slider', advanced: false, old: 28, new: 8 }
	],
	same: [],
	skips: [{ name: 'source_image', label: 'Picture', group_id: 'look', code: 'field_removed', reason: 'This setting is not in the form any more.' }]
};

const qwen = (over: Json = {}): Json => ({ preset_id: 'qwen21', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, quantity_field: 'quantity', owned: ['source_image'], ...over });
const flux = (over: Json = {}): Json => ({ preset_id: 'flux-fill', preset_name: 'Flux Fill', mode: 'editor:fill', from_mode: 'inpaint', mode_label: 'Inpaint', mode_icon: 'mode-restyle', label: null, short_description: null, order: 0, variant: null, region_strategy: 'crop', via: null, qualifies: true, reason: null, quantity_field: 'quantity', owned: ['source_image'], ...over });
const fluxWide = (): Json => flux({ mode: 'editor:fill2', from_mode: 'inpaint', mode_label: 'Wide fill', mode_icon: 'mode-extend', short_description: 'Fills a wider area', order: 1 });

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
	defaultClears: number;
	formModes: Array<string | null>;
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
	plans: Json[];
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

async function mockAll(page: Page, tasks: Json[], options: { sessions?: boolean; sessionName?: string; formulas?: boolean } = {}): Promise<Server> {
	const server: Server = { tasks, runs: [], runStatus: {}, live: {}, runGets: [], cancels: [], defaults: [], defaultClears: 0, formModes: [], startFails: null, saves: [], sessionPuts: [], sessionPosts: [], sessionFails: null, mediaRuns: [], sockets: [], runCount: 0, holdSubscribe: false, held: [], plans: [] };
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
	await page.route('**/api/presets/*/form*', (route) => {
		server.formModes.push(new URL(route.request().url()).searchParams.get('mode'));
		return ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA, ...(options.formulas ? { formulas: FORMULA_DECLARATION } : {}) });
	});
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
		if (route.request().method() === 'DELETE') server.defaultClears += 1;
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
	await page.route(/\/api\/sessions\/preset\/[^/?]+(\?.*)?$/, (route) => {
		const preset = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
		const stamp = (days: number) => new Date(Date.now() - days * 86400000).toISOString();
		const list: Json[] = [];
		if (options.sessions && preset === 'qwen21') list.push({ id: 'sess-1', preset_id: 'qwen21', name: options.sessionName ?? 'Soft portrait', data: { 'editor:edit': { formData: { steps: 20 } } }, created_at: stamp(9), updated_at: stamp(2), pinned: false, version: 3, kind: 'editor' });
		if (options.sessions && preset === 'flux-fill') list.push({ id: 'sess-flux', preset_id: 'flux-fill', name: 'Wide and soft', data: { 'editor:fill2': { formData: { steps: 11 } } }, created_at: stamp(5), updated_at: stamp(1), pinned: false, version: 1, kind: 'editor' });
		return ok(route, list);
	});
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
	await page.route(/\/api\/formulas(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, options.formulas ? [FORMULA] : []) : route.fallback()));
	await page.route(/\/api\/formulas\/[^/]+\/plan$/, async (route) => {
		server.plans.push(route.request().postDataJSON() as Json);
		await ok(route, FORMULA_PLAN);
	});
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
async function openPhoneTask(page: Page, id: string) {
	await page.locator('[data-tool-rail] [data-group-button="generate"]').click();
	await page.locator(`[data-rail-sheet="generate"] [data-sheet-item="${id}"]`).click();
}

const LONG_SESSION = 'Product shots, soft light v3';
const LONG_PRESET = 'MiniMax-H3-Image';

async function geometry(page: Page) {
	return page.evaluate(() => {
		const box = (element: Element | null) => {
			if (!element) return null;
			const rect = element.getBoundingClientRect();
			return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
		};
		const dock = document.querySelector('[data-frame-dock]')!;
		const strip = dock.querySelector<HTMLElement>('[data-dock-row="strip"]')!;
		const end = dock.querySelector('[data-editor-pickers-end]');
		return {
			viewport: { width: window.innerWidth, height: window.innerHeight },
			scroll: { page: document.documentElement.scrollHeight, body: document.body.scrollHeight },
			dock: box(dock),
			strip: box(strip),
			stripOverflow: getComputedStyle(strip).overflow,
			stripScroll: { client: strip.clientWidth, scroll: strip.scrollWidth },
			column: box(dock.querySelector('[data-dock-row="strip"]')!.parentElement),
			end: box(end),
			labels: Array.from(strip.querySelectorAll('.ctx-label')).map((label) => ({ text: label.textContent?.trim() ?? '', ...box(label)! })),
			preset: box(strip.querySelector('[data-testid="preset-picker-trigger"]')),
			mode: box(strip.querySelector('[data-testid="preset-header-mode"] button')),
			session: box(strip.querySelector('[data-testid="session-trigger"]')),
			save: box(strip.querySelector('[data-testid="session-save"]')),
			reload: box(strip.querySelector('[data-testid="session-reload"]')),
			defaultButton: box(strip.querySelector('[data-testid="default-button"]')),
			settings: box(strip.querySelector('[data-prompt-settings]')),
			formulas: box(strip.querySelector('[data-testid="formulas-button"]')),
			cuts: Array.from(strip.querySelectorAll('[data-testid="context-separator"]')).map((cut) => box(cut)!),
			field: box(dock.querySelector('.ai-ring')),
			fieldInner: box(dock.querySelector('[data-prompt-field]')),
			mark: box(dock.querySelector('[data-dock-mark] button')),
			markCell: box(dock.querySelector('[data-dock-mark]')),
			presetText: strip.querySelector('[data-preset-value]')?.textContent ?? ''
		};
	});
}

async function ringAudit(page: Page) {
	return page.evaluate(() => {
		const css = (name: string) => `rgb(${getComputedStyle(document.documentElement).getPropertyValue(name).trim().split(/\s+/).join(', ')})`;
		const warm = css('--warning');
		const aiColours = [css('--ai-1'), css('--ai-2'), css('--ai-3')];
		const found: string[] = [];
		const dock = document.querySelector('[data-frame-dock]')!;
		const roots = [dock, ...Array.from(document.querySelectorAll('[data-editor-frame], [data-frame-column]'))];
		const seen = new Set<Element>();
		for (const root of roots) {
			for (const element of [root, ...Array.from(root.querySelectorAll('*'))]) {
				if (seen.has(element)) continue;
				seen.add(element);
				const style = getComputedStyle(element);
				const inDock = dock.contains(element);
				const label = `${element.tagName.toLowerCase()}.${String(element.getAttribute('class') ?? '').split(/\s+/).slice(0, 4).join('.')}`;
				const ring = element.classList.contains('ai-ring') || element.classList.contains('ai-ring--on');
				if (ring && inDock && !element.querySelector('[data-prompt-field]')) found.push(`ring on ${label}`);
				const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0 && style.outlineColor !== 'rgba(0, 0, 0, 0)';
				const shadow = style.boxShadow !== 'none';
				if (!ring && inDock && (outline || shadow) && aiColours.some((colour) => style.boxShadow.includes(colour) || style.outlineColor === colour)) found.push(`ring colour on ${label}`);
				if (style.boxShadow.includes(warm) || (outline && style.outlineColor === warm)) found.push(`warning colour on ${label}`);
				if (inDock && !ring) {
					for (const side of ['Top', 'Right', 'Bottom', 'Left'] as const) {
						if (parseFloat(style[`border${side}Width`]) > 0 && style[`border${side}Color`] === warm) found.push(`warning border-${side.toLowerCase()} on ${label}`);
					}
				}
			}
		}
		const rings = Array.from(dock.querySelectorAll('.ai-ring, .ai-ring--on')).map((el) => (el.querySelector('[data-prompt-field]') ? 'field' : 'other'));
		return { found, rings };
	});
}

function checkStrip(g: Awaited<ReturnType<typeof geometry>>, mode: 'rest' | 'temp' | 'wrapped' = 'rest') {
	const stripBox = g.strip!;
	const dockBox = g.dock!;
	const wrapped = mode === 'wrapped';
	expect(g.stripOverflow).toBe('hidden');
	expect(g.stripScroll.scroll).toBeLessThanOrEqual(g.stripScroll.client + 1);
	const labelBottoms = new Set(g.labels.map((label) => Math.round(label.bottom)));
	expect(labelBottoms.size, `label baselines ${JSON.stringify(g.labels.map((l) => [l.text, l.bottom]))}`).toBe(1);
	const pickerBottoms = [g.preset, g.session].filter(Boolean).map((box) => Math.round(box!.bottom));
	expect(Math.max(...pickerBottoms) - Math.min(...pickerBottoms), `picker bottoms ${JSON.stringify(pickerBottoms)}`).toBeLessThanOrEqual(2);
	const rowCentre = g.end!.y + g.end!.height / 2;
	if (!wrapped) expect(Math.abs(rowCentre - (stripBox.y + stripBox.height / 2)), `row centre ${rowCentre} strip ${stripBox.y + stripBox.height / 2}`).toBeLessThanOrEqual(1);
	for (const control of [g.settings, g.formulas].filter(Boolean)) {
		const centre = control!.y + control!.height / 2;
		expect(Math.abs(centre - rowCentre), `end control centre ${centre} row centre ${rowCentre}`).toBeLessThanOrEqual(1);
	}
	for (const control of wrapped ? [] : [g.save, g.reload, g.defaultButton].filter(Boolean)) {
		const centre = control!.y + control!.height / 2;
		expect(Math.abs(centre - rowCentre), `session button centre ${centre} row centre ${rowCentre}`).toBeLessThanOrEqual(1);
	}
	expect(g.defaultButton, 'default button').not.toBeNull();
	expect(Math.abs(g.defaultButton!.height - g.save!.height), 'default button height matches Save').toBeLessThanOrEqual(0.5);
	if (g.reload) expect(Math.abs(g.defaultButton!.x - g.reload.right - (g.reload.x - g.save!.right)), 'Default gap matches Save to Reload').toBeLessThanOrEqual(1);
	for (const cut of g.cuts) {
		expect(Math.abs(cut.y - stripBox.y)).toBeLessThanOrEqual(1);
		expect(Math.abs(cut.height - 48)).toBeLessThanOrEqual(1);
	}
	expect(g.end!.right).toBeLessThanOrEqual(g.column!.right + 0.5);
	expect(g.settings!.right).toBeLessThanOrEqual(g.column!.right - 11);
	expect(g.settings!.right).toBeGreaterThanOrEqual(g.column!.right - 13);
	if (!wrapped) expect(g.end!.x).toBeGreaterThan(g.session!.right);
	if (mode === 'rest') expect(Math.abs(g.field!.y - (stripBox.bottom + 12))).toBeLessThanOrEqual(2);
	expect(Math.abs(dockBox.bottom - g.field!.bottom - 12)).toBeLessThanOrEqual(1.5);
	expect(dockBox.bottom).toBeLessThanOrEqual(g.viewport.height);
	expect(g.scroll.page).toBeLessThanOrEqual(g.viewport.height);
	expect(g.mark!.y).toBeGreaterThanOrEqual(dockBox.y);
	expect(g.mark!.bottom).toBeLessThanOrEqual(dockBox.bottom);
}

async function setResults(page: Page, count: number) {
	const view = page.locator('[data-frame-panel] [data-settings-view="desktop"]');
	await prompt(page).locator('[data-prompt-settings]').click();
	const card = view.getByText('Quantity', { exact: true }).first().locator('xpath=ancestor::*[contains(@class, "field-card")][1]');
	const input = card.locator('input[type="number"]').first();
	await input.fill(String(count));
	await input.blur();
	await prompt(page).locator('[data-prompt-settings]').click();
	await expect(view).toHaveCount(0);
}

const options = (page: Page) => page.locator('[data-options-panel]');
const prompt = (page: Page) => page.locator('[data-floating-prompt]');
const presetTrigger = (page: Page) => prompt(page).locator('[data-testid="preset-picker-trigger"]');
const sessionTrigger = (page: Page) => prompt(page).locator('[data-testid="session-trigger"], button[aria-label="Session"]').first();
const modeTrigger = (page: Page) => prompt(page).locator('[data-testid="preset-header-mode"] button').first();
const presetDropdown = (page: Page) => page.locator('[role="dialog"][aria-label="Choose preset"]');
const defaultStar = (page: Page) => prompt(page).locator('[data-testid="default-button"]');
const starTip = (page: Page) => page.locator('[role="tooltip"]');

async function closePresetDropdown(page: Page) {
	await page.keyboard.press('Escape');
	await expect(presetDropdown(page)).toHaveCount(0);
}
const row = (page: Page, id = 's1') => page.locator(`[data-edit-stack] [data-stack-row="${id}"]`);

async function begin(page: Page, tasks: Json[], sessions = false, sessionName?: string, formulas = false): Promise<Server> {
	const server = await mockAll(page, tasks, { sessions, sessionName, formulas });
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

async function scaleTo(page: Page, percent: string) {
	await page.getByRole('button', { name: percent, exact: true }).click();
	await page.locator('[data-options-apply]').click();
}

async function pickPreset(page: Page, id: string) {
	await expect(page.locator('[role="dialog"][aria-label="Choose preset"]')).toBeVisible();
	await page.locator(`[data-preset-row="${id}"] button.row-main`).click();
	await expect(page.locator('[role="dialog"][aria-label="Choose preset"]')).toHaveCount(0);
}

async function type(page: Page, text: string) {
	await prompt(page).locator('[data-prompt-field]').fill(text);
}

async function generate(page: Page) {
	await prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button').click();
}

test.describe('Edit a region and Remove an object on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('first use, a lasso, a prompt, a run with two results, choosing the second, and Save', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object')], true);
		await openHarbor(page);
		await expect(rail(page, 'edit_region')).toBeVisible();
		await expect(rail(page, 'remove_object')).toBeVisible();
		await selectRailTool(page, 'edit_region');
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'edit_region');

		await expect(page.locator('[role="dialog"][aria-label="Choose preset"]')).toBeVisible();
		await expect(page.locator('[data-preset-row]')).toHaveCount(2);
		await expect(prompt(page).locator('[data-picker-hint]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-dock-row]')).toHaveCount(2);
		await expect(presetTrigger(page)).toHaveText('Choose a preset');
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeDisabled();
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toHaveAccessibleName('Choose a preset first');
		await screenshot(page, JOURNEY, '02-first-use-dark-1440');
		await pickPreset(page, 'qwen21');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await expect(prompt(page).locator('[data-picker-hint]')).toHaveCount(0);
		await expect(sessionTrigger(page)).toHaveText(/No saved session/);
		await sessionTrigger(page).click();
		await page.locator('[data-session-row="sess-1"]').click();
		await expect(sessionTrigger(page)).toHaveText(/Soft portrait/);
		await expect(defaultStar(page)).toHaveAttribute('aria-pressed', 'false');
		await page.mouse.move(5, 5);
		await page.locator('[data-frame-dock] [data-dock-row="strip"]').screenshot({ path: shotPath(JOURNEY, '60-default-button-off-dark-1440'), animations: 'disabled' });
		await defaultStar(page).hover();
		await expect(starTip(page)).toContainText('Use as my default for Edit a region');
		await screenshot(page, JOURNEY, '50-star-off-tooltip-dark-1440');
		await defaultStar(page).click();
		await expect.poll(() => server.defaults.length).toBe(1);
		expect(server.defaults[0]).toEqual({ preset_id: 'qwen21', mode: 'editor:edit', setup: { kind: 'session', id: 'sess-1' } });
		await expect(defaultStar(page)).toHaveAttribute('aria-pressed', 'true');
		await page.mouse.move(5, 5);
		await page.locator('[data-frame-dock] [data-dock-row="strip"]').screenshot({ path: shotPath(JOURNEY, '60-default-button-on-dark-1440'), animations: 'disabled' });
		await screenshot(page, JOURNEY, '50-star-on-dark-1440');
		await defaultStar(page).hover();
		await expect(starTip(page)).toContainText('Click to stop using it as your default');
		await screenshot(page, JOURNEY, '50-star-on-tooltip-dark-1440');
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, '12-pickers-dark-1440');

		await screenshot(page, JOURNEY, '08-whole-picture-dark-1440');
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		await setResults(page, 2);
		await screenshot(page, JOURNEY, '01-prompt-dark-1440');
		await generate(page);

		await expect.poll(() => server.runs.length).toBe(1);
		const body = server.runs[0];
		expect(body).toMatchObject({ prompt: 'a small red sailboat, one white sail, calm water', candidates: 2, edit: { id: EDIT_ID }, setup: { preset_id: 'qwen21', mode: 'editor:edit', form_data: { steps: 20 } } });
		expect(body.setup).not.toHaveProperty('variant');
		expect((body.region as Json).item_id).toBeTruthy();
		await expect(row(page)).toContainText('Waiting');
		await expect(prompt(page).locator('[role="progressbar"]')).toBeVisible();
		await expect(prompt(page).locator('[data-border-progress="loading"]')).toHaveCount(1);
		await expect(page.locator('[data-run-chip]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-prompt-cancel], [data-prompt-mark="running"] button')).toBeVisible();
		await screenshot(page, JOURNEY, '05b-waiting-dark-1440');

		update(server, 'running', { progress: 0.4 });
		push(server, { type: 'workbench_update', generation_id: runGeneration(server.runCount), pipe_id: 1, output_type: 'image', temporary: true, image: PREVIEW });
		await expect(prompt(page).locator('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '40');
		await expect(prompt(page).locator('[data-border-progress="running"]')).toHaveCount(1);
		await expect(page.locator('[data-run-chip]')).toHaveCount(0);
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
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await expect(sessionTrigger(page)).toHaveText(/No saved session/);
		await expect(page.locator('[data-preset-row]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-picker-hint]')).toHaveCount(0);
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
		await selectRailTool(page, 'remove_object');
		await expect(prompt(page).locator('[data-prompt-field]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-prompt-note]')).toHaveText('The marked object will be removed.');
		await expect(prompt(page).locator('[data-prompt-status]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-dock-row]')).toHaveCount(2);
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeDisabled();
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toHaveAccessibleName('Mark the object first');
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
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeDisabled();
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toHaveAccessibleName('Mark the object first');
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
		await selectRailTool(page, 'edit_region');
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
		await selectRailTool(page, 'edit_region');
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
		await selectRailTool(page, 'edit_region');
		await markArea(page);
		await type(page, 'red sailboat');
		await setResults(page, 3);
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(3) }, 1);
		await expect(row(page, 's2')).toContainText('1 of 3');
		await page.locator('[data-stack-open="s1"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
		await shape(page, '16:9');
		await expect(row(page, 's2')).toContainText('The picture changed');
		await selectRailTool(page, 'edit_region');
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
		await selectRailTool(page, 'edit_region');
		await markArea(page);
		await type(page, 'a lantern');
		await setResults(page, 2);
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

		await selectRailTool(page, 'edit_region');
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
		await selectRailTool(page, 'resize');
		await scaleTo(page, '75%');
		await expect(row(page, 's1')).toContainText('Resize');
		await selectRailTool(page, 'edit_region');
		await markArea(page);
		await type(page, 'a lantern');
		await setResults(page, 2);
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
		await expect(prompt(page).locator('[data-dock-row="temp"] [data-dock-stale]')).toContainText('The picture changed');
		await expect(prompt(page).locator('[data-dock-redo]')).toBeVisible();
		await expect(row(page, 's2').locator('[data-stack-action="redo"]')).toBeVisible();
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
		await page.locator('[data-stack-open="s1"]').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'resize');
		await row(page, 's2').hover();
		await expect(row(page, 's2').locator('[data-stack-action="redo"]')).toBeVisible();
		await page.locator('[data-stack-open="s2"]').click();
		await expect(prompt(page).locator('[data-dock-stale]')).toContainText('The picture changed');
		await prompt(page).locator('[data-dock-redo]').click();
		await expect.poll(() => server.runs.length).toBe(3);
	});

	test('after a crop beneath, Redo asks for the area again and Generate replaces the same step', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await shape(page, '1:1');
		await selectRailTool(page, 'edit_region');
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
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeDisabled();
		expect(server.runs).toHaveLength(1);
		await markArea(page);
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeEnabled();
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
		await selectRailTool(page, 'edit_region');
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await page.locator('[data-prompt-mark="running"] button').click();
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
		const broken = { preset_id: 'qwen21', mode: 'editor:edit', setup: { kind: 'session', id: 'gone', name: 'Soft portrait' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } };
		await begin(page, [taskEntry('edit_region', 'Edit a region', { default: broken })]);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(prompt(page).locator('[data-dock-row="temp"] [data-picker-notice]')).toHaveText('Your default setup "Soft portrait" was deleted. Pick another.');
		await expect(page.locator('[data-picker-notice]')).toHaveCount(1);
		await expect(presetTrigger(page)).toHaveText('Choose a preset');
		await expect(page.locator('[data-preset-row]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeDisabled();
		await screenshot(page, JOURNEY, '03-broken-default-dark-1440');
	});

	test('no model: the empty pickers', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [] })]);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(page.locator('[data-picker-empty]')).toContainText('No model can do this yet');
		await screenshot(page, JOURNEY, '11-picker-empty-dark-1440');
	});

	test('no model can run: the presets are listed greyed with the reason', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen({ qualifies: false, reason: 'models_missing' }), flux({ qualifies: false, reason: 'not_assigned' })] })]);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await presetTrigger(page).click();
		const qwenRow = page.locator('[data-preset-row="qwen21"]');
		const fluxRow = page.locator('[data-preset-row="flux-fill"]');
		await expect(qwenRow).toHaveAttribute('aria-disabled', 'true');
		await expect(qwenRow.locator('[data-preset-unavailable]')).toHaveText('Models not installed');
		await expect(fluxRow).toHaveAttribute('aria-disabled', 'true');
		await expect(fluxRow.locator('[data-preset-unavailable]')).toHaveText('Not available to your account');
		await expect(qwenRow.locator('button.row-main')).toBeDisabled();
		await screenshot(page, JOURNEY, '11b-picker-greyed-dark-1440');
	});

	test('reloading in the middle of a run re-attaches to it', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
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

	test('the preset, mode and session pickers: pick each, and Generate sends that editor mode and that session', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen(), flux(), fluxWide()], default: sessionDefault })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(page.locator('[data-preset-row]')).toHaveCount(0);
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await expect(modeTrigger(page)).toHaveCount(0);
		await presetTrigger(page).click();
		await expect(page.locator('[data-preset-row]')).toHaveCount(2);
		await page.locator('[data-preset-row="flux-fill"] button.row-main').click();
		await expect(presetTrigger(page)).toHaveText('Flux Fill');
		await expect(modeTrigger(page)).toHaveText(/Inpaint/);
		await modeTrigger(page).click();
		await expect(page.locator('[role="option"]')).toHaveCount(2);
		await expect(page.locator('[role="option"]').nth(1)).toContainText('Fills a wider area');
		await page.locator('[role="option"]').nth(1).click();
		await expect(modeTrigger(page)).toHaveText(/Wide fill/);
		await sessionTrigger(page).click();
		await expect(page.locator('[data-session-row]')).toHaveCount(1);
		await page.locator('[data-session-row="sess-flux"]').click();
		await expect(sessionTrigger(page)).toHaveText(/Wide and soft/);
		await expect(defaultStar(page)).toHaveAttribute('aria-pressed', 'false');
		await defaultStar(page).hover();
		await expect(starTip(page)).toContainText(/Your default is Qwen-Image 2\.1 · .+ · .+/);
		await page.mouse.move(5, 5);
		await screenshot(page, JOURNEY, '12b-pickers-mode-dark-1440');
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		expect((server.runs[0].setup as Json)).toMatchObject({ preset_id: 'flux-fill', mode: 'editor:fill2', form_data: { steps: 11 } });
		expect((server.runs[0].setup as Json)).not.toHaveProperty('variant');
		expect(server.defaults).toHaveLength(0);
		await defaultStar(page).click();
		await expect.poll(() => server.defaults.length).toBe(1);
		expect(server.defaults[0]).toEqual({ preset_id: 'flux-fill', mode: 'editor:fill2', setup: { kind: 'session', id: 'sess-flux' } });
		await expect(defaultStar(page)).toHaveAttribute('aria-pressed', 'true');
		await defaultStar(page).click();
		await expect.poll(() => server.defaultClears).toBe(1);
		await expect(defaultStar(page)).toHaveAttribute('aria-pressed', 'false');
	});

	test('the result count is a field of Settings and a formula applied from the dock reaches the run', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true, undefined, true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await expect(prompt(page).locator('[aria-label="Results"]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-testid="formulas-button"]')).toBeVisible();
		await markArea(page);
		await type(page, 'a small red sailboat');
		await screenshot(page, JOURNEY, '30-count-in-form-dark-1440');
		await setResults(page, 3);
		await prompt(page).locator('[data-testid="formulas-button"]').click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await expect(drawer).toBeVisible();
		await drawer.locator('[data-row-id="formula-1"]').click();
		await expect.poll(() => server.plans.length).toBeGreaterThan(0);
		expect(server.plans[0]).toMatchObject({ mode: 'editor:edit' });
		await drawer.getByRole('button', { name: /^Apply/ }).click();
		await page.keyboard.press('Escape');
		await prompt(page).locator('[data-prompt-settings]').click();
		const view = page.locator('[data-frame-panel] [data-settings-view="desktop"]');
		const row = view.locator('[data-applied-formula="formula-1"]');
		await expect(row).toContainText('Crisp and quick');
		await expect(row.locator('[data-applied-counts]')).toContainText('2 changed · 1 skipped');
		await screenshot(page, JOURNEY, '31-formula-applied-dark-1440');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		expect((server.runs[0].setup as Json).form_data).toMatchObject({ strength: 0.9, steps: 8, quantity: 3 });
		expect(server.runs[0].candidates).toBe(3);
		await row.getByRole('button', { name: 'Remove Crisp and quick' }).click();
		await expect(view.locator('[data-applied-formula]')).toHaveCount(0);
	});

	test('Settings: change a value, run with it, save through the session control, save as new, and the discard prompts', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await expect(sessionTrigger(page)).toHaveText(/Soft portrait/);
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
		await expect(view.locator('[data-settings-body]')).toContainText('Quantity');
		await expect(view.locator('[data-settings-body]')).not.toContainText('Picture');
		expect(server.formModes).toContain('editor:edit');
		expect(server.formModes).not.toContain('edit');
		await expect(page.locator('[data-edit-stack]')).toHaveAttribute('data-folded', 'true');
		await expect(view.locator('[data-settings-footer]')).toHaveCount(0);
		await expect(page.locator('[data-testid="session-save"]')).toHaveCount(1);

		const strength = view.locator('[data-settings-body] input[type="number"]').first();
		await strength.fill('0.6');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toHaveText('Changed');
		await expect(prompt(page).locator('[data-setup-changed]')).toBeVisible();
		await screenshot(page, JOURNEY, '04-settings-dark-1440');

		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		const form = (server.runs[0].setup as Json).form_data as Json;
		expect(form).toMatchObject({ strength: 0.6, steps: 20 });
		expect(form).not.toHaveProperty('source_image');
		expect(form).toMatchObject({ quantity: 1 });

		await prompt(page).locator('[data-testid="session-save"]').click();
		await expect.poll(() => server.sessionPuts.length).toBe(1);
		expect(server.sessionPuts[0]).toMatchObject({ name: 'Soft portrait', mode: 'editor:edit', base_version: 3, kind: 'editor', data: { 'editor:edit': { formData: { strength: 0.6, steps: 20 } } } });
		await expect(view.locator('[data-settings-changed]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);

		await strength.fill('0.4');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		server.sessionFails = { status: 409, body: { error: 'session_kind_mismatch', message: 'x' } };
		await sessionTrigger(page).click();
		await page.locator('.session-dropdown').getByRole('button', { name: 'Save as new', exact: true }).click();
		await page.locator('#session-save-name').fill('Held by Generate');
		await page.getByRole('button', { name: /^Save/ }).last().click();
		await expect(page.getByText('That name is already used by one of your sessions on the Generate page. Choose another name.')).toBeVisible();
		server.sessionFails = null;
		await page.locator('#session-save-name').fill('Crisp');
		await page.getByRole('button', { name: /^Save/ }).last().click();
		await expect.poll(() => server.sessionPosts.length).toBe(2);
		expect(server.sessionPosts[1]).toMatchObject({ preset_id: 'qwen21', name: 'Crisp', mode: 'editor:edit', kind: 'editor', data: { 'editor:edit': { formData: { strength: 0.4 } } } });
		await expect(sessionTrigger(page)).toHaveText(/Crisp/);
		await expect(view.locator('[data-settings-changed]')).toHaveCount(0);
		expect(server.defaults).toHaveLength(0);

		await strength.fill('0.3');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		const newSession = page.getByRole('button', { name: 'New session', exact: true });
		if (!(await newSession.isVisible())) await sessionTrigger(page).click();
		await newSession.click();
		await expect(page.getByText('"Crisp" has changes that are not saved. Starting a new session clears them.')).toBeVisible();
		await screenshot(page, JOURNEY, '04b-discard-dark-1440');
		await page.getByRole('button', { name: /^Cancel/ }).last().click();
		await expect(sessionTrigger(page)).toHaveText(/Crisp/);
		if (!(await newSession.isVisible())) await sessionTrigger(page).click();
		await newSession.click();
		await page.getByRole('button', { name: /^Confirm/ }).last().click();
		await expect(sessionTrigger(page)).toHaveText(/No saved session/);
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);

		await page.keyboard.press('Escape');
		await expect(view).toHaveCount(0);
		await expect(prompt(page)).toBeVisible();
		await expect(options(page)).toBeVisible();
		await expect(page.locator('[data-edit-stack]')).not.toHaveAttribute('data-folded', 'true');
	});

	test('switching the mode with unsaved changes asks first', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen(), flux(), fluxWide()], default: { preset_id: 'flux-fill', mode: 'editor:fill', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await prompt(page).locator('[data-prompt-settings]').click();
		const view = page.locator('[data-frame-panel] [data-settings-view="desktop"]');
		const strength = view.locator('[data-settings-body] input[type="number"]').first();
		await expect(strength).toBeVisible();
		await strength.fill('0.6');
		await strength.blur();
		await expect(view.locator('[data-settings-changed]')).toBeVisible();
		await modeTrigger(page).click();
		await page.locator('[role="option"]').nth(1).click();
		await expect(page.getByText('Your changes to “Model defaults” are used only for this edit unless you save them.')).toBeVisible();
		await page.getByRole('button', { name: /Keep editing/ }).click();
		await expect(modeTrigger(page)).toHaveText(/Inpaint/);
		await modeTrigger(page).click();
		await page.locator('[role="option"]').nth(1).click();
		await page.getByRole('button', { name: /^Discard/ }).click();
		await expect(modeTrigger(page)).toHaveText(/Wide fill/);
		await expect(prompt(page).locator('[data-setup-changed]')).toHaveCount(0);
	});

	test('the dock sits under the canvas: its height at rest, with results and with an error, and the canvas height left', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()] })]);
		await openHarbor(page);
		const stage = page.locator('[data-image-editor] [data-zoom]').first();
		await expect(stage).toBeVisible();
		const without = (await stage.boundingBox())!.height;
		await expect(page.locator('[data-frame-dock]')).toHaveCount(0);
		await selectRailTool(page, 'edit_region');
		const dock = page.locator('[data-frame-dock]');
		await expect(dock).toBeVisible();
		await expect(prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button')).toBeVisible();
		const rest = (await dock.boundingBox())!;
		const withDock = (await stage.boundingBox())!;
		const stageBox = withDock;
		expect(rest.y).toBeGreaterThanOrEqual(stageBox.y + stageBox.height - 1);
		expect(rest.height).toBeGreaterThanOrEqual(111);
		expect(rest.height).toBeLessThanOrEqual(120);
		expect(withDock.height).toBeGreaterThan(450);
		await screenshot(page, JOURNEY, '14-dock-rest-dark-1440');
		await dock.locator('[data-dock-mark] button').hover();
		await screenshot(page, JOURNEY, '14b-dock-hover-dark-1440');
		await page.mouse.move(10, 10);
		await expect(page.locator('[data-canvas-zoom]')).toBeVisible();
		const zoom = (await page.locator('[data-canvas-zoom]').boundingBox())!;
		const canvasTop = (await page.locator('[data-editor-canvas]').boundingBox())!.y;
		expect(Math.abs(zoom.y - canvasTop - 12)).toBeLessThanOrEqual(1);
		await expect(prompt(page).locator('[data-prompt-grip]')).toHaveCount(0);
		await markArea(page);
		await type(page, 'a lantern');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await expect(dock.locator('[data-border-progress="loading"]')).toHaveCount(1);
		await expect(page.locator('[data-run-chip]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '14c-dock-running-dark-1440');
		update(server, 'done', { candidates: candidates(2) });
		await expect(dock.locator('[data-candidate-strip]')).toBeVisible();
		const results = (await dock.boundingBox())!;
		expect(results.height).toBeGreaterThanOrEqual(155);
		expect(results.height).toBeLessThanOrEqual(170);
		server.startFails = { status: 422, body: { error: 'prompt_required', message: 'x' } };
		await prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button').click();
		await expect(prompt(page).locator('[data-prompt-error]')).toBeVisible();
		const failed = (await dock.boundingBox())!;
		const mark = (await dock.locator('[data-dock-mark] button.generate-button').boundingBox())!;
		expect(Math.round(mark.height)).toBe(70);
		expect(mark.x).toBeGreaterThan(results.x + results.width - 120);
		const measured = `DOCK canvas without dock ${Math.round(without)}px, with dock ${Math.round(withDock.height)}px; dock at rest ${Math.round(rest.height)}px, with results ${Math.round(results.height)}px, with an error ${Math.round(failed.height)}px, mark ${Math.round(mark.height)}px`;
		console.log(measured);
		test.info().annotations.push({ type: 'dock', description: `canvas without dock ${Math.round(without)}px, with dock ${Math.round(withDock.height)}px; dock at rest ${Math.round(rest.height)}px, with results ${Math.round(results.height)}px, with an error ${Math.round(failed.height)}px` });
		await screenshot(page, JOURNEY, '13-dock-dark-1440');
	});

	test('the dock strip: one baseline, cuts inside the strip, even padding, the ring on the prompt field only, and every state', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen({ preset_name: LONG_PRESET }), flux({ preset_name: 'Flux Fill' }), fluxWide(), qwen({ preset_id: 'sd-edit', preset_name: 'SDXL Edit' })], default: sessionDefault }), taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })], true, LONG_SESSION);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText(LONG_PRESET);
		await expect(sessionTrigger(page)).toContainText('Product shots');
		await expect(modeTrigger(page)).toHaveCount(0);
		await page.mouse.move(700, 300);
		const rest = await geometry(page);
		checkStrip(rest);
		expect(rest.cuts).toHaveLength(1);
		expect(rest.preset!.width).toBeLessThanOrEqual(241);
		expect(rest.dock!.height).toBeGreaterThanOrEqual(111);
		expect(rest.dock!.height).toBeLessThanOrEqual(120);
		const audit = await ringAudit(page);
		expect(audit.found, JSON.stringify(audit)).toEqual([]);
		expect(audit.rings).toEqual(['field']);
		await screenshot(page, JOURNEY, '20-built-rest-dark-1440');
		await prompt(page).locator('[data-prompt-field]').focus();
		await page.mouse.move(700, 300);
		const focused = await ringAudit(page);
		expect(focused.found, JSON.stringify(focused)).toEqual([]);
		expect(focused.rings).toEqual(['field']);
		checkStrip(await geometry(page));
		await screenshot(page, JOURNEY, '20b-built-focused-dark-1440');
		await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

		const mark = prompt(page).locator('[data-prompt-mark] button');
		await expect(mark).toBeDisabled();
		await expect(mark).toHaveAccessibleName('Describe the change first');
		await markArea(page);
		await type(page, 'a lantern');
		await expect(mark).toBeEnabled();
		await expect(mark).toHaveAccessibleName('Generate');
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		await expect(prompt(page).locator('[data-prompt-mark="running"] button')).toHaveAccessibleName('Cancel generation');
		update(server, 'running', { progress: 0.4 });
		await expect(prompt(page).locator('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '40');
		await expect(prompt(page).locator('[data-dock-row]')).toHaveCount(2);
		await expect(page.locator('[data-run-chip]')).toHaveCount(0);
		await page.mouse.move(700, 300);
		await screenshot(page, JOURNEY, '25-built-running-dark-1440');
		update(server, 'done', { candidates: candidates(2) });
		await expect(prompt(page).locator('[data-candidate-strip]')).toBeVisible();
		await page.mouse.move(700, 300);
		const results = await geometry(page);
		checkStrip(results, 'temp');
		expect(results.dock!.height).toBeGreaterThanOrEqual(155);
		expect(results.dock!.height).toBeLessThanOrEqual(170);
		expect(results.mark!.y + results.mark!.height / 2 - (rest.mark!.y + rest.mark!.height / 2)).toBeLessThanOrEqual(results.dock!.height - rest.dock!.height);
		await screenshot(page, JOURNEY, '21-built-results-dark-1440');

		server.startFails = { status: 422, body: { error: 'prompt_required', message: 'x' } };
		await prompt(page).locator('[data-prompt-mark] button').click();
		await expect(prompt(page).locator('[data-prompt-error]')).toBeVisible();
		await page.mouse.move(700, 300);
		await screenshot(page, JOURNEY, '20c-built-failed-dark-1440');

		await presetTrigger(page).click();
		const dropdown = page.locator('[role="dialog"][aria-label="Choose preset"]');
		await expect(dropdown).toBeVisible();
		await expect(page.locator('[data-preset-row]')).toHaveCount(3);
		const dropdownBox = (await dropdown.boundingBox())!;
		const rowBox = (await page.locator('[data-preset-row]').last().boundingBox())!;
		expect(dropdownBox.height, 'a short list is not taller than its rows').toBeLessThan(rowBox.y + rowBox.height - dropdownBox.y + 120);
		expect(dropdownBox.y).toBeGreaterThan(60);
		await screenshot(page, JOURNEY, '24-built-presets-open-3-dark-1440');
		await page.keyboard.press('Escape');
		await expect(dropdown).toHaveCount(0);

		await sessionTrigger(page).click();
		await expect(page.locator('.session-dropdown')).toBeVisible();
		const sessionBox = (await page.locator('.session-dropdown').boundingBox())!;
		expect(sessionBox.y).toBeGreaterThan(60);
		await screenshot(page, JOURNEY, '26-built-session-open-dark-1440');
		await page.keyboard.press('Escape');
		await expect(page.locator('.session-dropdown')).toHaveCount(0);

		await presetTrigger(page).click();
		await page.locator('[data-preset-row="flux-fill"] button.row-main').click();
		await expect(presetTrigger(page)).toHaveText('Flux Fill');
		await expect(modeTrigger(page)).toHaveText(/Inpaint/);
		await expect(modeTrigger(page).locator('svg')).toHaveCount(2);
		await page.mouse.move(700, 300);
		const two = await geometry(page);
		checkStrip(two, 'temp');
		expect(two.cuts).toHaveLength(2);
		const order = [two.preset!.x, two.mode!.x, two.session!.x];
		expect([...order].sort((a, b) => a - b)).toEqual(order);
		await screenshot(page, JOURNEY, '22-built-two-modes-dark-1440');
		await modeTrigger(page).click();
		await expect(page.locator('[role="option"]')).toHaveCount(2);
		const optionsBox = (await page.locator('[role="option"]').first().boundingBox())!;
		expect(optionsBox.y).toBeGreaterThan(60);
		await page.keyboard.press('Escape');

		await selectRailTool(page, 'remove_object');
		await expect(prompt(page).locator('[data-dock-row]')).toHaveCount(2);
		await page.mouse.move(700, 300);
		await screenshot(page, JOURNEY, '20d-built-remove-dark-1440');
	});

	test('Settings go back with the header button and the prompt button, and the Edits list can be shown while open', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
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

test.describe('Edit a region at 1280', () => {
	test.use({ viewport: { width: 1280, height: 800 } });

	const flux3 = (over: Json = {}) => flux({ preset_name: 'Flux 3', ...over });
	const widths = (g: Awaited<ReturnType<typeof geometry>>) => `preset ${Math.round(g.preset!.width)} mode ${Math.round(g.mode?.width ?? 0)} session ${Math.round(g.session!.width)} gap ${Math.round(g.end!.x - g.session!.right)}`;

	test('two modes, no session: Flux 3 is whole and the strip leaves no gap before the right group', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [flux3(), fluxWide()], default: { preset_id: 'flux-fill', mode: 'editor:fill', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Flux 3');
		await expect(modeTrigger(page)).toHaveText(/Inpaint/);
		await page.mouse.move(700, 300);
		const g = await geometry(page);
		checkStrip(g, 'wrapped');
		console.log(`1280 flux3 ${widths(g)}`);
		expect(g.preset!.width).toBeGreaterThan(80);
		await screenshot(page, JOURNEY, '27-built-two-modes-dark-1280');
	});

	test('one mode and a session: the long names share the room, preset keeping the most', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen({ preset_name: LONG_PRESET })], default: sessionDefault })], true, LONG_SESSION);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toContainText('MiniMax');
		await page.mouse.move(700, 300);
		const g = await geometry(page);
		checkStrip(g, 'wrapped');
		console.log(`1280 minimax ${widths(g)}`);
		expect(g.preset!.width).toBeGreaterThanOrEqual(g.session!.width * 0.5);
	});

	test('two modes and a long session name', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen(), qwen({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })], default: sessionDefault })], true, LONG_SESSION);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await page.mouse.move(700, 300);
		const g = await geometry(page);
		checkStrip(g, 'wrapped');
		console.log(`1280 qwen ${widths(g)}`);
	});
});

test.describe('Edit a region at 1440 with the default out of the strip', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	const cells = (page: Page) =>
		page.evaluate(() => {
			const read = (selector: string) => {
				const node = document.querySelector<HTMLElement>(selector);
				return node ? { text: node.textContent?.trim() ?? '', cut: node.scrollWidth > node.clientWidth + 1, width: Math.round(node.getBoundingClientRect().width) } : null;
			};
			return { preset: read('[data-preset-value]'), mode: read('[data-testid="preset-header-mode"] button span.truncate'), session: read('[data-testid="session-trigger"]') };
		});

	test('Flux 3 with Edit and Inpaint and no session shows all three whole', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [flux({ preset_name: 'Flux 3' }), fluxWide()], default: { preset_id: 'flux-fill', mode: 'editor:fill', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })], true);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Flux 3');
		await page.mouse.move(700, 300);
		const found = await cells(page);
		console.log(`1440 flux3 ${JSON.stringify(found)}`);
		expect(found.preset!.cut).toBe(false);
		expect(found.mode!.cut).toBe(false);
		expect(found.session!.cut).toBe(false);
		checkStrip(await geometry(page), 'temp');
	});

	test('Qwen-Image 2.1 with two modes and a long session shows the names whole or says which gave way', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen(), qwen({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })], default: sessionDefault })], true, 'Product shots, soft light v3');
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await page.mouse.move(700, 300);
		const found = await cells(page);
		console.log(`1440 qwen ${JSON.stringify(found)}`);
		expect(found.preset!.cut).toBe(false);
		expect(found.session!.text).toContain('Product shots, soft light v3');
		checkStrip(await geometry(page), 'temp');
	});
});

test.describe('Edit a region in a narrow window', () => {
	test.use({ viewport: { width: 1024, height: 768 } });

	test('the dock at 1024: long names truncate and the right group stays whole', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen({ preset_name: LONG_PRESET }), flux()], default: sessionDefault })], true, LONG_SESSION);
		await openHarbor(page);
		await selectRailTool(page, 'edit_region');
		await expect(presetTrigger(page)).toContainText('MiniMax');
		await page.mouse.move(600, 300);
		const g = await geometry(page);
		checkStrip(g, 'wrapped');
		expect(g.end!.right).toBeLessThanOrEqual(g.dock!.right);
		expect(g.save!.x).toBeGreaterThan(g.session!.right - 1);
		expect(g.save!.right).toBeLessThanOrEqual(g.strip!.right);
		expect(g.session!.width).toBeGreaterThan(40);
		const audit = await ringAudit(page);
		expect(audit.found, JSON.stringify(audit)).toEqual([]);
		await screenshot(page, JOURNEY, '23-built-rest-dark-1024');
	});
});

test.describe('Edit a region on the phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the prompt sheet with the three pickers, and the run', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen(), flux()] }), taskEntry('remove_object', 'Remove an object')], true);
		await openHarbor(page);
		await openPhoneTask(page, 'edit_region');
		await expect(page.locator('[role="dialog"][aria-label="Choose preset"]')).toBeVisible();
		await screenshot(page, JOURNEY, '02-first-use-dark-390');
		await pickPreset(page, 'qwen21');
		await expect(prompt(page)).toBeVisible();
		await expect(prompt(page).locator('[data-editor-pickers="phone"]')).toBeVisible();
		await expect(presetTrigger(page)).toHaveText('Qwen-Image 2.1');
		await prompt(page).locator('button[aria-label="Session"]').click();
		await page.locator('[data-session-row="sess-1"]').click();
		await expect(prompt(page).locator('button[aria-label="Session"]')).toContainText('Soft portrait');
		await markArea(page);
		await type(page, 'a small red sailboat, one white sail, calm water');
		await screenshot(page, JOURNEY, '12-pickers-dark-390');
		await screenshot(page, JOURNEY, '01-prompt-dark-390');
		await prompt(page).getByRole('button', { name: 'Collapse' }).click();
		await expect(page.locator('[data-floating-prompt="collapsed"]')).toBeVisible();
		await screenshot(page, JOURNEY, '01b-prompt-collapsed-dark-390');
		await page.locator('[data-floating-prompt="collapsed"]').getByRole('button', { name: 'Expand' }).click();
		await generate(page);
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0].setup).toMatchObject({ preset_id: 'qwen21', mode: 'editor:edit', form_data: { steps: 20 } });
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

	test('a formula applied on the phone is listed in Settings', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region', { offers: [qwen()], default: sessionDefault })], true, undefined, true);
		await openHarbor(page);
		await openPhoneTask(page, 'edit_region');
		await prompt(page).locator('[data-testid="formulas-button"]').click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await expect(drawer).toBeVisible();
		await drawer.locator('[data-row-id="formula-1"]').click();
		await drawer.getByRole('button', { name: /^Apply/ }).click();
		await page.keyboard.press('Escape');
		await prompt(page).locator('[data-prompt-settings]').click();
		const view = page.locator('[data-settings-view="phone"]');
		await expect(view.locator('[data-applied-formula="formula-1"]')).toContainText('Crisp and quick');
		await screenshot(page, JOURNEY, '32-formula-applied-dark-390');
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
		await expect(view.locator('[data-settings-footer]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '04-settings-dark-390');
		await view.locator('[data-detail-back]').click();
		await expect(view).toHaveCount(0);
		await expect(prompt(page)).toBeVisible();
		await expect(prompt(page).locator('[data-setup-changed]')).toBeVisible();
		await prompt(page).locator('button[aria-label="Session"]').click();
		await page.getByRole('button', { name: 'New session', exact: true }).click();
		await expect(page.getByText(/has changes that are not saved/)).toBeVisible();
		await screenshot(page, JOURNEY, '04b-discard-dark-390');
	});
});
