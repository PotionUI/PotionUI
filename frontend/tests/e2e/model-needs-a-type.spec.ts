import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-needs-a-type';

const SHA_A = 'a'.repeat(64);
const SHA_B = 'b'.repeat(64);

type MockModel = {
	id: string;
	filename: string;
	model_type: string;
	sha256: string | null;
	type_source: string;
	family?: string | null;
};

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

function listItem(m: MockModel) {
	return {
		id: m.id,
		filename: m.filename,
		name: null,
		model_type: m.model_type,
		file_size: 6_400_000_000,
		sha256: m.sha256,
		tags: [],
		backend_ids: [],
		created_at: '2026-09-29T10:00:00Z'
	};
}

function detail(m: MockModel) {
	return {
		...listItem(m),
		description: '',
		prompting_guidance: null,
		files: [],
		providers: [],
		location: null,
		copies: 1,
		is_directory: false,
		indexed_at: '2026-09-29T10:00:00Z',
		updated_at: '2026-09-29T10:00:00Z',
		type_info: {
			source: m.type_source,
			folder_type: 'checkpoint',
			family: m.family ?? null,
			variant: null,
			classifier: null,
			components: [],
			verdict_status: m.family || m.model_type !== 'undefined' ? 'classified' : 'undecided',
			packaging: null
		}
	};
}

async function mockModels(page: Page, models: MockModel[], conflictOnSet = false) {
	const puts: unknown[] = [];
	await page.route(/\/api\/models(\/|\?|$)/, async (route) => {
		const url = new URL(route.request().url());
		const method = route.request().method();
		const path = url.pathname;
		if (path === '/api/models/types' && method === 'GET') {
			const counts = new Map<string, number>();
			for (const m of models) counts.set(m.model_type, (counts.get(m.model_type) ?? 0) + 1);
			const types = [...counts.entries()].map(([type, count]) => ({
				type,
				directory: type === 'undefined' ? null : `/models/${type}`,
				count,
				subdirectories: []
			}));
			return fulfillJson(route, { success: true, data: { types, total: models.length } });
		}
		if (path === '/api/models' && method === 'GET') {
			const wanted = url.searchParams.get('model_type');
			const rows = models.filter((m) => !wanted || m.model_type === wanted).map(listItem);
			return fulfillJson(route, {
				success: true,
				data: { models: rows, total: rows.length, availability_indexed: true }
			});
		}
		const typeMatch = path.match(/^\/api\/models\/([^/]+)\/type$/);
		if (typeMatch) {
			const model = models.find((m) => m.id === typeMatch[1]);
			if (!model) return fulfillJson(route, { success: false }, 404);
			if (method === 'PUT') {
				const body = route.request().postDataJSON();
				puts.push(body);
				if (conflictOnSet) {
					return fulfillJson(
						route,
						{
							detail: {
								error: 'model_type_conflict',
								message: `Another model named '${model.filename}' already exists as ${body.model_type}`
							}
						},
						409
					);
				}
				model.model_type = body.model_type;
				model.type_source = 'admin';
			} else if (method === 'DELETE') {
				model.model_type = 'checkpoint';
				model.type_source = 'folder';
			}
			return fulfillJson(route, { success: true, data: { model: detail(model) } });
		}
		const detailMatch = path.match(/^\/api\/models\/([^/]+)$/);
		if (detailMatch && method === 'GET') {
			const model = models.find((m) => m.id === detailMatch[1]);
			if (model) return fulfillJson(route, { success: true, data: { model: detail(model) } });
		}
		const subMatch = path.match(/^\/api\/models\/([^/]+)\/(availability|previews)$/);
		if (subMatch && models.some((m) => m.id === subMatch[1])) {
			const data = subMatch[2] === 'previews' ? { previews: [] } : { availability: [] };
			return fulfillJson(route, { success: true, data });
		}
		return route.fallback();
	});
	return puts;
}

function seedModels(): MockModel[] {
	return [
		{ id: 'm1', filename: 'mystery-flux-a.safetensors', model_type: 'undefined', sha256: SHA_A, type_source: 'header' },
		{ id: 'm2', filename: 'mystery-b.safetensors', model_type: 'undefined', sha256: SHA_B, type_source: 'header' },
		{
			id: 'm3',
			filename: 'flux1-dev.safetensors',
			model_type: 'diffusion_model',
			sha256: 'c'.repeat(64),
			type_source: 'header',
			family: 'flux'
		},
		{ id: 'm4', filename: 'sdxl-base.safetensors', model_type: 'checkpoint', sha256: 'd'.repeat(64), type_source: 'folder' }
	];
}

function visibleText(page: Page, text: string) {
	return page.getByText(text).filter({ visible: true }).first();
}

async function gotoModels(page: Page) {
	await page.goto('/admin?tab=models');
	await expect(page.getByText('By type')).toBeVisible({ timeout: 15000 });
}

test.describe('needs a type - desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('sidebar row is first with a warning count and filters to undefined models', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels());
		await gotoModels(page);
		const needs = page.getByRole('option', { name: /Needs a type/ });
		await expect(needs).toBeVisible();
		await expect(needs).toContainText('2');
		const labels = await page.locator('[data-pane-row]').allInnerTexts();
		const typeLabels = labels.filter((l) => /Needs a type|Base model|Diffusion model/.test(l));
		expect(typeLabels[0]).toContain('Needs a type');
		await screenshot(page, JOURNEY, 'desktop-all-models');
		await needs.click();
		await expect(visibleText(page, 'mystery-flux-a.safetensors')).toBeVisible();
		await expect(page.getByText('flux1-dev.safetensors')).toHaveCount(0);
		await expect(page.getByText('UNDEFINED')).toHaveCount(0);
		await expect(page.getByText('Needs a type').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-needs-a-type-list');
	});

	test('the row is hidden when no model needs a type', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels().filter((m) => m.model_type !== 'undefined'));
		await gotoModels(page);
		await expect(page.getByRole('option', { name: /Base model/ })).toBeVisible();
		await expect(page.getByRole('option', { name: /Needs a type/ })).toHaveCount(0);
	});

	test('changing the type on the detail page removes the model from the filter', async ({ page }) => {
		await loginAsOwner(page);
		const puts = await mockModels(page, seedModels());
		await gotoModels(page);
		await page.getByRole('option', { name: /Needs a type/ }).click();
		await visibleText(page, 'mystery-flux-a.safetensors').click();
		const select = page.locator('#model-type');
		await expect(select).toBeVisible({ timeout: 15000 });
		await expect(select).toHaveValue('undefined');
		await expect(page.getByText("The file header didn't match any known model. Choose its type.")).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-detail-needs-a-type');

		await select.selectOption('diffusion_model');
		await expect(page.getByText('1 unsaved change')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-detail-type-staged');
		await page.getByRole('button', { name: 'Save', exact: true }).click();
		await expect(page.getByText('Set by an admin')).toBeVisible({ timeout: 10000 });
		expect(puts).toEqual([{ model_type: 'diffusion_model' }]);
		await expect(page.getByRole('button', { name: 'Reset to automatic' })).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-detail-type-saved');

		await page.locator('[data-detail-back]').click();
		await expect(visibleText(page, 'mystery-b.safetensors')).toBeVisible({ timeout: 10000 });
		await expect(page.getByText('mystery-flux-a.safetensors')).toHaveCount(0);
		await expect(page.getByRole('option', { name: /Needs a type/ })).toContainText('1');
		await screenshot(page, JOURNEY, 'desktop-list-after-change');
	});

	test('reset to automatic stages and saves a DELETE', async ({ page }) => {
		await loginAsOwner(page);
		const models = seedModels();
		models[0].model_type = 'lora';
		models[0].type_source = 'admin';
		await mockModels(page, models);
		let deleted = false;
		page.on('request', (req) => {
			if (req.method() === 'DELETE' && /\/api\/models\/m1\/type$/.test(req.url())) deleted = true;
		});
		await page.goto('/admin?tab=models&id=m1');
		const select = page.locator('#model-type');
		await expect(select).toHaveValue('lora', { timeout: 15000 });
		await expect(page.getByText('Set by an admin')).toBeVisible();
		await page.getByRole('button', { name: 'Reset to automatic' }).click();
		await expect(select).toHaveValue('automatic');
		await page.getByRole('button', { name: 'Save', exact: true }).click();
		await expect.poll(() => deleted).toBe(true);
		await expect(page.getByText('From the folder it is in')).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole('button', { name: 'Reset to automatic' })).toHaveCount(0);
	});

	test('a header verdict shows the family line', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels());
		await page.goto('/admin?tab=models&id=m3');
		await expect(page.getByText('Detected from the file: Flux')).toBeVisible({ timeout: 15000 });
		await expect(page.locator('#model-type')).toHaveValue('diffusion_model');
	});

	test('a 409 shows the conflict inline and keeps the change staged', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels(), true);
		await page.goto('/admin?tab=models&id=m1');
		const select = page.locator('#model-type');
		await expect(select).toBeVisible({ timeout: 15000 });
		await select.selectOption('checkpoint');
		await page.getByRole('button', { name: 'Save', exact: true }).click();
		await expect(page.getByText(/already exists as checkpoint/)).toBeVisible({ timeout: 10000 });
		await expect(select).toHaveValue('checkpoint');
		await expect(page.getByText('1 unsaved change')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-detail-conflict');
	});

	test('a model without a hash cannot be retyped', async ({ page }) => {
		await loginAsOwner(page);
		const models = seedModels();
		models[0].sha256 = null;
		await mockModels(page, models);
		await page.goto('/admin?tab=models&id=m1');
		const select = page.locator('#model-type');
		await expect(select).toBeVisible({ timeout: 15000 });
		await expect(select).toBeDisabled();
		await select.locator('xpath=..').hover({ force: true });
		await expect(page.getByText('no content hash yet')).toBeVisible({ timeout: 5000 });
		await page.waitForTimeout(600);
		await screenshot(page, JOURNEY, 'desktop-detail-no-hash');
	});

	test('download dialog explains that the type is read from the file', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels());
		await page.route(/\/api\/models\/types/, (route) =>
			route.fulfill({
				json: {
					success: true,
					data: {
						types: [
							{ type: 'lora', directory: '/models/loras', count: 3, subdirectories: [] },
							{ type: 'checkpoint', directory: '/models/checkpoints', count: 12, subdirectories: [] }
						]
					}
				}
			})
		);
		await page.goto('/admin?tab=downloads');
		await page.getByRole('button', { name: /Add (Your First )?Download/i }).first().click();
		await expect(page.locator('#url')).toBeVisible();
		const hint = page.getByText('The model type is read from the file after download.');
		await expect(hint).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-download-hint');
		await page.getByRole('button', { name: /^lora/i }).click();
		await expect(hint).toHaveCount(0);
	});
});

function rootWithScan(scanHeaders: boolean) {
	return {
		id: 'lib1',
		label: 'Forge models',
		path: '/srv/forge/models',
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [
			{
				model_type: 'checkpoint',
				folder: 'Stable-diffusion',
				subdir: 'Stable-diffusion',
				path: '/srv/forge/models/Stable-diffusion',
				exists: true,
				position: 0,
				is_write: false,
				indexed_files: 12,
				size_bytes: 12_000_000_000,
				unindexed: 0,
				scan_headers: scanHeaders
			},
			{
				model_type: 'lora',
				folder: 'Lora',
				subdir: 'Lora',
				path: '/srv/forge/models/Lora',
				exists: true,
				position: 0,
				is_write: false,
				indexed_files: 30,
				size_bytes: 3_000_000_000,
				unindexed: 0,
				scan_headers: false
			}
		]
	};
}

async function mockFolders(page: Page, opts: { failPatch?: boolean } = {}) {
	let scan = false;
	const patches: unknown[] = [];
	await page.route('**/api/models/roots', (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		return fulfillJson(route, {
			success: true,
			data: {
				roots: [rootWithScan(scan)],
				types: [],
				unplaced: [],
				indexing: { state: 'idle' },
				server_os: 'Linux',
				path_style: 'posix'
			}
		});
	});
	await page.route('**/api/models/roots/lib1/bindings', (route) => {
		patches.push(route.request().postDataJSON());
		if (opts.failPatch) {
			return fulfillJson(
				route,
				{ detail: { error: 'model_roots_invalid_binding', message: 'Header detection is not available for this folder.' } },
				422
			);
		}
		scan = route.request().postDataJSON().scan_headers;
		return fulfillJson(route, { success: true, data: rootWithScan(scan) });
	});
	return patches;
}

async function openFolder(page: Page) {
	await page.goto('/admin?tab=models&view=folders');
	await expect(page.getByRole('heading', { name: 'Folders', level: 2 })).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: /^Forge models/ }).click();
	await expect(page.getByText('Stable-diffusion', { exact: true })).toBeVisible({ timeout: 10000 });
}

test.describe('detect type from file - folders', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the switch shows only on checkpoint-like bindings and patches by type and subdir', async ({ page }) => {
		await loginAsOwner(page);
		const patches = await mockFolders(page);
		await openFolder(page);
		const toggles = page.getByRole('switch', { name: 'Detect type from file' });
		await expect(toggles).toHaveCount(1);
		await expect(toggles).not.toBeChecked();
		await screenshot(page, JOURNEY, 'desktop-folders-switch-off');

		await page.getByText('Detect type from file', { exact: true }).hover();
		await expect(page.getByText(/Pickle files keep the folder's type/)).toBeVisible({ timeout: 5000 });
		await page.waitForTimeout(600);
		await screenshot(page, JOURNEY, 'desktop-folders-switch-tooltip');

		await toggles.click({ force: true });
		await expect(toggles).toBeChecked({ timeout: 10000 });
		expect(patches).toEqual([{ model_type: 'checkpoint', subdir: 'Stable-diffusion', scan_headers: true }]);
		await screenshot(page, JOURNEY, 'desktop-folders-switch-on');
	});

	test('a failed change shows the error and puts the switch back', async ({ page }) => {
		await loginAsOwner(page);
		await mockFolders(page, { failPatch: true });
		await openFolder(page);
		const toggle = page.getByRole('switch', { name: 'Detect type from file' });
		await toggle.click({ force: true });
		await expect(page.getByText('Header detection is not available for this folder.')).toBeVisible({ timeout: 10000 });
		await expect(toggle).not.toBeChecked();
		await screenshot(page, JOURNEY, 'desktop-folders-switch-error');
	});
});

test.describe('needs a type - mobile', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('list, detail and folders fit a phone', async ({ page }) => {
		await loginAsOwner(page);
		await mockModels(page, seedModels());
		await mockFolders(page);
		await page.goto('/admin?tab=models&view=undefined');
		await expect(visibleText(page, 'mystery-flux-a.safetensors')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'mobile-needs-a-type-list');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

		await page.goto('/admin?tab=models&id=m1');
		await expect(page.locator('#model-type')).toBeVisible({ timeout: 15000 });
		await page.waitForTimeout(800);
		await page.locator('#model-type').scrollIntoViewIfNeeded();
		await screenshot(page, JOURNEY, 'mobile-detail-type');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

		await openFolder(page);
		await screenshot(page, JOURNEY, 'mobile-folders-switch');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	});
});
