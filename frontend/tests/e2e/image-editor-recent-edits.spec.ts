import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-recent-edits';
const GENERATION_ID = 'gen-e2e-recent';
const FILE_ID = 612;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;

type Json = Record<string, unknown>;

interface Row {
	id: string;
	name: string | null;
	source: { kind: string; id: string; name: string | null };
	revision: number;
	state: Json;
	updated_at: string;
}

interface Server {
	rows: Row[];
	lists: string[];
	creates: Json[];
	saves: Array<{ id: string; body: Json }>;
	gets: string[];
	renames: Array<{ id: string; name: string | null }>;
	duplicates: string[];
	deletes: string[];
	history: boolean;
	failList: boolean;
	gate: Promise<void> | null;
	nextId: number;
}

const KINDS = ['library', 'history', 'upload'];
const NAMED: Record<number, string> = { 1: 'Cover crop, warm', 3: 'Poster, final', 5: 'Moon series 3', 7: 'Lab bench cleanup' };

function seedRows(total: number): Row[] {
	return Array.from({ length: total }, (_, index) => {
		const n = index + 1;
		const stamp = new Date(Date.now() - n * n * 3600 * 1000).toISOString();
		return {
			id: `ed_${n}`,
			name: NAMED[n] ?? null,
			source: { kind: KINDS[n % 3], id: `src-${n}`, name: `picture-${n}.png` },
			revision: 2,
			state: {},
			updated_at: stamp
		};
	});
}

function summary(row: Row): Json {
	return {
		id: row.id,
		tool: 'image',
		name: row.name,
		source: row.source,
		thumbnail_upload_id: null,
		revision: row.revision,
		created_at: row.updated_at,
		updated_at: row.updated_at
	};
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

async function mockServer(page: Page, total: number): Promise<Server> {
	const server: Server = {
		rows: seedRows(total),
		lists: [],
		creates: [],
		saves: [],
		gets: [],
		renames: [],
		duplicates: [],
		deletes: [],
		history: true,
		failList: false,
		gate: null,
		nextId: 1
	};
	const picture = solidPng(640, 480);

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET'
			? ok(route, { generations: server.history ? [generation()] : [], total: server.history ? 1 : 0 })
			: route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: picture }));
	await page.route('**/api/media/files/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: picture }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: picture }));
	await page.route(/\/api\/library\/items\/[^/?]+(\?.*)?$/, (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = new URL(route.request().url()).pathname.split('/').pop() ?? 'item';
		return ok(route, {
			id,
			filename: `${id}.png`,
			original_filename: `${id}.png`,
			media_type: 'image',
			mime_type: 'image/png',
			url: `/api/e2e-art/${id}.png`,
			width: 640,
			height: 480
		});
	});
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: { properties: {} } }));

	await page.route(/\/api\/edits(\/[^/?]+)?(\/duplicate)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const method = request.method();
		const url = new URL(request.url());
		const parts = url.pathname.split('/');
		const id = parts[3] ?? null;
		const sub = parts[4] ?? null;
		if (server.gate) await server.gate;
		if (!id && method === 'GET') {
			server.lists.push(url.search);
			if (server.failList) return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: { success: false } }) });
			const limit = Number(url.searchParams.get('limit') ?? 50);
			const offset = Number(url.searchParams.get('offset') ?? 0);
			const needle = (url.searchParams.get('search') ?? '').toLowerCase();
			const matching = needle ? server.rows.filter((row) => `${row.name ?? ''} ${row.source.name ?? ''}`.toLowerCase().includes(needle)) : server.rows;
			return ok(route, { edits: matching.slice(offset, offset + limit).map(summary), total: matching.length, limit, offset });
		}
		if (!id && method === 'POST') {
			const body = request.postDataJSON() as { source: Row['source']; state: Json };
			server.creates.push(body as unknown as Json);
			const row: Row = { id: `ed_new_${server.nextId++}`, name: null, source: { ...body.source, name: body.source.name ?? null }, revision: 1, state: body.state, updated_at: new Date().toISOString() };
			server.rows = [row, ...server.rows];
			return ok(route, { edit: { ...summary(row), state: row.state } });
		}
		const row = server.rows.find((candidate) => candidate.id === id);
		if (!row) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { success: false, error: 'edit_not_found' } }) });
		if (method === 'GET') {
			server.gets.push(row.id);
			return ok(route, { edit: { ...summary(row), state: row.state } });
		}
		if (method === 'PUT') {
			const body = request.postDataJSON() as { revision: number; state: Json };
			server.saves.push({ id: row.id, body: body as unknown as Json });
			if (body.revision !== row.revision) {
				return route.fulfill({
					status: 409,
					contentType: 'application/json',
					body: JSON.stringify({ detail: { success: false, error: 'edit_revision_conflict', revision: row.revision } })
				});
			}
			row.revision += 1;
			row.state = body.state;
			return ok(route, { edit: summary(row) });
		}
		if (method === 'PATCH') {
			const body = request.postDataJSON() as { name: string | null };
			server.renames.push({ id: row.id, name: body.name });
			row.name = body.name;
			return ok(route, { edit: summary(row) });
		}
		if (method === 'POST' && sub === 'duplicate') {
			server.duplicates.push(row.id);
			const copy: Row = { ...row, id: `ed_copy_${server.nextId++}`, name: `${row.name ?? row.source.name?.replace(/\.[^.]+$/, '') ?? 'edit'} copy`, revision: 1, updated_at: new Date().toISOString() };
			server.rows = [copy, ...server.rows];
			return ok(route, { edit: { ...summary(copy), state: copy.state } });
		}
		if (method === 'DELETE') {
			server.deletes.push(row.id);
			server.rows = server.rows.filter((candidate) => candidate.id !== row.id);
			return ok(route, { id: row.id, deleted: true });
		}
		return route.fallback();
	});
	return server;
}

async function begin(page: Page, total: number): Promise<Server> {
	const server = await mockServer(page, total);
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	return server;
}

async function openEditor(page: Page, phone: boolean) {
	if (phone) {
		await expect(page.locator('.studio-topbar')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		return page.getByTestId('phone-tool-tab').getByTestId('tool-tab-empty');
	}
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const screen = page.getByTestId('tool-tab-empty');
	await expect(screen).toContainText('Open a picture to edit');
	return screen;
}

function tiles(page: Page) {
	return page.locator('[data-edit-tile][data-edit-tile^="ed_"]');
}

function tile(page: Page, id: string) {
	return page.locator(`[data-edit-tile="${id}"]`);
}

async function openMenu(page: Page, id: string) {
	await tile(page, id).hover();
	await tile(page, id).getByRole('button', { name: /^Options for/ }).click();
	await expect(page.getByRole('menu')).toBeVisible();
}

function confirmDialog(page: Page) {
	return page.getByRole('dialog').or(page.getByRole('alertdialog')).filter({ hasText: 'Delete this edit?' });
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

async function applyCrop(page: Page) {
	await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
	await expect(page.locator('[data-options-panel] [data-crop-output]')).toHaveText('480×480');
	await page.locator('[data-options-apply]').click();
}

async function applyReel(page: Page) {
	await rail(page, 'effects').click();
	await expect(page.locator('[data-tray-item="reel"]')).toBeVisible({ timeout: 15000 });
	await page.locator('[data-tray-item="reel"]').click();
	await expect(page.locator('[data-stack-row="pending"]')).toContainText('Reel');
	await page.locator('[data-options-apply]').click();
}

async function closeEditorTab(page: Page, name: RegExp) {
	await page.locator('button.book-tab', { hasText: name }).locator('.close-button').click({ force: true });
	await page.getByRole('button', { name: /Discard and close|^Confirm$/ }).click();
	await expect(page.locator('button.book-tab', { hasText: name })).toHaveCount(0);
}

function indicator(page: Page, state: string) {
	return page.locator(`[data-edit-save-state="${state}"]`);
}

async function states(page: Page, server: Server, phone: boolean, width: number) {
	const reopen = async () => {
		await page.reload();
		if (phone) return page.getByTestId('phone-tool-tab').getByTestId('tool-tab-empty');
		return openEditor(page, false);
	};

	let screen = await openEditor(page, phone);
	await expect(tiles(page)).toHaveCount(12);
	await expect(screen.locator('[data-recent-edits]')).toContainText('27');
	await tile(page, 'ed_2').hover();
	await screenshot(page, JOURNEY, `01-populated-${width}`);

	await openMenu(page, 'ed_3');
	await screenshot(page, JOURNEY, `05-action-menu-${width}`);
	await page.keyboard.press('Escape');

	await openMenu(page, 'ed_4');
	await page.getByRole('menuitem', { name: 'Rename' }).click();
	await page.getByLabel('Name for this edit').fill('Glass study, soft light');
	await screenshot(page, JOURNEY, `06-rename-${width}`);
	await page.keyboard.press('Escape');
	await expect(page.getByLabel('Name for this edit')).toHaveCount(0);

	await openMenu(page, 'ed_5');
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	await expect(confirmDialog(page)).toBeVisible();
	await screenshot(page, JOURNEY, `07-delete-confirm-${width}`);
	await page.keyboard.press('Escape');
	await expect(confirmDialog(page)).toHaveCount(0);
	expect(server.deletes).toEqual([]);

	await screen.getByLabel('Search edits by name').fill('lantern');
	await expect(screen.locator('[data-empty-state="compact"]')).toContainText('No matching edits');
	await expect(screen.locator('[data-recent-edits]')).toContainText('0 of 27');
	await screenshot(page, JOURNEY, `03-search-no-match-${width}`);
	await screen.getByRole('button', { name: 'Clear search' }).first().click();
	await expect(tiles(page)).toHaveCount(12);

	await page.getByRole('button', { name: 'Page 2' }).scrollIntoViewIfNeeded();
	await page.getByRole('button', { name: 'Page 2' }).click();
	await expect(page.getByRole('button', { name: 'Page 2' })).toHaveAttribute('aria-current', 'page');
	await screenshot(page, JOURNEY, `09-pagination-${width}`);

	let release: () => void = () => undefined;
	server.gate = new Promise<void>((resolve) => (release = resolve));
	screen = await reopen();
	await expect(screen.locator('[data-edits-loading]')).toBeVisible();
	await screenshot(page, JOURNEY, `04-loading-${width}`);
	server.gate = null;
	release();
	await expect(tiles(page)).toHaveCount(12);

	server.failList = true;
	screen = await reopen();
	await expect(screen.locator('[data-empty-state="compact"]')).toContainText("Couldn't load your edits");
	await screenshot(page, JOURNEY, `08-load-error-${width}`);
	server.failList = false;
	await screen.getByRole('button', { name: 'Retry' }).click();
	await expect(tiles(page)).toHaveCount(12);

	const saved = server.rows;
	server.rows = [];
	screen = await reopen();
	await expect(screen.locator('[data-empty-state="compact"]')).toContainText('No edits yet');
	await expect(screen.locator('[data-recent-history] [data-edit-tile]')).toHaveCount(1);
	await expect(screen.getByLabel('Search edits by name')).toHaveCount(0);
	await screenshot(page, JOURNEY, `02-first-use-${width}`);

	server.history = false;
	screen = await reopen();
	await expect(screen.locator('[data-empty-state="page"]')).toContainText('No edits yet');
	await expect(screen.locator('[data-recent-history]')).toHaveCount(0);
	await screenshot(page, JOURNEY, `02b-first-use-no-history-${width}`);
	server.history = true;
	server.rows = saved;
}

test.afterEach(async ({ page }) => {
	await clearEdits(page).catch(() => undefined);
});

test.describe('Image editor recent edits on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('change a picture, close the tab, open a new editor tab and continue the edit', async ({ page }) => {
		const server = await begin(page, 0);
		let screen = await openEditor(page, false);
		await expect(screen.locator('[data-empty-state="compact"]')).toContainText('No edits yet');
		await screen.locator('[data-recent-history] button').first().click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);
		expect((server.creates[0].source as Json).name).toBe('harbor-dawn.png');
		await expect(indicator(page, 'saved')).toBeVisible();

		await closeEditorTab(page, /harbor-dawn/);
		screen = await openEditor(page, false);
		await expect(tiles(page)).toHaveCount(1);
		await expect(tiles(page).first().locator('[data-edit-name]')).toHaveText('harbor-dawn');
		await tiles(page).first().hover();
		await screenshot(page, JOURNEY, 'continue-1440');
		await tiles(page).first().locator('[data-edit-open]').click();

		await expect(page.locator('[data-image-editor]')).toBeVisible();
		await expect(page.locator('[data-image-editor] [data-frame-undo]')).toBeEnabled();
		expect(new Set(server.gets)).toEqual(new Set([server.rows[0].id]));
		await applyReel(page);
		await expect.poll(() => server.saves.length).toBe(1);
		expect(server.saves[0].body.revision).toBe(1);
		expect(server.creates).toHaveLength(1);
		await expect(indicator(page, 'saved')).toBeVisible();
	});

	test('an edit open in another tab is marked and clicking it goes to that tab', async ({ page }) => {
		const server = await begin(page, 27);
		await openEditor(page, false);
		await tile(page, 'ed_2').locator('[data-edit-open]').click();
		await expect(page.locator('[data-image-editor]')).toBeVisible();
		expect(new Set(server.gets)).toEqual(new Set(['ed_2']));

		await page.getByRole('button', { name: 'Add new tab' }).click();
		await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
		await expect(page.getByTestId('tool-tab-empty')).toBeVisible();
		await expect(tile(page, 'ed_2')).toHaveAttribute('data-open-elsewhere', '');
		await expect(tile(page, 'ed_2').locator('[data-open-badge]')).toContainText('Open in another tab');
		await tile(page, 'ed_2').hover();
		await screenshot(page, JOURNEY, 'open-in-another-tab-1440');
		const getsBefore = server.gets.length;
		const tabsBefore = await page.locator('button.book-tab').count();
		await tile(page, 'ed_2').locator('[data-edit-open]').click();
		await expect(page.getByTestId('tool-tab-empty')).toHaveCount(0);
		await expect(page.locator('[data-image-editor]')).toBeVisible();
		await expect.poll(() => server.gets.length).toBe(getsBefore + 1);
		expect(new Set(server.gets)).toEqual(new Set(['ed_2']));
		await expect(page.locator('button.book-tab')).toHaveCount(tabsBefore);
		expect(server.creates).toHaveLength(0);
		expect(server.saves).toHaveLength(0);
	});

	test('search, rename, duplicate, delete and pagination', async ({ page }) => {
		const server = await begin(page, 27);
		const screen = await openEditor(page, false);
		await expect(tiles(page)).toHaveCount(12);
		await expect(screen.locator('[data-recent-edits]')).toContainText('27');
		await expect(tile(page, 'ed_1').locator('[data-edit-name]')).toHaveText('Cover crop, warm');
		await expect(tile(page, 'ed_2').locator('[data-edit-name]')).toHaveText('picture-2');

		await page.getByRole('button', { name: 'Page 3' }).click();
		await expect(tiles(page)).toHaveCount(3);
		expect(server.lists.at(-1)).toContain('offset=24');
		await page.getByRole('button', { name: 'Page 1' }).click();
		await expect(tiles(page)).toHaveCount(12);

		await screen.getByLabel('Search edits by name').fill('poster');
		await expect(tiles(page)).toHaveCount(1);
		expect(server.lists.at(-1)).toContain('search=poster');
		await expect(screen.locator('[data-recent-edits]')).toContainText('1 of 27');
		await screen.getByRole('button', { name: 'Clear search' }).click();
		await expect(tiles(page)).toHaveCount(12);

		await openMenu(page, 'ed_4');
		await page.getByRole('menuitem', { name: 'Rename' }).click();
		await page.getByLabel('Name for this edit').fill('Glass study, soft light');
		await page.keyboard.press('Enter');
		await expect(tile(page, 'ed_4').locator('[data-edit-name]')).toHaveText('Glass study, soft light');
		expect(server.renames).toEqual([{ id: 'ed_4', name: 'Glass study, soft light' }]);

		await openMenu(page, 'ed_4');
		await page.getByRole('menuitem', { name: 'Rename' }).click();
		await page.getByLabel('Name for this edit').fill('');
		await page.keyboard.press('Enter');
		await expect(tile(page, 'ed_4').locator('[data-edit-name]')).toHaveText('picture-4');
		expect(server.renames.at(-1)).toEqual({ id: 'ed_4', name: null });

		await openMenu(page, 'ed_2');
		await page.getByRole('menuitem', { name: 'Duplicate' }).click();
		await expect(tiles(page).first().locator('[data-edit-name]')).toHaveText('picture-2 copy');
		expect(server.duplicates).toEqual(['ed_2']);
		await expect(screen.locator('[data-recent-edits]')).toContainText('28');

		await openMenu(page, 'ed_5');
		await page.getByRole('menuitem', { name: 'Delete' }).click();
		const dialog = confirmDialog(page);
		await expect(dialog).toContainText('Delete this edit?');
		await expect(dialog).toContainText('The original picture is not affected.');
		await dialog.getByRole('button', { name: 'Delete edit' }).click();
		await expect(tile(page, 'ed_5')).toHaveCount(0);
		expect(server.deletes).toEqual(['ed_5']);
		await expect(screen.locator('[data-recent-edits]')).toContainText('27');
	});

	test('a deleted edit shows a plain message and the list refreshes', async ({ page }) => {
		const server = await begin(page, 27);
		await openEditor(page, false);
		await expect(tiles(page)).toHaveCount(12);
		server.rows = server.rows.filter((row) => row.id !== 'ed_6');
		await tile(page, 'ed_6').locator('[data-edit-open]').click();
		await expect(page.getByText('That edit no longer exists.')).toBeVisible();
		await expect(tile(page, 'ed_6')).toHaveCount(0);
	});

	test('every state at 1440', async ({ page }) => {
		const server = await begin(page, 27);
		await states(page, server, false, 1440);
	});
});

test.describe('Image editor recent edits on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('every state at 390 without sideways scrolling', async ({ page }) => {
		const server = await begin(page, 27);
		await states(page, server, true, 390);
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
		expect(overflow).toBeLessThanOrEqual(0);
	});

	test('the start strip shows above the list and a tap continues the edit', async ({ page }) => {
		const server = await begin(page, 27);
		const screen = await openEditor(page, true);
		await expect(tiles(page)).toHaveCount(12);
		const strip = await screen.locator('[data-start-panel]').boundingBox();
		const list = await screen.locator('[data-edits-list]').boundingBox();
		expect(strip && list && strip.y < list.y).toBe(true);
		await expect(screen.locator('[data-start-source]')).toHaveCount(4);
		await tile(page, 'ed_2').locator('[data-edit-open]').tap();
		await expect(page.getByTestId('phone-tool-tab').locator('[data-editor-frame="phone"]')).toBeVisible();
		expect(new Set(server.gets)).toEqual(new Set(['ed_2']));
	});
});
