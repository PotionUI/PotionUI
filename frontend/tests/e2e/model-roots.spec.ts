import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson, mockAdminSocket } from './helpers';
import { gotoFolders, openRowMenu, selectFolderView, sidebarRow } from './model-folders-nav';

const JOURNEY = 'model-roots';

function homeRoot(overrides: Record<string, unknown> = {}) {
	return {
		id: 'home',
		label: 'PotionUI models',
		path: 'models',
		kind: 'home',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [
			{
				model_type: 'lora',
				folder: 'loras',
				subdir: 'loras',
				path: 'models/loras',
				exists: true,
				position: 0,
				is_write: true,
				indexed_files: 4,
				size_bytes: 40000,
				unindexed: 0
			}
		],
		...overrides
	};
}

function libraryRoot(overrides: Record<string, unknown> = {}) {
	return {
		id: 'lib1',
		label: 'NAS',
		path: '/mnt/storage/models',
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [
			{
				model_type: 'checkpoint',
				folder: 'checkpoints',
				subdir: 'checkpoints',
				path: '/mnt/storage/models/checkpoints',
				exists: true,
				position: 0,
				is_write: false,
				indexed_files: 12,
				size_bytes: 12_000_000_000,
				unindexed: 0
			}
		],
		...overrides
	};
}

function overview(overrides: Record<string, unknown> = {}) {
	return {
		roots: [homeRoot()],
		types: [
			{ model_type: 'lora', folder: 'loras', order: ['home'], write_root_id: 'home' },
			{ model_type: 'checkpoint', folder: 'checkpoints', order: [], write_root_id: null }
		],
		unplaced: [],
		indexing: { state: 'idle' },
		server_os: 'Linux',
		path_style: 'posix',
		...overrides
	};
}

async function mockRootsGet(page: Page, data: Record<string, unknown>) {
	await page.route('**/api/models/roots', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return fulfillJson(route, { success: true, data });
	});
}

async function mockDetect(page: Page, data: Record<string, unknown>) {
	await page.route('**/api/models/roots/detect', (route) => fulfillJson(route, { success: true, data }));
}

async function mockIndexingStatus(page: Page, status: Record<string, unknown> = { state: 'idle' }) {
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: status })
	);
	await mockAdminSocket(page, () => status);
}

async function mockReadiness(page: Page) {
	await page.route('**/api/readiness', (route) =>
		fulfillJson(route, {
			overall: 'not_ready',
			checks: [
				{ area: 'content', status: 'not_ready', code: 'NO_PRESETS', message: 'No presets are installed.', action: null }
			]
		})
	);
}

async function expectNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
	expect(overflow).toBeLessThanOrEqual(0);
}

async function gotoSetup(page: Page) {
	await page.goto('/setup');
	await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Continue' }).click();
	await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
	await expect(page.locator('[data-models-row]')).toBeVisible();
}

async function gotoSetupExisting(page: Page) {
	await gotoSetup(page);
	await page.getByRole('button', { name: 'Use a folder you already have' }).click();
	await expect(page.getByLabel('Folder path')).toBeVisible();
}

async function gotoAdminFolders(page: Page, view?: string | RegExp) {
	await gotoFolders(page, view);
}

test.describe('model roots - setup wizard', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('detect -> create -> indexing starts', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await mockDetect(page, {
			path: '/mnt/storage/ComfyUI/models',
			effective_path: '/mnt/storage/ComfyUI/models',
			state: 'online',
			writable_hint: true,
			case_insensitive: false,
			layout: 'typed',
			suggestions: [
				{ model_type: 'checkpoint', subdir: 'checkpoints', matched_by: 'canonical', file_count: 12, file_count_truncated: false },
				{ model_type: 'lora', subdir: 'loras', matched_by: 'canonical', file_count: 30, file_count_truncated: true }
			],
			single_type_guess: null,
			conflicts: [],
			warnings: []
		});
		await page.route('**/api/models/roots', (route) => {
			if (route.request().method() !== 'POST') return route.continue();
			return fulfillJson(
				route,
				{ success: true, data: libraryRoot({ label: 'ComfyUI models', path: '/mnt/storage/ComfyUI/models' }) },
				201
			);
		});

		await gotoSetupExisting(page);
		await page.getByLabel('Folder path').fill('/mnt/storage/ComfyUI/models');
		await page.getByRole('button', { name: 'Detect' }).click();
		await expect(page.getByText('Detected folders')).toBeVisible({ timeout: 10000 });
		await expect(page.getByText('30+ files')).toBeVisible();
		await screenshot(page, JOURNEY, 'wizard-detected');

		await page.getByRole('button', { name: 'Add folder' }).click();
		await expect(page.getByText('Re-indexing in the background')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-added');
	});

	test('Start fresh keeps the built-in models folder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		let created = false;
		await page.route('**/api/models/roots', (route) => {
			if (route.request().method() === 'POST') created = true;
			return route.fallback();
		});
		await gotoSetup(page);
		await page.getByRole('button', { name: 'Change' }).click();
		await expect(page.getByRole('radio', { name: /Start fresh/ })).toHaveAttribute('aria-checked', 'true');
		await expect(page.getByText('Models will be saved to')).toBeVisible();
		await expect(page.locator('[data-panel="fresh"]').getByText('models', { exact: true })).toBeVisible();
		await expect(page.getByLabel('Folder path')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'wizard-start-fresh');
		await page.getByRole('button', { name: 'Keep this folder' }).click();
		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
		await expect(page.locator('[data-models-row]')).toBeVisible();
		expect(created).toBe(false);
		await screenshot(page, JOURNEY, 'wizard-start-fresh-continued');
	});

	test('Windows server shows a drive-letter placeholder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ server_os: 'Windows', path_style: 'windows' }));
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await gotoSetupExisting(page);
		await expect(page.getByPlaceholder('D:\\models')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-windows-placeholder');
	});

	test('mapped drive letter warns to use a UNC path', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ server_os: 'Windows', path_style: 'windows' }));
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await mockDetect(page, {
			path: 'Z:\\models',
			effective_path: 'Z:\\models',
			state: 'offline',
			writable_hint: false,
			case_insensitive: true,
			layout: 'empty',
			suggestions: [],
			single_type_guess: null,
			conflicts: [],
			warnings: [
				"Drive Z: is not visible to the PotionUI process; mapped drive letters are per-user sessions - if PotionUI runs as a service, use \\\\server\\share instead."
			]
		});

		await gotoSetupExisting(page);
		await page.getByLabel('Folder path').fill('Z:\\models');
		await page.getByRole('button', { name: 'Detect' }).click();
		await expect(page.getByText(/mapped drive letters are per-user sessions/)).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-unc-warning');
	});

	test('offline path is refused before any suggestions render', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await mockDetect(page, {
			path: '/mnt/data/offline',
			effective_path: '/mnt/data/offline',
			state: 'offline',
			writable_hint: false,
			case_insensitive: false,
			layout: 'empty',
			suggestions: [],
			single_type_guess: null,
			conflicts: [],
			warnings: []
		});

		await gotoSetupExisting(page);
		await page.getByLabel('Folder path').fill('/mnt/data/offline');
		await page.getByRole('button', { name: 'Detect' }).click();
		await expect(page.getByText("This path isn't reachable from the server.")).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-offline');
	});

	test('overlapping path is flagged as a conflict', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await mockDetect(page, {
			path: 'models/loras',
			effective_path: 'models/loras',
			state: 'online',
			writable_hint: true,
			case_insensitive: false,
			layout: 'single',
			suggestions: [],
			single_type_guess: 'lora',
			conflicts: [{ root_id: 'home', reason: 'overlaps models/loras' }],
			warnings: []
		});

		await gotoSetupExisting(page);
		await page.getByLabel('Folder path').fill('models/loras');
		await page.getByRole('button', { name: 'Detect' }).click();
		await expect(page.getByText(/Overlaps a folder that's already a model root/)).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-conflict');
	});
});

test.describe('model roots - admin folders', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('lists the home root and a library root with state badges', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ roots: [homeRoot(), libraryRoot()] }));
		await mockIndexingStatus(page);
		await gotoAdminFolders(page);
		await expect(sidebarRow(page, 'PotionUI models')).toBeVisible();
		await expect(sidebarRow(page, 'NAS')).toBeVisible();
		await expect(page.getByText('Built-in')).toBeVisible();
		await expect(page.getByText('Online').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'admin-list');
	});

	test('offline root shows an offline badge with the reason in a tooltip', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(
			page,
			overview({
				roots: [homeRoot(), libraryRoot({ state: 'offline', state_reason: 'the path does not exist' })]
			})
		);
		await mockIndexingStatus(page);
		await gotoAdminFolders(page, 'NAS');
		await expect(page.getByText('Offline', { exact: true })).toBeVisible();
		await expect(page.getByText('the path does not exist').first()).toBeVisible();
		await page.getByText('Offline', { exact: true }).hover();
		await screenshot(page, JOURNEY, 'admin-offline-root');
	});

	test('removing a type from a root asks for confirmation', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ roots: [homeRoot(), libraryRoot()] }));
		await mockIndexingStatus(page);
		let patchCalled = false;
		await page.route('**/api/models/roots/lib1', (route) => {
			if (route.request().method() !== 'PATCH') return route.continue();
			patchCalled = true;
			return fulfillJson(route, { success: true, data: libraryRoot({ bindings: [] }) });
		});

		await gotoAdminFolders(page, 'NAS');
		await expect(page.getByText('checkpoints', { exact: true }).filter({ visible: true })).toBeVisible({ timeout: 10000 });
		await openRowMenu(page, 'checkpoints', 'Remove from folder');
		await expect(page.getByText('Models under "checkpoints" in "NAS" become unavailable')).toBeVisible({
			timeout: 10000
		});
		await screenshot(page, JOURNEY, 'admin-remove-type-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => patchCalled).toBe(true);
	});

	test('deleting a root asks for confirmation and explains models are not deleted', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ roots: [homeRoot(), libraryRoot()] }));
		await mockIndexingStatus(page);
		let deleteCalled = false;
		await page.route('**/api/models/roots/lib1', (route) => {
			if (route.request().method() !== 'DELETE') return route.continue();
			deleteCalled = true;
			return fulfillJson(route, { success: true, data: { id: 'lib1', deleted: true } });
		});

		await gotoAdminFolders(page, 'NAS');
		await page.getByRole('button', { name: 'Folder settings' }).click();
		await page.getByRole('button', { name: 'Delete folder' }).click();
		await expect(page.getByText(/become unavailable, not deleted/)).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-delete-root-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => deleteCalled).toBe(true);
	});

	test('a read-only folder is never offered as the downloads folder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(
			page,
			overview({
				roots: [homeRoot(), libraryRoot({ read_only: true })],
				types: [
					{ model_type: 'checkpoint', folder: 'checkpoints', order: ['home', 'lib1'], write_root_id: 'home' }
				]
			})
		);
		await mockIndexingStatus(page);
		let writeCalled = false;
		await page.route('**/api/models/roots/write', (route) => {
			writeCalled = true;
			return route.continue();
		});

		await gotoAdminFolders(page, 'Downloads & priority');
		await expect(page.getByText('Existing files never move.')).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole('button', { name: 'Use for downloads' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'admin-write-refused');
		await selectFolderView(page, 'NAS');
		await page.getByRole('button', { name: 'Actions for checkpoints', exact: true }).click();
		await expect(page.getByRole('menuitem', { name: 'Details' })).toBeVisible();
		await expect(page.getByRole('menuitem', { name: 'Use for downloads' })).toHaveCount(0);
		expect(writeCalled).toBe(false);
	});

	test('suggested folder can be added as a folder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(
			page,
			overview({
				unplaced: [{ dir: '/mnt/external/checkpoints', count: 8, types: ['checkpoint'] }]
			})
		);
		await mockIndexingStatus(page);
		await mockDetect(page, {
			path: '/mnt/external/checkpoints',
			effective_path: '/mnt/external/checkpoints',
			state: 'online',
			writable_hint: true,
			case_insensitive: false,
			layout: 'single',
			suggestions: [],
			single_type_guess: 'checkpoint',
			conflicts: [],
			warnings: []
		});

		await gotoAdminFolders(page, 'Found outside folders');
		await expect(page.getByText('Suggested folders')).toBeVisible({ timeout: 10000 });
		await page.getByRole('button', { name: 'Add as folder' }).click();
		const modal = page.getByRole('dialog');
		await expect(modal.getByText('Add a model folder')).toBeVisible({ timeout: 10000 });
		await expect(modal.getByText('Base model')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-add-as-root-modal');
	});
});

test.describe('model roots - phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('wizard step at phone width', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await gotoSetup(page);
		await page.getByRole('button', { name: 'Change' }).click();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'wizard-phone-choice');
		await page.getByRole('radio', { name: /Use an existing folder/ }).click();
		await expect(page.getByLabel('Folder path')).toBeVisible();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'wizard-phone');
	});
});
