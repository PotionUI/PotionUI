import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-roots';

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

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
}

async function mockReadiness(page: Page) {
	await page.route('**/api/readiness', (route) =>
		fulfillJson(route, {
			overall: 'ready',
			checks: [{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: 'Service is healthy.', action: null }]
		})
	);
}

async function gotoSetup(page: Page) {
	await page.goto('/setup');
	await expect(page.getByText('Models location')).toBeVisible({ timeout: 15000 });
}

async function gotoAdminFolders(page: Page) {
	await page.goto('/admin?tab=models&view=folders');
	await expect(page.getByRole('heading', { name: 'Folders', level: 2 })).toBeVisible({ timeout: 15000 });
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

		await gotoSetup(page);
		await page.getByLabel('Folder path').fill('/mnt/storage/ComfyUI/models');
		await page.getByRole('button', { name: 'Detect' }).click();
		await expect(page.getByText('Detected types')).toBeVisible({ timeout: 10000 });
		await expect(page.getByText('30+ files')).toBeVisible();
		await screenshot(page, JOURNEY, 'wizard-detected');

		await page.getByRole('button', { name: 'Add folder' }).click();
		await expect(page.getByText('Re-indexing in the background')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-added');
	});

	test('skip keeps the built-in models folder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview());
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await gotoSetup(page);
		await page.getByRole('button', { name: 'Skip' }).click();
		await expect(page.getByText('models/ (default location in this install)')).toBeVisible();
		await screenshot(page, JOURNEY, 'wizard-skipped');
	});

	test('Windows server shows a drive-letter placeholder', async ({ page }) => {
		await loginAsOwner(page);
		await mockRootsGet(page, overview({ server_os: 'Windows', path_style: 'windows' }));
		await mockIndexingStatus(page);
		await mockReadiness(page);
		await gotoSetup(page);
		await expect(page.getByPlaceholder('D:\\ComfyUI\\models')).toBeVisible({ timeout: 10000 });
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

		await gotoSetup(page);
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

		await gotoSetup(page);
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

		await gotoSetup(page);
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
		await expect(page.getByRole('button', { name: /^PotionUI models/ })).toBeVisible();
		await expect(page.getByRole('button', { name: /^NAS/ })).toBeVisible();
		await expect(page.getByText('Built-in')).toBeVisible();
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
		await gotoAdminFolders(page);
		await expect(page.getByText('Offline')).toBeVisible();
		await page.getByText('Offline').hover();
		await page.waitForTimeout(300);
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

		await gotoAdminFolders(page);
		await page.getByRole('button', { name: /^NAS/ }).click();
		await expect(page.getByText('checkpoints', { exact: true })).toBeVisible({ timeout: 10000 });
		await page.getByRole('button', { name: 'Remove checkpoints from NAS' }).click();
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

		await gotoAdminFolders(page);
		await page.getByRole('button', { name: /^NAS/ }).click();
		await page.getByRole('button', { name: 'Delete' }).click();
		await expect(page.getByText(/become unavailable, not deleted/)).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-delete-root-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => deleteCalled).toBe(true);
	});

	test('setting a read-only root as the write target is disabled with a reason', async ({ page }) => {
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

		await gotoAdminFolders(page);
		await expect(page.getByText('Model type order')).toBeVisible({ timeout: 10000 });
		const setWriteButton = page.getByRole('button', { name: 'Set as write' });
		await expect(setWriteButton).toBeDisabled();
		await setWriteButton.hover();
		await expect(page.getByText('This folder is read-only')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-write-refused');
		await setWriteButton.click({ force: true });
		expect(writeCalled).toBe(false);
	});

	test('suggested folder can be added as a root', async ({ page }) => {
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

		await gotoAdminFolders(page);
		await expect(page.getByText('Suggested folders')).toBeVisible({ timeout: 10000 });
		await page.getByRole('button', { name: 'Add as root' }).click();
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
		await screenshot(page, JOURNEY, 'wizard-phone');
	});
});
