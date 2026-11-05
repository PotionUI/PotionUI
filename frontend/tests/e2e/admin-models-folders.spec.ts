import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson, mockAdminSocket } from './helpers';
import { clickAddFolder, gotoFolders, openFolderSettings, openRowMenu, selectFolderView, sidebarRow } from './model-folders-nav';

const JOURNEY = 'admin-models-folders';

type Obj = Record<string, any>;

function binding(type: string, subdir: string, over: Obj = {}): Obj {
	return {
		model_type: type,
		folder: subdir,
		subdir,
		path: `/data/depot/models/${subdir}`,
		exists: true,
		position: 0,
		is_write: false,
		scan_headers: false,
		indexed_files: 10,
		size_bytes: 1_200_000_000,
		unindexed: 0,
		...over
	};
}

function rootOf(id: string, over: Obj = {}): Obj {
	return {
		id,
		label: id,
		path: `/data/${id}`,
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: new Date().toISOString(),
		bindings: [],
		...over
	};
}

const HOME = rootOf('home', {
	label: 'PotionUI',
	path: '/home/me/potionui/models',
	kind: 'home',
	bindings: [
		binding('checkpoint', 'checkpoints', { is_write: true, indexed_files: 14, unindexed: 6, size_bytes: 71_400_000_000 }),
		binding('lora', 'loras', { is_write: true, indexed_files: 212, size_bytes: 18_200_000_000 }),
		binding('vae', 'vae', { is_write: true, indexed_files: 6, size_bytes: 1_100_000_000 })
	]
});

const DEPOT = rootOf('depot', {
	label: 'Depot (SSD 2)',
	path: '/data/depot/models',
	bindings: [
		binding('checkpoint', 'checkpoints', { indexed_files: 300, size_bytes: 600_000_000_000, scan_headers: true }),
		binding('lora', 'loras/flux', { indexed_files: 120, size_bytes: 12_000_000_000 }),
		binding('controlnet', 'controlnet', { exists: false, indexed_files: 0, size_bytes: 0 })
	]
});

const NAS = rootOf('nas', {
	label: 'NAS',
	path: '/data/nas/models',
	state: 'offline',
	state_reason: 'The path does not exist.',
	bindings: [binding('checkpoint', 'checkpoints', { indexed_files: 40 })]
});

const LONG = rootOf('long', {
	label: 'Very long folder name for the archive',
	path: '/data/very/long/path/that/keeps/going/and/going/until/it/must/be/cut/off/models',
	bindings: [binding('lora', 'sub/folders/that/nest/very/deeply/and/keep/on/going/forever/loras', { indexed_files: 3 })]
});

function overview(roots: Obj[], over: Obj = {}): Obj {
	const order = new Map<string, string[]>();
	for (const r of roots) for (const b of r.bindings) order.set(b.model_type, [...(order.get(b.model_type) ?? []), r.id]);
	return {
		roots,
		types: [...order.entries()].map(([model_type, ids]) => ({
			model_type,
			folder: model_type,
			order: ids,
			write_root_id: roots.find((r) => r.bindings.some((b: Obj) => b.model_type === model_type && b.is_write))?.id ?? null
		})),
		unplaced: [],
		indexing: { state: 'idle' },
		server_os: 'Linux',
		path_style: 'posix',
		...over
	};
}

interface Log {
	writes: Obj[];
	orders: Obj[];
	patches: Obj[];
	probes: string[];
	deletes: string[];
}

async function mockApi(page: Page, data: Obj): Promise<Log> {
	const log: Log = { writes: [], orders: [], patches: [], probes: [], deletes: [] };
	await page.route('**/api/models/roots', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return fulfillJson(route, { success: true, data });
	});
	await page.route('**/api/models/roots/order', (route) => {
		log.orders.push(route.request().postDataJSON());
		return fulfillJson(route, { success: true, data });
	});
	await page.route('**/api/models/roots/write', (route) => {
		log.writes.push(route.request().postDataJSON());
		return fulfillJson(route, { success: true, data: data.roots[0] });
	});
	await page.route('**/api/models/roots/*/probe', (route) => {
		log.probes.push(route.request().url().split('/').slice(-2)[0]);
		return fulfillJson(route, { success: true, data: data.roots[0] });
	});
	await page.route(/\/api\/models\/roots\/(?!order$|write$|detect$)[^/]+$/, (route) => {
		const method = route.request().method();
		if (method === 'PATCH') {
			log.patches.push(route.request().postDataJSON());
			return fulfillJson(route, { success: true, data: data.roots[0] });
		}
		if (method === 'DELETE') {
			log.deletes.push(route.request().url().split('/').pop()!);
			return fulfillJson(route, { success: true, data: { deleted: true } });
		}
		return route.continue();
	});
	await page.route('**/api/models/roots/detect', (route) =>
		fulfillJson(route, {
			success: true,
			data: {
				path: '/data/external/models',
				effective_path: '/data/external/models',
				state: 'online',
				writable_hint: true,
				case_insensitive: false,
				layout: 'single',
				suggestions: [],
				single_type_guess: 'lora',
				conflicts: [],
				warnings: []
			}
		})
	);
	await page.route('**/api/models/layouts', (route) => fulfillJson(route, { layouts: [], load_errors: {} }));
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: { state: 'idle' } })
	);
	await mockAdminSocket(page, () => ({ state: 'idle' }));
	return log;
}

async function expectNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
	expect(overflow).toBeLessThanOrEqual(0);
}

test.describe('admin models folders - desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('sidebar lists folders with counts and problem badges, the table sums indexed and pending files', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME, DEPOT, NAS], { unplaced: [{ dir: '/data/old/models', count: 3, types: ['lora'] }] }));
		await gotoFolders(page);

		await expect(sidebarRow(page, 'PotionUI')).toContainText('238');
		await expect(sidebarRow(page, 'Depot')).toContainText('1');
		await expect(sidebarRow(page, 'NAS')).toContainText('1');
		await expect(sidebarRow(page, 'Found outside folders')).toContainText('1');
		await expect(sidebarRow(page, 'Downloads & priority')).toBeVisible();
		await expect(sidebarRow(page, 'Indexing')).toBeVisible();
		await expect(sidebarRow(page, 'Add folder')).toBeVisible();

		await expect(page.getByRole('heading', { level: 2, name: 'PotionUI' })).toBeVisible();
		await expect(page.locator('[data-root-stats]')).toContainText('238 files');
		await expect(page.locator('[data-root-stats]')).toContainText('6 not indexed');
		const checkpointRow = page.locator('[data-binding-row]').filter({ hasText: 'checkpoints' });
		await expect(checkpointRow.locator('[data-cell="files"]')).toHaveText('20');
		await expect(checkpointRow).toContainText('Downloads');
		await expect(checkpointRow).toContainText('6 pending');
		await screenshot(page, JOURNEY, 'desktop-normal');

		await sidebarRow(page, 'Depot').click();
		const missing = page.locator('[data-binding-row]').filter({ hasText: 'controlnet' });
		await expect(missing).toContainText('Missing');
		await expect(page.locator('[data-binding-row]').filter({ hasText: /^Base model/ })).toContainText('Header');
		await screenshot(page, JOURNEY, 'desktop-depot-missing');

		await sidebarRow(page, 'NAS').click();
		await expect(page.getByText('NAS is offline')).toBeVisible();
		await expect(page.getByText('The path does not exist.').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-error-root');
	});

	test('row details open the side panel and Use for downloads sends the write target', async ({ page }) => {
		await loginAsOwner(page);
		const log = await mockApi(page, overview([HOME, DEPOT]));
		await gotoFolders(page, 'Depot');
		await page.locator('[data-binding-row]').filter({ hasText: 'loras/flux' }).click();
		const panel = page.locator('[data-side-panel]');
		await expect(panel).toBeVisible();
		await expect(panel).toContainText('/data/depot/models/loras/flux');
		await expect(panel).toContainText('Downloads folder');
		await screenshot(page, JOURNEY, 'desktop-side-panel-binding');
		await panel.getByRole('button', { name: 'Close details' }).click();
		await expect(panel).toBeHidden();

		await openRowMenu(page, 'loras/flux', 'Use for downloads');
		await expect.poll(() => log.writes.length).toBe(1);
		expect(log.writes[0]).toEqual({ root_id: 'depot', model_type: 'lora', subdir: 'loras/flux' });
	});

	test('Detect type from file and Remove from the row menu call the folder APIs', async ({ page }) => {
		await loginAsOwner(page);
		const log = await mockApi(page, overview([HOME, DEPOT]));
		let scan: Obj | null = null;
		await page.route('**/api/models/roots/depot/bindings', (route) => {
			scan = route.request().postDataJSON();
			return fulfillJson(route, { success: true, data: DEPOT });
		});
		await gotoFolders(page, 'Depot');
		await openRowMenu(page, 'checkpoints', 'Stop detecting type from file');
		await expect.poll(() => scan).toEqual({ model_type: 'checkpoint', subdir: 'checkpoints', scan_headers: false });

		await openRowMenu(page, 'loras/flux', 'Remove from folder');
		await expect(page.getByText(/become unavailable until you point that type/)).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-remove-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => log.patches.length).toBe(1);
		expect(log.patches[0]).toEqual({ remove_bindings: [{ model_type: 'lora', subdir: 'loras/flux' }] });
	});

	test('Folder settings: rename, check now, detect again and delete with confirmation', async ({ page }) => {
		await loginAsOwner(page);
		const log = await mockApi(page, overview([HOME, DEPOT]));
		await gotoFolders(page, 'Depot');
		await openFolderSettings(page);
		const panel = page.locator('[data-side-panel]');
		await expect(panel.getByRole('button', { name: /Save changes/ })).toHaveCount(0);
		await panel.getByLabel('Name').fill('Archive');
		await panel.getByRole('button', { name: /Save changes/ }).click();
		await expect.poll(() => log.patches.length).toBe(1);
		expect(log.patches[0]).toEqual({ label: 'Archive' });
		await screenshot(page, JOURNEY, 'desktop-side-panel-root');

		await panel.getByRole('button', { name: 'Check now' }).click();
		await expect.poll(() => log.probes).toContain('depot');

		await panel.getByRole('button', { name: 'Delete folder' }).click();
		await expect(page.getByText(/become unavailable, not deleted/)).toBeVisible();
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => log.deletes).toEqual(['depot']);
	});

	test('Downloads & priority reorders folders for a type', async ({ page }) => {
		await loginAsOwner(page);
		const log = await mockApi(page, overview([HOME, DEPOT]));
		await gotoFolders(page, 'Downloads & priority');
		await expect(page.locator('[data-routing]')).toContainText('Existing files never move.');
		await screenshot(page, JOURNEY, 'desktop-routing');
		await page.getByRole('button', { name: 'Move PotionUI later for Base model' }).click();
		await expect.poll(() => log.orders.length).toBe(1);
		expect(log.orders[0]).toMatchObject({ root_ids: ['depot', 'home'], model_type: 'checkpoint' });
	});

	test('Found outside folders opens the add dialog for the suggested path', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME], { unplaced: [{ dir: '/data/external/models', count: 8, types: ['lora'] }] }));
		await gotoFolders(page, 'Found outside folders');
		await screenshot(page, JOURNEY, 'desktop-found-outside');
		await page.getByRole('button', { name: 'Add as folder' }).click();
		await expect(page.getByRole('dialog').getByText('Add a model folder')).toBeVisible({ timeout: 10000 });
	});

	test('Add folder in the sidebar opens the dialog', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME]));
		await gotoFolders(page);
		await clickAddFolder(page);
		await expect(page.getByRole('dialog').getByText('Add a model folder')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'desktop-add-folder');
	});

	test('Indexing is its own view', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME]));
		const status = { state: 'indexing', processed: 900, total: 1200 };
		await page.route('**/api/models/indexing/status', (route) => fulfillJson(route, { success: true, data: status }));
		await mockAdminSocket(page, () => status);
		await gotoFolders(page, 'Indexing');
		await expect(page.getByText('Model indexing', { exact: true })).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'desktop-indexing');
	});

	test('fresh install: only the built-in folder with empty counts', async ({ page }) => {
		await loginAsOwner(page);
		const fresh = rootOf('home', {
			label: 'PotionUI',
			path: '/home/me/potionui/models',
			kind: 'home',
			bindings: [binding('checkpoint', 'checkpoints', { is_write: true, indexed_files: 0, size_bytes: 0 })]
		});
		await mockApi(page, overview([fresh]));
		await gotoFolders(page);
		await expect(page.locator('[data-root-stats]')).toContainText('0 files');
		await expect(sidebarRow(page, 'Found outside folders')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-fresh');
	});

	test('long paths are cut with an ellipsis and the full path shows in a tooltip', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME, LONG]));
		await gotoFolders(page, 'Very long');
		await expectNoHorizontalOverflow(page);
		await page.locator('[data-binding-row]').first().getByText('sub/folders').filter({ visible: true }).first().hover();
		await expect(page.getByText(LONG.bindings[0].path)).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-long');
	});
});

test.describe('admin models folders - phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('Folder select switches the pane and Back returns to Models', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME, DEPOT, NAS]));
		await gotoFolders(page);
		await expect(page.getByLabel('Folder', { exact: true })).toBeVisible();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-normal');

		await selectFolderView(page, 'Depot');
		await expect(page.getByRole('heading', { level: 2, name: 'Depot (SSD 2)' })).toBeVisible();
		await screenshot(page, JOURNEY, 'phone-depot');

		await selectFolderView(page, 'NAS');
		await expect(page.getByText('NAS is offline')).toBeVisible();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-error-root');

		await selectFolderView(page, 'Downloads & priority');
		await expect(page.locator('[data-routing]')).toBeVisible();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-routing');

		await page.locator('button[data-detail-back]').click();
		await expect(page).not.toHaveURL(/view=folders/);
	});

	test('row details open as a bottom sheet', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME]));
		await gotoFolders(page);
		await page.locator('[data-binding-row]').filter({ hasText: 'vae' }).click();
		const sheet = page.locator('[data-side-panel]');
		await expect(sheet).toBeVisible();
		const box = await sheet.boundingBox();
		expect(box!.y + box!.height).toBeGreaterThan(800);
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-sheet');
	});

	test('Add folder icon next to the select opens the dialog', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([HOME]));
		await gotoFolders(page);
		await clickAddFolder(page);
		await expect(page.getByRole('dialog').getByText('Add a model folder')).toBeVisible({ timeout: 10000 });
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-add-folder');
	});

	test('fresh install and long paths fit a phone', async ({ page }) => {
		await loginAsOwner(page);
		await mockApi(page, overview([rootOf('home', { label: 'PotionUI', kind: 'home', path: '/home/me/models', bindings: [binding('lora', 'loras', { indexed_files: 0, size_bytes: 0 })] }), LONG]));
		await gotoFolders(page);
		await screenshot(page, JOURNEY, 'phone-fresh');
		await selectFolderView(page, 'Very long');
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'phone-long');
	});
});
