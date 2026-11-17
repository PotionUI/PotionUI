import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, loginAsOwner, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-continue-from-picture';
const GENERATION_ID = 'gen-e2e-continue';
const FILE_ID = 733;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/lighthouse-night.png`;
const ORIGINAL_ID = 'orig-1';

type Json = Record<string, unknown>;

interface EditRow {
	id: string;
	source: { kind: string; id: string; name: string | null };
	revision: number;
	state: Json;
	updated_at: string;
}

interface PictureLink {
	editId: string;
	revision: number;
	continuable: boolean;
}

interface Picture {
	id: string;
	name: string;
	link: PictureLink | null;
}

interface Server {
	edits: EditRow[];
	pictures: Picture[];
	runs: Json[];
	runPictures: Map<string, string>;
	gets: string[];
	creates: Json[];
	saves: Array<{ id: string; body: Json }>;
	nextId: number;
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
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

function summary(row: EditRow): Json {
	return {
		id: row.id,
		tool: 'image',
		name: null,
		source: row.source,
		thumbnail_upload_id: null,
		revision: row.revision,
		created_at: row.updated_at,
		updated_at: row.updated_at
	};
}

function libraryRow(server: Server, picture: Picture): Json {
	const link = picture.link;
	return {
		id: picture.id,
		filename: `${picture.id}.png`,
		original_filename: picture.name,
		media_type: 'image',
		mime_type: 'image/png',
		url: `/api/e2e-art/${picture.id}.png`,
		thumbnail_medium: `/api/e2e-art/${picture.id}.png`,
		width: 640,
		height: 480,
		tags: [],
		is_favorite: false,
		created_at: '2026-11-01T10:00:00Z',
		made_in_editor: link
			? {
					edit_id: link.editId,
					tool: 'image',
					revision: link.revision,
					available: server.edits.some((edit) => edit.id === link.editId),
					continuable: link.continuable
				}
			: null
	};
}

async function mockServer(page: Page, seed: { pictures?: Picture[]; edits?: EditRow[] } = {}): Promise<Server> {
	const server: Server = {
		edits: seed.edits ?? [],
		pictures: seed.pictures ?? [{ id: ORIGINAL_ID, name: 'lighthouse-night.png', link: null }],
		runs: [],
		runPictures: new Map(),
		gets: [],
		creates: [],
		saves: [],
		nextId: 1
	};
	const picture = solidPng(640, 480);
	const small = solidPng(64, 64);

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? ok(route, { generations: [generation()], total: 1 }) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/lighthouse-night.png*`, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: picture })
	);
	await page.route('**/api/media/files/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: picture }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: picture }));
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: { properties: {} } }));

	await page.route(/\/api\/library\/items(\?.*)?$/, (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const items = server.pictures.map((entry) => libraryRow(server, entry));
		return ok(route, { items, total: items.length, limit: 24, offset: 0 });
	});
	await page.route(/\/api\/library\/items\/[^/?]+(\?.*)?$/, (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
		const found = server.pictures.find((entry) => entry.id === id);
		if (!found) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { success: false } }) });
		return ok(route, libraryRow(server, found));
	});
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) =>
		ok(route, { collections: [], smart_counts: { all: server.pictures.length, favorites: 0, unsorted: server.pictures.length } })
	);
	await page.route(/\/api\/library\/facets$/, (route) => ok(route, { media_types: { image: server.pictures.length } }));
	await page.route(/\/api\/tags(\?.*)?$/, (route) => ok(route, { tags: [] }));

	await page.route(/\/api\/media-tools\/artifacts$/, (route) =>
		ok(route, { id: 'art-1', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art-1.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 })
	);
	await page.route(/\/api\/media-tools\/preview\/frame$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		const body = route.request().postDataJSON() as { inputs: Array<{ item_id?: string }>; output: { mode: string; edit?: { id: string; revision: number } } };
		server.runs.push(body as unknown as Json);
		const runId = `run-${server.runs.length}`;
		const link: PictureLink | null = body.output.edit
			? { editId: body.output.edit.id, revision: body.output.edit.revision, continuable: body.output.mode !== 'replace' }
			: null;
		if (body.output.mode === 'replace') {
			const target = server.pictures.find((entry) => entry.id === body.inputs[0]?.item_id);
			if (target) target.link = link;
			server.runPictures.set(runId, target?.id ?? ORIGINAL_ID);
		} else {
			const created: Picture = { id: `copy-${runId}`, name: 'lighthouse-night-edit.png', link };
			server.pictures = [created, ...server.pictures];
			server.runPictures.set(runId, created.id);
		}
		await ok(route, { run_id: runId }, 202);
	});
	await page.route(/\/api\/media-tools\/runs\/[^/]+$/, async (route) => {
		const runId = route.request().url().split('/').pop() as string;
		const found = server.pictures.find((entry) => entry.id === server.runPictures.get(runId));
		const mode = (server.runs[Number(runId.replace('run-', '')) - 1]?.output as { mode?: string } | undefined)?.mode;
		await ok(route, {
			run_id: runId,
			status: 'completed',
			progress: 1,
			label: 'Saving',
			replaced: mode === 'replace',
			error: null,
			items: found ? [libraryRow(server, found)] : []
		});
	});

	await page.route(/\/api\/edits(\/[^/?]+)?(\/duplicate)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const method = request.method();
		const url = new URL(request.url());
		const id = url.pathname.split('/')[3] ?? null;
		if (!id && method === 'GET') {
			return ok(route, { edits: server.edits.map(summary), total: server.edits.length, limit: 12, offset: 0 });
		}
		if (!id && method === 'POST') {
			const body = request.postDataJSON() as { source: EditRow['source']; state: Json };
			server.creates.push(body as unknown as Json);
			const row: EditRow = {
				id: `ed_new_${server.nextId++}`,
				source: { ...body.source, name: body.source.name ?? null },
				revision: 1,
				state: body.state,
				updated_at: new Date().toISOString()
			};
			server.edits = [row, ...server.edits];
			return ok(route, { edit: { ...summary(row), state: row.state } });
		}
		const row = server.edits.find((candidate) => candidate.id === id);
		if (!row) {
			return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { success: false, error: 'edit_not_found' } }) });
		}
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
		if (method === 'DELETE') {
			server.edits = server.edits.filter((candidate) => candidate.id !== row.id);
			return ok(route, { id: row.id, deleted: true });
		}
		return route.fallback();
	});
	return server;
}

async function begin(page: Page, seed: Parameters<typeof mockServer>[1] = {}): Promise<Server> {
	await loginAsOwner(page);
	await clearEdits(page);
	const server = await mockServer(page, seed);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container:visible, .studio-topbar:visible').first()).toBeVisible({ timeout: 15000 });
	return server;
}

function bookTab(page: Page, name: string | RegExp) {
	return page.locator('button.book-tab', { hasText: name });
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

async function stackRows(page: Page) {
	return page.locator('[data-edit-stack="panel"] [data-stack-row]').evaluateAll((rows) =>
		rows.map((row) => `${row.getAttribute('data-status')}${row.hasAttribute('data-hidden') ? ':hidden' : ''}:${row.textContent?.replace(/\s+/g, '')}`)
	);
}

const ORIGINAL = 'original:00Original640×480·PNG';
const CROP = '01Crop1:1·480×480';
const REEL = '02ReelIntensity100';

async function openHistoryPicture(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-recent-history] button').first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
}

async function applyCrop(page: Page) {
	await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
	await expect(page.locator('[data-options-panel] [data-crop-output]')).toHaveText('480×480');
	await page.locator('[data-options-apply]').click();
	await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `current:${CROP}`]);
}

async function applyReel(page: Page) {
	await rail(page, 'effects').click();
	await expect(page.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
	await page.locator('[data-look-item="reel"]').click();
	await expect(page.locator('[data-stack-row="pending"]')).toContainText('Reel');
	await page.locator('[data-options-apply]').click();
	await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `applied:${CROP}`, `current:${REEL}`]);
}

async function closeTab(page: Page, name: RegExp) {
	await bookTab(page, name).locator('.close-button').click({ force: true });
	const confirm = page.getByRole('button', { name: /Discard and close|^Confirm$/ });
	if (await confirm.isVisible({ timeout: 1500 }).catch(() => false)) await confirm.click();
	await expect(bookTab(page, name)).toHaveCount(0);
}

async function openDetails(page: Page, name: string) {
	await page.goto('/library');
	const card = page.locator('[data-library-card]').filter({ hasText: name });
	await expect(card).toHaveCount(1);
	await card.locator('.media-zoom').click();
	await expect(page.locator('[data-details-action-bar]')).toBeVisible();
}

async function openEditMenu(page: Page) {
	await page.locator('[data-details-action-bar] [data-bar-item="edit"]').click();
	await expect(page.locator('[data-bar-row="edit-image"]')).toBeVisible();
}

function editRows(page: Page) {
	return page.locator('[data-bar-row="continue-edit"], [data-bar-row="edit-image"]');
}

test.describe('Continue an edit from a saved picture on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('save as new, close the tab, then Continue this edit from the Library brings every step back', async ({ page }) => {
		const server = await begin(page);
		await openHistoryPicture(page);
		await applyCrop(page);
		await applyReel(page);
		await expect.poll(() => server.creates.length).toBe(1);
		await expect(page.locator('[data-edit-save-state="saved"]')).toBeVisible();

		await page.locator('[data-frame-save]').click();
		await expect.poll(() => server.runs.length).toBe(1);
		const sent = (server.runs[0].output as { mode: string; edit?: { id: string; revision: number } });
		expect(sent.mode).toBe('new');
		expect(sent.edit?.id).toBe(server.edits[0].id);
		expect(sent.edit?.revision).toBeGreaterThanOrEqual(1);
		await expect(page.getByText('Saved to Library').first()).toBeVisible();

		await closeTab(page, /lighthouse-night/);
		await openDetails(page, 'lighthouse-night-edit.png');
		await expect(page.locator('[data-made-in-editor]')).toContainText('Made in the editor');
		await expect(page.locator('[data-continue-edit]')).toHaveText('Continue this edit');
		await screenshot(page, JOURNEY, '01-details-line-1440');

		await openEditMenu(page);
		await expect(editRows(page)).toHaveText([/Continue this edit/, /Edit this picture/]);
		await screenshot(page, JOURNEY, '02-two-row-menu-1440');
		await page.locator('[data-bar-row="continue-edit"]').click();

		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `applied:${CROP}`, `current:${REEL}`]);
		expect(new Set(server.gets)).toEqual(new Set([server.edits[0].id]));
		await expect(bookTab(page, 'lighthouse-night').getByTestId('tab-unsaved')).toHaveCount(0);

		const savesBefore = server.saves.length;
		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => server.saves.length).toBeGreaterThan(savesBefore);
		expect(server.saves.at(-1)?.id).toBe(server.edits[0].id);
		expect(server.creates).toHaveLength(1);
		await expect(page.locator('[data-edit-save-state="saved"]')).toBeVisible();

		await page.locator('[data-frame-save]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		const second = server.runs[1].output as { mode: string; target_item_id?: string; edit?: { id: string } };
		expect(second.mode).toBe('update');
		expect(second.target_item_id).toBe('copy-run-1');
		expect(second.edit?.id).toBe(server.edits[0].id);
	});

	test('the item menu and the Tools menu offer both rows and Continue reopens the edit', async ({ page }) => {
		const edit: EditRow = {
			id: 'ed_menu',
			source: { kind: 'history', id: String(FILE_ID), name: 'lighthouse-night.png' },
			revision: 3,
			state: {},
			updated_at: new Date().toISOString()
		};
		const server = await begin(page, {
			edits: [edit],
			pictures: [{ id: 'copy-menu', name: 'lighthouse-night-edit.png', link: { editId: 'ed_menu', revision: 3, continuable: true } }]
		});
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'lighthouse-night-edit.png' });
		await expect(card).toHaveCount(1);
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		const menu = page.locator('[data-tools-menu]');
		await expect(menu.locator('[data-tool="continue-edit"], [data-tool="edit-image"]')).toHaveText([/Continue this edit/, /Edit this picture|Open in Image editor/]);
		await screenshot(page, JOURNEY, '06-item-menu-1440');
		await menu.locator('[data-tool="continue-edit"]').click();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		expect(server.gets).toContain('ed_menu');
	});

	test('a deleted edit leaves only Edit this picture and a details line without an action', async ({ page }) => {
		const server = await begin(page, {
			pictures: [
				{ id: 'copy-gone', name: 'lighthouse-night-edit.png', link: { editId: 'ed_gone', revision: 4, continuable: true } },
				{ id: ORIGINAL_ID, name: 'lighthouse-night.png', link: null }
			]
		});
		expect(server.edits).toHaveLength(0);
		await openDetails(page, 'lighthouse-night-edit.png');
		await expect(page.locator('[data-made-in-editor]')).toContainText('Made in the editor');
		await expect(page.locator('[data-continue-edit]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '03-details-edit-gone-1440');
		await openEditMenu(page);
		await expect(editRows(page)).toHaveCount(1);
		await expect(page.locator('[data-bar-row="continue-edit"]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '04-one-row-edit-gone-1440');
		await page.locator('[data-bar-row="edit-image"]').click();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		await expect.poll(() => stackRows(page)).toEqual(['current:00Original640×480·PNG']);
		expect(server.gets).toEqual([]);
	});

	test('a picture saved over the original offers only Edit this picture', async ({ page }) => {
		const server = await begin(page);
		await openDetails(page, 'lighthouse-night.png');
		await openEditMenu(page);
		await expect(editRows(page)).toHaveCount(1);
		await page.locator('[data-bar-row="edit-image"]').click();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		await rail(page, 'crop').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
		await applyCrop(page);
		await expect.poll(() => server.creates.length).toBe(1);
		await expect(page.locator('[data-edit-save-state="saved"]')).toBeVisible();

		await page.locator('[data-frame-save-menu]').click();
		await page.locator('[data-save-row="replace"]').click();
		await expect.poll(() => server.runs.length).toBe(1);
		const sent = server.runs[0].output as { mode: string; edit?: { id: string } };
		expect(sent.mode).toBe('replace');
		expect(sent.edit?.id).toBe(server.edits[0].id);

		await closeTab(page, /lighthouse-night/);
		await openDetails(page, 'lighthouse-night.png');
		await expect(page.locator('[data-made-in-editor]')).toContainText('Made in the editor');
		await expect(page.locator('[data-continue-edit]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '05-details-replaced-1440');
		await openEditMenu(page);
		await expect(editRows(page)).toHaveCount(1);
		await expect(page.locator('[data-bar-row="continue-edit"]')).toHaveCount(0);
	});
});

test.describe('Continue an edit from a saved picture on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the two-row menu and the details line fit the screen', async ({ page }) => {
		const edit: EditRow = {
			id: 'ed_seed',
			source: { kind: 'history', id: String(FILE_ID), name: 'lighthouse-night.png' },
			revision: 3,
			state: {},
			updated_at: new Date().toISOString()
		};
		await begin(page, {
			edits: [edit],
			pictures: [{ id: 'copy-seed', name: 'lighthouse-night-edit.png', link: { editId: 'ed_seed', revision: 3, continuable: true } }]
		});
		await openDetails(page, 'lighthouse-night-edit.png');
		await expect(page.locator('[data-made-in-editor]')).toBeVisible();
		await expect(page.locator('[data-continue-edit]')).toBeVisible();
		await screenshot(page, JOURNEY, '01-details-line-390');
		await openEditMenu(page);
		await expect(editRows(page)).toHaveText([/Continue this edit/, /Edit this picture/]);
		await screenshot(page, JOURNEY, '02-two-row-menu-390');
	});
});
