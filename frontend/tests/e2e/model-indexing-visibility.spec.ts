import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-indexing-visibility';

async function fulfillJson(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
}

function locationConfig(overrides: Record<string, unknown> = {}) {
	return {
		external_path: '/mnt/storage/models',
		overrides: {},
		directories: [
			{
				directory: 'checkpoints',
				target: null,
				linked: true,
				resolved_target: '/mnt/storage/models/checkpoints',
				has_real_files: true
			},
			{
				directory: 'loras',
				target: null,
				linked: true,
				resolved_target: '/mnt/storage/models/loras',
				has_real_files: true
			},
			{
				directory: 'vae',
				target: null,
				linked: true,
				resolved_target: '/mnt/storage/models/vae',
				has_real_files: false
			}
		],
		windows_unsupported: false,
		...overrides
	};
}

function readinessReport(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		overall: 'not_ready',
		checks: [
			{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: 'Service is healthy.', action: null },
			{ area: 'execution', status: 'ready', code: 'EXECUTION_READY', message: 'A backend is ready.', action: null },
			{
				area: 'content',
				status: 'not_ready',
				code: 'NO_PRESETS_ASSIGNED',
				message: "You don't have any presets yet. Ask your administrator to assign one.",
				action: null
			},
			{
				area: 'generation_proven',
				status: 'not_ready',
				code: 'NEVER_GENERATED',
				message: 'Nothing has been generated yet.',
				action: null
			}
		],
		...overrides
	};
}

async function mockModelsLocation(page: Page, config: Record<string, unknown>) {
	await page.route('**/api/models/location', (route) => fulfillJson(route, { success: true, data: config }));
}

async function mockIndexingStatus(page: Page, status: Record<string, unknown>) {
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: status })
	);
}

async function mockReadiness(page: Page, report: Record<string, unknown>) {
	await page.route('**/api/readiness', (route) => fulfillJson(route, report));
}

async function gotoSetup(page: Page) {
	await page.goto('/setup');
	await expect(page.getByText('Models location')).toBeVisible({ timeout: 15000 });
}

async function gotoAdminStorage(page: Page) {
	await page.goto('/admin?tab=settings&view=storage');
	const heading = page.getByRole('heading', { name: 'Models Location' });
	await expect(heading).toBeVisible({ timeout: 15000 });
	await heading.scrollIntoViewIfNeeded();
}

test.describe('model indexing visibility - wizard', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('scanning', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'scanning', scanned_roots: ['/mnt/storage/models'] });
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('Scanning /mnt/storage/models')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-scanning');
	});

	test('indexing with progress', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'indexing', processed: 412, total: 1637 });
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('412 / 1637')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-indexing-progress');
	});

	test('restart pending', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'indexing',
			processed: 80,
			total: 200,
			restart_pending: true
		});
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('restarting the scan once this pass finishes')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-restart-pending');
	});

	test('done', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'done', indexed: 128, found_on_disk: 128 });
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('models found on disk')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-done');
	});

	test('zero found with auto-matched and created-empty folders', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(
			page,
			locationConfig({ auto_matched: ['checkpoints'], created_empty: ['loras', 'vae'] })
		);
		await mockIndexingStatus(page, {
			state: 'done',
			found_on_disk: 0,
			scanned_roots: ['/mnt/storage/models']
		});
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('No model files found in')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-zero-found');
	});

	test('failed', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'failed',
			error: 'Ran out of disk space while hashing model files.'
		});
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('Ran out of disk space')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-failed');
	});

	test('blocked', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'blocked',
			error: 'The antivirus-scan plugin vetoed this run: quarantine directory is full.'
		});
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('antivirus-scan plugin vetoed')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-blocked');
	});

	test('failed-files list expanded with a long truncated path', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'done',
			indexed: 127,
			found_on_disk: 128,
			failed_files: [
				{
					path: '/mnt/storage/models/loras/very-long-subdirectory-name-for-testing-truncation/another-nested-folder/broken-checkpoint-file-with-a-really-long-descriptive-name.safetensors',
					error: 'Permission denied'
				}
			],
			failed_files_total: 1
		});
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		const toggle = page.getByRole('button', { name: /file failed/ });
		await expect(toggle).toBeVisible({ timeout: 10000 });
		await toggle.click();
		await page.getByText('broken-checkpoint-file').hover();
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, 'wizard-failed-files-expanded');
	});

	test('readiness row shows live indexing counts', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'indexing', processed: 412, total: 1637 });
		await mockReadiness(
			page,
			readinessReport({
				checks: [
					{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: 'Service is healthy.', action: null },
					{ area: 'execution', status: 'ready', code: 'EXECUTION_READY', message: 'A backend is ready.', action: null },
					{
						area: 'content',
						status: 'degraded',
						code: 'MODELS_INDEXING',
						message: 'Your models are still being set up (412/1637). Check back shortly.',
						action: null
					},
					{
						area: 'generation_proven',
						status: 'not_ready',
						code: 'NEVER_GENERATED',
						message: 'Nothing has been generated yet.',
						action: null
					}
				]
			})
		);
		await gotoSetup(page);
		await expect(page.getByText('indexing', { exact: true })).toBeVisible({ timeout: 10000 });
		await expect(page.getByText('Your models are still being set up (412/1637)')).toBeVisible();
		await screenshot(page, JOURNEY, 'readiness-row-indexing');
	});
});

test.describe('model indexing visibility - wizard phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('indexing with progress at phone width', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'indexing', processed: 412, total: 1637 });
		await mockReadiness(page, readinessReport());
		await gotoSetup(page);
		await expect(page.getByText('412 / 1637')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'wizard-indexing-progress-phone');
	});
});

test.describe('model indexing visibility - admin panel', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('indexing with progress', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, { state: 'indexing', processed: 900, total: 1200 });
		await gotoAdminStorage(page);
		await expect(page.getByText('900 / 1200')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-indexing-progress');
	});

	test('zero found with auto-matched and created-empty folders', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(
			page,
			locationConfig({ auto_matched: ['checkpoints'], created_empty: ['loras', 'vae'] })
		);
		await mockIndexingStatus(page, {
			state: 'done',
			found_on_disk: 0,
			scanned_roots: ['/mnt/storage/models']
		});
		await gotoAdminStorage(page);
		await expect(page.getByText('No model files found in')).toBeVisible({ timeout: 10000 });
		const createdEmptyLine = page.getByText('No matching folder found, created empty');
		await expect(createdEmptyLine).toBeVisible();
		await createdEmptyLine.scrollIntoViewIfNeeded();
		await screenshot(page, JOURNEY, 'admin-zero-found');
	});

	test('blocked', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'blocked',
			error: 'The antivirus-scan plugin vetoed this run: quarantine directory is full.'
		});
		await gotoAdminStorage(page);
		await expect(page.getByText('antivirus-scan plugin vetoed')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'admin-blocked');
	});

	test('failed-files list expanded with a long truncated path', async ({ page }) => {
		await loginAsOwner(page);
		await mockModelsLocation(page, locationConfig());
		await mockIndexingStatus(page, {
			state: 'done',
			indexed: 127,
			found_on_disk: 128,
			failed_files: [
				{
					path: '/mnt/storage/models/loras/very-long-subdirectory-name-for-testing-truncation/another-nested-folder/broken-checkpoint-file-with-a-really-long-descriptive-name.safetensors',
					error: 'Permission denied'
				}
			],
			failed_files_total: 1
		});
		await gotoAdminStorage(page);
		const toggle = page.getByRole('button', { name: /file failed/ });
		await expect(toggle).toBeVisible({ timeout: 10000 });
		await toggle.click();
		const failedRow = page.getByText('broken-checkpoint-file');
		await failedRow.scrollIntoViewIfNeeded();
		await failedRow.hover();
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, 'admin-failed-files-expanded');
	});
});
