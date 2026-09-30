import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-folder-picker';
const ROOT = '/srv/sm';

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

type Folder = { name: string; has_models: boolean; linked?: boolean };

const TREE: Record<string, Folder[]> = {
	'': [
		{ name: 'Data', has_models: true },
		{ name: 'extras', has_models: false },
		{ name: 'archive', has_models: true, linked: true }
	],
	Data: [{ name: 'Models', has_models: true }],
	'Data/Models': [
		{ name: 'Lora', has_models: true },
		{ name: 'Extra', has_models: false }
	],
	extras: []
};

function listing(sub: string) {
	const folders = TREE[sub] ?? (sub.includes('missing') ? undefined : []);
	if (!folders) return null;
	return {
		path: ROOT,
		sub,
		parent: sub ? sub.split('/').slice(0, -1).join('/') : null,
		folders: folders.map((f) => ({
			name: f.name,
			subdir: sub ? `${sub}/${f.name}` : f.name,
			has_models: f.has_models,
			linked: f.linked ?? false
		})),
		has_models: folders.some((f) => f.has_models),
		truncated: false
	};
}

function suggestion(model_type: string, subdir: string, label: string) {
	return {
		model_type,
		subdir,
		label,
		write: true,
		scan_headers: false,
		matched_by: 'profile',
		source: 'profile',
		file_count: 3,
		file_count_truncated: false
	};
}

type Binding = { model_type: string; subdir: string; is_write: boolean };

function bindingView(b: Binding) {
	return {
		model_type: b.model_type,
		folder: b.model_type === 'lora' ? 'loras' : 'checkpoints',
		subdir: b.subdir,
		path: `${ROOT}/${b.subdir}`,
		exists: true,
		position: 0,
		is_write: b.is_write,
		indexed_files: 3,
		size_bytes: 3_000_000,
		unindexed: 0,
		scan_headers: false
	};
}

async function setup(page: Page, options: { patchFails?: boolean } = {}) {
	const bindings: Binding[] = [{ model_type: 'lora', subdir: 'Data/Models/Lora', is_write: true }];
	const log = { browses: [] as any[], patches: [] as any[], creates: [] as any[] };

	const rootView = () => ({
		id: 'lib1',
		label: 'StabilityMatrix',
		path: ROOT,
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		layout_profile: 'generic',
		bindings: bindings.map(bindingView)
	});

	await page.route('**/api/models/roots', (route) => {
		const method = route.request().method();
		if (method === 'POST') {
			const body = route.request().postDataJSON();
			log.creates.push(body);
			return fulfillJson(route, { success: true, data: { ...rootView(), bindings: [] } }, 201);
		}
		if (method !== 'GET') return route.fallback();
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
		if (options.patchFails) {
			return fulfillJson(
				route,
				{ detail: { error: 'model_roots_binding_nested', message: 'extras is nested inside another folder.' } },
				422
			);
		}
		for (const b of body.bindings ?? []) bindings.push({ model_type: b.model_type, subdir: b.subdir, is_write: false });
		return fulfillJson(route, { success: true, data: rootView() });
	});
	await page.route('**/api/models/roots/browse**', (route) => {
		const url = new URL(route.request().url());
		const sub = url.searchParams.get('sub') ?? '';
		log.browses.push({ path: url.searchParams.get('path'), sub });
		const data = listing(sub);
		if (!data) {
			return fulfillJson(
				route,
				{ detail: { error: 'model_roots_browse_failed', message: "That subfolder doesn't exist." } },
				404
			);
		}
		return fulfillJson(route, { success: true, data });
	});
	await page.route('**/api/models/roots/detect', (route) =>
		fulfillJson(route, {
			success: true,
			data: {
				path: ROOT,
				root_path: ROOT,
				effective_path: ROOT,
				state: 'online',
				writable_hint: true,
				case_insensitive: false,
				layout: 'typed',
				profile: null,
				alternatives: [{ id: 'generic', label: 'Generic (folder names)', confidence: null, score: 0 }],
				suggestions: [suggestion('lora', 'Data/Models/Lora', 'Lora')],
				outside_folders: [],
				extra_roots: [],
				delegated: [],
				single_type_guess: null,
				conflicts: [],
				warnings: []
			}
		})
	);
	await page.route('**/api/models/layouts', (route) => fulfillJson(route, { layouts: [], load_errors: {} }));
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: { state: 'idle' } })
	);
	return log;
}

async function openFolders(page: Page) {
	await page.goto('/admin?tab=models&view=folders');
	await expect(page.getByRole('heading', { name: 'Folders', level: 2 })).toBeVisible({ timeout: 15000 });
}

async function openRoot(page: Page) {
	await openFolders(page);
	await page.getByRole('button', { name: /^StabilityMatrix/ }).click();
	await expect(page.getByText('Data/Models/Lora').first()).toBeVisible({ timeout: 10000 });
}

test.describe('folder picker - desktop', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('Add folder: pick a subfolder for a type and create with it', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openFolders(page);
		await page.getByRole('button', { name: 'Add folder' }).first().click();
		await page.getByLabel('Folder path').fill(ROOT);
		await page.getByRole('button', { name: 'Detect', exact: true }).click();
		await expect(page.getByText('Detected folders')).toBeVisible({ timeout: 10000 });

		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await expect(page.getByRole('button', { name: 'Data', exact: true })).toBeVisible();
		await expect(page.getByRole('button', { name: /extras\s+Empty/ })).toBeVisible();
		await expect(page.getByRole('button', { name: /archive\s+Link/ })).toBeVisible();
		expect(log.browses[0]).toEqual({ path: ROOT, sub: '' });

		await page.getByLabel('Type', { exact: true }).selectOption('checkpoint');
		await page.getByRole('button', { name: 'Data', exact: true }).click();
		await page.getByRole('button', { name: 'Models', exact: true }).click();
		await expect(page.getByRole('button', { name: /Extra\s+Empty/ })).toBeVisible();
		await page.getByRole('button', { name: /Extra\s+Empty/ }).click();
		await expect(page.getByText('will use Data/Models/Extra')).toBeVisible();
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, 'desktop-add-folder-picker');

		await page.getByRole('button', { name: 'Add Base model folder' }).click();
		await expect(page.getByText('Added by you')).toBeVisible();
		await expect(page.getByText('Data/Models/Extra', { exact: true })).toBeVisible();
	});

	test('Add folder: a typed path becomes a ticked "Added by you" row and is created', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openFolders(page);
		await page.getByRole('button', { name: 'Add folder' }).first().click();
		await page.getByLabel('Folder path').fill(ROOT);
		await page.getByRole('button', { name: 'Detect', exact: true }).click();
		await expect(page.getByText('Detected folders')).toBeVisible({ timeout: 10000 });

		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await page.getByLabel('Type', { exact: true }).selectOption('vae');
		await page.getByLabel('Or type a path inside the folder').fill('extras/vae-files');
		await page.getByRole('button', { name: 'Add VAE folder' }).click();

		await expect(page.getByText('Added by you')).toBeVisible();
		await expect(page.getByRole('checkbox', { name: /vae-files/ })).toBeChecked();
		await screenshot(page, JOURNEY, 'desktop-add-folder-added-row');

		await page.getByRole('button', { name: 'Add folder' }).last().click();
		await expect.poll(() => log.creates.length).toBe(1);
		const bindings = log.creates[0].bindings;
		expect(bindings).toHaveLength(2);
		expect(bindings).toContainEqual(expect.objectContaining({ model_type: 'vae', subdir: 'extras/vae-files', write: true }));
	});

	test('overlaps and full paths are refused before anything is sent', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openFolders(page);
		await page.getByRole('button', { name: 'Add folder' }).first().click();
		await page.getByLabel('Folder path').fill(ROOT);
		await page.getByRole('button', { name: 'Detect', exact: true }).click();
		await expect(page.getByText('Detected folders')).toBeVisible({ timeout: 10000 });
		await page.getByRole('button', { name: 'Add a folder for a type' }).click();

		const typed = page.getByLabel('Or type a path inside the folder');
		await typed.fill('Data/Models/Lora/sub');
		await expect(page.getByText(/overlaps Data\/Models\/Lora/)).toBeVisible();
		await expect(page.getByRole('button', { name: /^Add .* folder$/ })).toBeDisabled();
		await typed.fill('/etc');
		await expect(page.getByText('Use a path relative to the folder, not a full path.')).toBeVisible();
		await typed.fill('../up');
		await expect(page.getByText('A path cannot go up with "..".')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-refused-path');
		expect(log.creates).toHaveLength(0);
	});

	test('Folders panel: pick a folder for a type and add it to an existing root', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await page.getByLabel('Type', { exact: true }).selectOption('lora');
		await page.getByRole('button', { name: 'Data', exact: true }).click();
		await page.getByRole('button', { name: 'Models', exact: true }).click();
		await page.getByRole('button', { name: /Extra\s+Empty/ }).click();
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, 'desktop-panel-picker');
		await page.getByRole('button', { name: 'Add LoRA folder' }).click();

		await expect.poll(() => log.patches.length).toBe(1);
		expect(log.patches[0]).toEqual({
			bindings: [{ model_type: 'lora', subdir: 'Data/Models/Extra', write: false }]
		});
		await expect(page.getByText('Data/Models/Extra').first()).toBeVisible();
		await expect(page.getByLabel('Type', { exact: true })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-panel-added');
	});

	test('Folders panel: a server refusal is shown in the picker', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, { patchFails: true });
		await openRoot(page);
		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await page.getByLabel('Or type a path inside the folder').fill('extras');
		await page.getByRole('button', { name: /^Add .* folder$/ }).click();
		await expect(page.getByText('extras is nested inside another folder.')).toBeVisible();
		await expect(page.getByLabel('Type', { exact: true })).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-panel-refused');
	});

	test('the folder itself is refused while other folders are added', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await expect(page.getByRole('button', { name: /^Add .* folder$/ })).toBeDisabled();
		await page.getByRole('button', { name: 'use the folder itself' }).click();
		await expect(page.getByText(/overlaps Data\/Models\/Lora/)).toBeVisible();
		await expect(page.getByRole('button', { name: /^Add .* folder$/ })).toBeDisabled();
		expect(log.patches).toHaveLength(0);
	});

	test('Add folder: the folder itself can be chosen once nothing else is ticked', async ({ page }) => {
		await loginAsOwner(page);
		const log = await setup(page);
		await openFolders(page);
		await page.getByRole('button', { name: 'Add folder' }).first().click();
		await page.getByLabel('Folder path').fill(ROOT);
		await page.getByRole('button', { name: 'Detect', exact: true }).click();
		await expect(page.getByText('Detected folders')).toBeVisible({ timeout: 10000 });
		await page.getByRole('checkbox', { name: /Lora/ }).uncheck();

		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await page.getByLabel('Type', { exact: true }).selectOption('vae');
		await page.getByRole('button', { name: 'use the folder itself' }).click();
		await expect(page.getByText('will use the folder itself')).toBeVisible();
		await page.getByRole('button', { name: 'Add VAE folder' }).click();
		await expect(page.getByText('(this folder)').first()).toBeVisible();

		await page.getByRole('button', { name: 'Add folder' }).last().click();
		await expect.poll(() => log.creates.length).toBe(1);
		expect(log.creates[0].bindings).toEqual([expect.objectContaining({ model_type: 'vae', subdir: '' })]);
	});
});

test.describe('folder picker - mobile', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the picker fits a phone', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page);
		await openRoot(page);
		await page.getByRole('button', { name: 'Add a folder for a type' }).click();
		await expect(page.getByRole('button', { name: 'Data', exact: true })).toBeVisible();
		const scrolls = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
		expect(scrolls).toBe(false);
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, 'mobile-panel-picker');
	});
});
