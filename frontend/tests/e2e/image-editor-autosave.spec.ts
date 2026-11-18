import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot, selectRailTool } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-autosave';
const GENERATION_ID = 'gen-e2e-autosave';
const FILE_ID = 502;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_one';

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [{ name: 'prompt', type: 'string', title: 'Prompt', default: 'a harbor at dawn' }]
		}
	}
};

interface Server {
	creates: Json[];
	saves: Array<{ id: string; body: Json }>;
	gets: string[];
	revision: number;
	nextId: number;
	conflictNext: boolean;
	down: boolean;
	remoteState: Json | null;
	gate: Promise<void> | null;
}

function generation(): Json {
	const now = new Date().toISOString();
	return {
		id: GENERATION_ID,
		preset_id: 'krea2',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: now,
		updated_at: now,
		completed_at: now,
		files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }]
	};
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function editRecord(server: Server, id: string, state: Json | null): Json {
	const now = new Date().toISOString();
	return {
		id,
		tool: 'image',
		name: null,
		source: { kind: 'history', id: String(FILE_ID) },
		thumbnail_upload_id: null,
		revision: server.revision,
		created_at: now,
		updated_at: now,
		...(state ? { state } : {})
	};
}

async function mockServer(page: Page): Promise<Server> {
	const server: Server = {
		creates: [],
		saves: [],
		gets: [],
		revision: 1,
		nextId: 1,
		conflictNext: false,
		down: false,
		remoteState: null,
		gate: null
	};
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? ok(route, { generations: [generation()], total: 1 }) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: source })
	);
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));

	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const method = request.method();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		if (server.down) return route.abort('failed');
		if (server.gate) await server.gate;
		if (method === 'POST' && !id) {
			const body = request.postDataJSON() as Json;
			server.creates.push(body);
			const newId = server.nextId === 1 ? EDIT_ID : `ed_e2e_${server.nextId}`;
			server.nextId += 1;
			server.revision = 1;
			return ok(route, { edit: editRecord(server, newId, body.state as Json) });
		}
		if (method === 'GET' && id) {
			server.gets.push(id);
			return ok(route, { edit: editRecord(server, id, server.remoteState ?? ((server.creates[0]?.state as Json) ?? {})) });
		}
		if (method === 'PUT' && id) {
			const body = request.postDataJSON() as Json;
			server.saves.push({ id, body });
			if (server.conflictNext || body.revision !== server.revision) {
				server.conflictNext = false;
				server.revision += 1;
				return route.fulfill({
					status: 409,
					contentType: 'application/json',
					body: JSON.stringify({ detail: { success: false, error: 'edit_revision_conflict', message: 'changed', revision: server.revision } })
				});
			}
			server.revision += 1;
			return ok(route, { edit: editRecord(server, id, null) });
		}
		return route.fallback();
	});
	return server;
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

function indicator(page: Page, state?: string) {
	return page.locator(state ? `[data-edit-save-state="${state}"]` : '[data-edit-save-state]');
}

async function openHarbor(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-recent-history] button').first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
	await expect(page.locator('[data-crop-overlay]')).toBeVisible();
}

async function applyCrop(page: Page) {
	await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
	await expect(page.locator('[data-options-panel] [data-crop-output]')).toHaveText('480×480');
	await page.locator('[data-options-apply]').click();
}

async function applyReel(page: Page) {
	await selectRailTool(page, 'effects');
	await expect(page.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
	await page.locator('[data-look-item="reel"]').click();
	await expect(page.locator('[data-stack-row="pending"]')).toContainText('Reel');
	await page.locator('[data-options-apply]').click();
}

async function begin(page: Page): Promise<Server> {
	const server = await mockServer(page);
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return server;
}

test.describe('Image editor autosave on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a change creates the edit and later changes are sent as one debounced update', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		await expect(indicator(page)).toHaveCount(0);
		expect(server.creates).toHaveLength(0);

		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);
		const created = server.creates[0];
		expect(created.tool).toBe('image');
		expect(created.source).toMatchObject({ kind: 'history' });
		await expect(indicator(page, 'saved')).toBeVisible();
		await expect(indicator(page, 'saved')).toHaveText('Saved');
		await screenshot(page, JOURNEY, '01-saved-1440');

		await applyReel(page);
		await expect.poll(() => server.saves.length).toBe(1);
		expect(server.saves[0].id).toBe(EDIT_ID);
		expect(server.saves[0].body.revision).toBe(1);
		const steps = ((server.saves[0].body.state as Json).stack as Json).steps as unknown[];
		expect(steps).toHaveLength(2);
		expect(server.creates).toHaveLength(1);
		await expect(indicator(page, 'saved')).toBeVisible();
	});

	test('shows Saving while a request is in flight', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		let release: () => void = () => undefined;
		server.gate = new Promise<void>((resolve) => (release = resolve));
		await applyCrop(page);
		await expect(indicator(page, 'saving')).toBeVisible();
		await screenshot(page, JOURNEY, '02-saving-1440');
		server.gate = null;
		release();
		await expect(indicator(page, 'saved')).toBeVisible();
	});

	test('a conflict shows the notice and Keep mine as a copy links the tab to a new edit', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);
		server.conflictNext = true;
		await applyReel(page);

		const notice = page.locator('[data-edit-notice="conflict"]');
		await expect(notice).toBeVisible();
		await expect(page.getByRole('button', { name: 'Load the newer one' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Keep mine as a copy' })).toBeVisible();
		await expect(indicator(page, 'conflict')).toBeVisible();
		await screenshot(page, JOURNEY, '03-conflict-1440');
		const savesBefore = server.saves.length;

		await page.getByRole('button', { name: 'Keep mine as a copy' }).click();
		await expect.poll(() => server.creates.length).toBe(2);
		const copySteps = ((server.creates[1].state as Json).stack as Json).steps as unknown[];
		expect(copySteps).toHaveLength(2);
		await expect(notice).toHaveCount(0);
		await expect(indicator(page, 'saved')).toBeVisible();
		expect(server.saves).toHaveLength(savesBefore);
	});

	test('Load the newer one replaces the tab with the saved version and sends nothing', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);
		server.remoteState = server.creates[0].state as Json;
		server.conflictNext = true;
		await applyReel(page);
		await expect(page.locator('[data-edit-notice="conflict"]')).toBeVisible();
		const savesBefore = server.saves.length;

		await page.getByRole('button', { name: 'Load the newer one' }).click();
		await expect(page.locator('[data-edit-notice]')).toHaveCount(0);
		expect(server.gets).toContain(EDIT_ID);
		await expect(indicator(page, 'saved')).toBeVisible();
		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => server.saves.length).toBe(savesBefore + 1);
		expect(server.creates).toHaveLength(1);
	});

	test('changes made offline are queued and sent once when the connection returns', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);

		await selectRailTool(page, 'effects');
		await expect(page.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
		server.down = true;
		await page.context().setOffline(true);
		await applyReel(page);
		await expect(indicator(page, 'offline')).toBeVisible();
		await expect(indicator(page, 'offline')).toHaveText('Offline');
		await screenshot(page, JOURNEY, '04-offline-1440');
		expect(server.saves).toHaveLength(0);

		server.down = false;
		await page.context().setOffline(false);
		await expect.poll(() => server.saves.length).toBe(1);
		const steps = ((server.saves[0].body.state as Json).stack as Json).steps as unknown[];
		expect(steps).toHaveLength(2);
		await expect(indicator(page, 'saved')).toBeVisible();
	});
});

test.describe('Image editor autosave on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('shows the state chip and the conflict notice', async ({ page }) => {
		const server = await mockServer(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.studio-topbar')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		const phone = page.getByTestId('phone-tool-tab');
		await phone.locator('[data-recent-history] button').first().click();
		await expect(phone.locator('[data-editor-frame="phone"]')).toBeVisible();
		await expect(indicator(page)).toHaveCount(0);

		await phone.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
		await phone.locator('[data-options-apply]').click();
		await expect.poll(() => server.creates.length).toBe(1);
		await expect(indicator(page, 'saved')).toBeVisible();
		await screenshot(page, JOURNEY, '01-saved-390');

		await phone.locator('[data-tool-rail="strip"] button[data-tool="effects"]').click();
		await expect(phone.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
		server.down = true;
		await page.context().setOffline(true);
		await phone.locator('[data-look-item="reel"]').click();
		await phone.locator('[data-options-apply]').click();
		await expect(indicator(page, 'offline')).toBeVisible();
		await screenshot(page, JOURNEY, '04-offline-390');

		server.down = false;
		server.conflictNext = true;
		await page.context().setOffline(false);
		await expect(page.locator('[data-edit-notice="conflict"]')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Load the newer one' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Keep mine as a copy' })).toBeVisible();
		await screenshot(page, JOURNEY, '03-conflict-390');
	});
});
