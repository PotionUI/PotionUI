import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-layout-folders-panel';

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

type Binding = { model_type: string; subdir: string; is_write: boolean; scan_headers?: boolean };

function bindingView(root: string, b: Binding) {
	const folder = b.model_type === 'lora' ? 'loras' : b.model_type === 'vae' ? 'vae' : 'checkpoints';
	return {
		model_type: b.model_type,
		folder,
		subdir: b.subdir,
		path: `${root}/${b.subdir}`,
		exists: true,
		position: 0,
		is_write: b.is_write,
		indexed_files: 7,
		size_bytes: 7_000_000,
		unindexed: 0,
		scan_headers: b.scan_headers ?? false
	};
}

async function setup(page: Page, options: { readOnly?: boolean; detectRootPath?: string } = {}) {
	const rootPath = '/srv/sm';
	const bindings: Binding[] = [
		{ model_type: 'checkpoint', subdir: 'Data/Models/StableDiffusion', is_write: true, scan_headers: true },
		{ model_type: 'lora', subdir: 'Data/Models/Lora', is_write: true },
		{ model_type: 'lora', subdir: 'Data/Models/LyCORIS', is_write: false }
	];
	const log = { patches: [] as any[], writes: [] as any[], detects: [] as any[] };

	const rootView = () => ({
		id: 'lib1',
		label: 'StabilityMatrix',
		path: rootPath,
		kind: 'library',
		read_only: options.readOnly ?? false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		layout_profile: 'stabilitymatrix',
		bindings: bindings.map((b) => bindingView(rootPath, b))
	});

	await page.route('**/api/models/roots', (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		return fulfillJson(route, {
			success: true,
			data: {
				roots: [rootView()],
				types: [],
				unplaced: [],
				indexing: { state: 'idle' },
				server_os: 'Linux',
				path_style: 'posix'
			}
		});
	});
	await page.route('**/api/models/roots/lib1', (route) => {
		if (route.request().method() !== 'PATCH') return route.fallback();
		const body = route.request().postDataJSON();
		log.patches.push(body);
		for (const ref of body.remove_bindings ?? []) {
			const i = bindings.findIndex((b) => b.model_type === ref.model_type && b.subdir === ref.subdir);
			if (i >= 0) bindings.splice(i, 1);
		}
		for (const b of body.bindings ?? []) bindings.push({ model_type: b.model_type, subdir: b.subdir, is_write: false });
		return fulfillJson(route, { success: true, data: rootView() });
	});
	await page.route('**/api/models/roots/write', (route) => {
		const body = route.request().postDataJSON();
		log.writes.push(body);
		for (const b of bindings) if (b.model_type === body.model_type) b.is_write = b.subdir === body.subdir;
		return fulfillJson(route, { success: true, data: rootView() });
	});
	await page.route('**/api/models/roots/detect', (route) => {
		log.detects.push(route.request().postDataJSON());
		const s = (model_type: string, subdir: string, label: string) => ({
			model_type,
			subdir,
			label,
			write: true,
			scan_headers: false,
			matched_by: 'profile',
			source: 'profile',
			file_count: 2,
			file_count_truncated: false
		});
		return fulfillJson(route, {
			success: true,
			data: {
				path: rootPath,
				root_path: options.detectRootPath ?? rootPath,
				effective_path: rootPath,
				state: 'online',
				writable_hint: true,
				case_insensitive: false,
				layout: 'typed',
				profile: null,
				alternatives: [],
				suggestions: [
					s('checkpoint', 'Data/Models/StableDiffusion', 'StableDiffusion'),
					s('lora', 'Data/Models/Lora', 'Lora'),
					s('lora', 'Data/Models/LyCORIS', 'LyCORIS'),
					s('vae', 'Data/Models/VAE', 'VAE')
				],
				outside_folders: [],
				extra_roots: [],
				delegated: [],
				single_type_guess: null,
				conflicts: [],
				warnings: []
			}
		});
	});
	await page.route('**/api/models/layouts', (route) =>
		fulfillJson(route, {
			layouts: [{ id: 'stabilitymatrix', label: 'StabilityMatrix', source: 'marketplace' }],
			load_errors: {}
		})
	);
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: { state: 'idle' } })
	);
	return log;
}

async function openRoot(page: Page) {
	await page.goto('/admin?tab=models&view=folders');
	await expect(page.getByRole('heading', { name: 'Folders', level: 2 })).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: /^StabilityMatrix/ }).click();
	await expect(page.getByText('LyCORIS', { exact: true })).toBeVisible({ timeout: 10000 });
}

test.describe('folders panel with layout profiles - desktop', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('shows the profile badge and groups subfolders by type', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page);
		await openRoot(page);
		await expect(page.locator('button[aria-expanded]').getByText('StabilityMatrix', { exact: true }).nth(1)).toBeVisible();
		await expect(page.getByText('LoRA', { exact: true })).toBeVisible();
		await expect(page.getByText('Lora', { exact: true })).toBeVisible();
		const row = (subdir: string) => page.locator('li').filter({ hasText: subdir }).last();
		await expect(row('Data/Models/Lora').getByText('Downloads', { exact: true })).toBeVisible();
		await expect(row('Data/Models/LyCORIS').getByText('Downloads', { exact: true })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-panel-grouped');
	});

	test('makes another subfolder the downloads folder', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Make LyCORIS the downloads folder' }).click();
		await expect.poll(() => log.writes.length).toBe(1);
		expect(log.writes[0]).toEqual({ model_type: 'lora', root_id: 'lib1', subdir: 'Data/Models/LyCORIS' });
		await expect(page.getByRole('button', { name: 'Make Lora the downloads folder' })).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole('button', { name: 'Make LyCORIS the downloads folder' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-panel-downloads-moved');
	});

	test('removes one subfolder by type and subdir', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Remove LyCORIS from StabilityMatrix' }).click();
		await expect(page.getByText('Models under "LyCORIS" in "StabilityMatrix" become unavailable')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'desktop-panel-remove-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => log.patches.length).toBe(1);
		expect(log.patches[0]).toEqual({ remove_bindings: [{ model_type: 'lora', subdir: 'Data/Models/LyCORIS' }] });
		await expect(page.getByText('LyCORIS', { exact: true })).toHaveCount(0, { timeout: 10000 });
		await expect(page.getByText('Lora', { exact: true })).toBeVisible();
	});

	test('detect again offers only what is missing and never removes', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Detect again' }).click();
		await expect(page.getByText('Found by detecting again')).toBeVisible({ timeout: 10000 });
		await expect(page.locator('input[id^="again-"]')).toHaveCount(1);
		await screenshot(page, JOURNEY, 'desktop-panel-detect-again');
		await page.getByRole('button', { name: 'Add selected' }).click();
		await expect.poll(() => log.patches.length).toBe(1);
		expect(log.patches[0].remove_bindings).toBeUndefined();
		expect(log.patches[0].remove_types).toBeUndefined();
		expect(log.patches[0].bindings).toEqual([
			{ model_type: 'vae', subdir: 'Data/Models/VAE', scan_headers: false, write: false }
		]);
		await expect(page.getByText('Found by detecting again')).toHaveCount(0, { timeout: 10000 });
		await expect(page.getByText('VAE', { exact: true }).first()).toBeVisible();
		await page.getByRole('button', { name: 'Detect again' }).click();
		await expect(page.getByText('Nothing new found')).toBeVisible({ timeout: 10000 });
	});
});

test.describe('folders panel edge cases', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('a read-only root offers no downloads folder button', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, { readOnly: true });
		await openRoot(page);
		await expect(page.getByRole('button', { name: /the downloads folder$/ })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Remove LyCORIS from StabilityMatrix' })).toBeVisible();
	});

	test('detect again on a root inside a larger install compares nothing', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, { detectRootPath: '/srv/sm/api/app.git' });
		await openRoot(page);
		await page.getByRole('button', { name: 'Detect again' }).click();
		await expect(page.getByText('This folder belongs to a larger install, so nothing was compared.')).toBeVisible({
			timeout: 10000
		});
		await expect(page.getByRole('button', { name: 'Add selected' })).toHaveCount(0);
		await expect(page.locator('input[id^="again-"]')).toHaveCount(0);
	});
});

test.describe('folders panel with layout profiles - mobile', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the expanded root fits a phone', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page);
		await openRoot(page);
		await screenshot(page, JOURNEY, 'mobile-panel');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	});
});
