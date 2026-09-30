import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'model-layout-add-folder';

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

function sug(model_type: string, subdir: string, label: string, write: boolean, count = 4) {
	return {
		model_type,
		subdir,
		label,
		write,
		scan_headers: model_type === 'checkpoint',
		matched_by: 'profile',
		source: 'profile',
		file_count: count,
		file_count_truncated: false
	};
}

function smDetection(overrides: Record<string, unknown> = {}) {
	return {
		path: '/srv/sm',
		root_path: '/srv/sm',
		effective_path: '/srv/sm/Data/Models',
		state: 'online',
		writable_hint: true,
		case_insensitive: false,
		layout: 'typed',
		profile: {
			id: 'stabilitymatrix',
			label: 'StabilityMatrix',
			variant: null,
			confidence: 'strong',
			score: 14,
			source: 'marketplace',
			install_path: '/srv/sm/Data',
			models_path: '/srv/sm/Data/Models',
			evidence: ['Data/.sm-portable', 'Data/Packages', 'Models/StableDiffusion']
		},
		alternatives: [
			{ id: 'comfyui', label: 'ComfyUI', confidence: 'weak', score: 3 },
			{ id: 'generic', label: 'Generic (folder names)', confidence: null, score: 0 }
		],
		suggestions: [
			sug('checkpoint', 'Data/Models/StableDiffusion', 'StableDiffusion', true, 12),
			sug('lora', 'Data/Models/Lora', 'Lora', true, 30),
			sug('lora', 'Data/Models/LyCORIS', 'LyCORIS', false, 5),
			sug('vae', 'Data/Models/VAE', 'VAE', true, 2)
		],
		outside_folders: [],
		extra_roots: [],
		delegated: [],
		single_type_guess: null,
		conflicts: [],
		warnings: [],
		...overrides
	};
}

function createdRoot(path: string, label: string) {
	return {
		id: `r-${label}`,
		label,
		path,
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		layout_profile: 'generic',
		bindings: []
	};
}

type DetectCall = { path: string; profile?: string };

async function setup(page: Page, detect: (call: DetectCall) => Record<string, unknown>) {
	const detects: DetectCall[] = [];
	const creates: Record<string, any>[] = [];
	await page.route('**/api/models/roots', (route) => {
		const method = route.request().method();
		if (method === 'GET') {
			return fulfillJson(route, {
				success: true,
				data: {
					roots: [],
					types: [],
					unplaced: [],
					indexing: { state: 'idle' },
					server_os: 'Linux',
					path_style: 'posix'
				}
			});
		}
		if (method === 'POST') {
			const body = route.request().postDataJSON();
			creates.push(body);
			return fulfillJson(route, { success: true, data: createdRoot(body.path, body.path.split('/').pop()) }, 201);
		}
		return route.fallback();
	});
	await page.route('**/api/models/roots/detect', (route) => {
		const call = route.request().postDataJSON() as DetectCall;
		detects.push(call);
		return fulfillJson(route, { success: true, data: detect(call) });
	});
	await page.route('**/api/models/layouts', (route) =>
		fulfillJson(route, {
			layouts: [
				{ id: 'stabilitymatrix', label: 'StabilityMatrix', source: 'marketplace' },
				{ id: 'comfyui', label: 'ComfyUI', source: 'marketplace' },
				{ id: 'fooocus', label: 'Fooocus', source: 'marketplace' },
				{ id: 'pinokio', label: 'Pinokio', source: 'marketplace' }
			],
			load_errors: {}
		})
	);
	await page.route('**/api/models/indexing/status', (route) =>
		fulfillJson(route, { success: true, data: { state: 'idle' } })
	);
	return { detects, creates };
}

async function openAddFolder(page: Page, path: string) {
	await page.goto('/admin?tab=models&view=folders');
	await expect(page.getByRole('heading', { name: 'Folders', level: 2 })).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Add folder' }).first().click();
	await page.getByLabel('Folder path').fill(path);
	await page.getByRole('button', { name: 'Detect', exact: true }).click();
}

test.describe('add folder with layout profiles - desktop', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('StabilityMatrix is detected, downloads choice and payload', async ({ page }) => {
		await loginAsOwner(page);
		const { creates } = await setup(page, () => smDetection());
		await openAddFolder(page, '/srv/sm');
		await expect(page.getByText('Detected: StabilityMatrix')).toBeVisible({ timeout: 10000 });
		await expect(page.getByLabel('Layout')).toHaveValue('stabilitymatrix');
		await expect(page.getByText('LyCORIS', { exact: true })).toBeVisible();
		await expect(page.getByLabel('Downloads go here')).toHaveCount(2);
		await page.getByText('Detected: StabilityMatrix').hover();
		await page.waitForTimeout(600);
		await screenshot(page, JOURNEY, 'desktop-stabilitymatrix');

		await page.getByRole('radio').nth(1).check();
		await page.getByRole('button', { name: 'Add folder' }).last().click();
		await expect.poll(() => creates.length).toBe(1);
		const body = creates[0];
		expect(body.path).toBe('/srv/sm');
		expect(body.profile).toBe('stabilitymatrix');
		const loraWrites = body.bindings.filter((b: any) => b.model_type === 'lora' && b.write).map((b: any) => b.subdir);
		expect(loraWrites).toEqual(['Data/Models/LyCORIS']);
		expect(body.bindings).toHaveLength(4);
		expect(body.write_types.sort()).toEqual(['checkpoint', 'lora', 'vae']);
	});

	test('overriding the layout re-detects with that profile', async ({ page }) => {
		await loginAsOwner(page);
		const { detects } = await setup(page, (call) =>
			call.profile === 'comfyui'
				? smDetection({
						profile: { ...smDetection().profile, id: 'comfyui', label: 'ComfyUI', confidence: 'strong' },
						suggestions: [sug('checkpoint', 'checkpoints', 'checkpoints', true)]
					})
				: smDetection()
		);
		await openAddFolder(page, '/srv/sm');
		await expect(page.getByText('Detected: StabilityMatrix')).toBeVisible({ timeout: 10000 });
		await page.getByLabel('Layout').selectOption('comfyui');
		await expect(page.getByText('Detected: ComfyUI')).toBeVisible({ timeout: 10000 });
		expect(detects.at(-1)).toEqual({ path: '/srv/sm', profile: 'comfyui' });
		await expect(page.getByText('LyCORIS', { exact: true })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-override-comfyui');
	});

	test('an outside folder offers the install folder', async ({ page }) => {
		await loginAsOwner(page);
		const { detects } = await setup(page, (call) =>
			call.path === '/srv/webui'
				? smDetection({ path: '/srv/webui', root_path: '/srv/webui', outside_folders: [] })
				: smDetection({
						path: '/srv/webui/models',
						root_path: '/srv/webui/models',
						outside_folders: [
							{
								model_type: 'embedding',
								path: '/srv/webui/embeddings',
								label: 'embeddings',
								install_path: '/srv/webui'
							}
						]
					})
		);
		await openAddFolder(page, '/srv/webui/models');
		await expect(page.getByText('This tool keeps some folders outside the one you chose')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'desktop-outside-folder');
		await page.getByRole('button', { name: 'Use the install folder instead' }).click();
		await expect(page.getByLabel('Folder path')).toHaveValue('/srv/webui');
		await expect.poll(() => detects.length).toBe(2);
		expect(detects[1].path).toBe('/srv/webui');
		await expect(page.getByText('This tool keeps some folders outside the one you chose')).toHaveCount(0);
	});

	test('extra roots are never pre-ticked and are created after the main root', async ({ page }) => {
		await loginAsOwner(page);
		const { creates } = await setup(page, () =>
			smDetection({
				extra_roots: [
					{
						path: '/mnt/data/shared-models',
						label: 'shared-models',
						source: 'extra_model_paths.yaml › a1111',
						primary: false,
						profile_id: 'comfyui',
						suggestions: [sug('checkpoint', 'checkpoints', 'checkpoints', true, 9)]
					},
					{
						path: '/mnt/data/other',
						label: 'other',
						source: 'extra_model_paths.yaml › other',
						primary: false,
						profile_id: 'comfyui',
						suggestions: [sug('lora', 'loras', 'loras', true, 1)]
					}
				]
			})
		);
		await openAddFolder(page, '/srv/sm');
		await expect(page.getByText('Also used by this install')).toBeVisible({ timeout: 10000 });
		const boxes = page.locator('input[id^="root-extra-"]');
		await expect(boxes).toHaveCount(2);
		await expect(boxes.first()).not.toBeChecked();
		await screenshot(page, JOURNEY, 'desktop-extra-roots');
		await boxes.first().check();
		await page.getByRole('button', { name: 'Add folder' }).last().click();
		await expect.poll(() => creates.length).toBe(2);
		expect(creates[0].path).toBe('/srv/sm');
		expect(creates[1].path).toBe('/mnt/data/shared-models');
		expect(creates[1].profile).toBe('comfyui');
	});

	test('Pinokio apps can be picked', async ({ page }) => {
		await loginAsOwner(page);
		const { detects } = await setup(page, (call) =>
			call.path === '/srv/pinokio/api/comfy.git'
				? smDetection({
						path: '/srv/pinokio/api/comfy.git',
						root_path: '/srv/pinokio/api/comfy.git',
						profile: { ...smDetection().profile, id: 'comfyui', label: 'ComfyUI' },
						delegated: []
					})
				: smDetection({
						path: '/srv/pinokio',
						root_path: '/srv/pinokio/api/forge.git',
						profile: { ...smDetection().profile, id: 'pinokio', label: 'Pinokio' },
						delegated: [
							{ path: '/srv/pinokio/api/forge.git', label: 'forge.git', profile: { id: 'forge', label: 'Forge', confidence: 'strong' } },
							{ path: '/srv/pinokio/api/comfy.git', label: 'comfy.git', profile: { id: 'comfyui', label: 'ComfyUI', confidence: 'strong' } }
						]
					})
		);
		await openAddFolder(page, '/srv/pinokio');
		await expect(page.getByText('Pinokio apps', { exact: true })).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'desktop-pinokio-apps');
		await page.getByRole('button', { name: /comfy\.git/ }).click();
		await expect.poll(() => detects.length).toBe(2);
		expect(detects[1].path).toBe('/srv/pinokio/api/comfy.git');
		await expect(page.getByText('Detected: ComfyUI')).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole('button', { name: /forge\.git/ })).toBeVisible();
	});

	test('no known layout says Generic was used and keeps the picker', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, () =>
			smDetection({
				profile: null,
				alternatives: [{ id: 'generic', label: 'Generic (folder names)', confidence: null, score: 0 }],
				suggestions: [{ model_type: 'lora', subdir: 'loras', matched_by: 'canonical', file_count: 3, file_count_truncated: false }]
			})
		);
		await openAddFolder(page, '/srv/unknown');
		await expect(page.getByText('No known layout detected')).toBeVisible({ timeout: 10000 });
		await expect(page.getByText(/folder names are used \(Generic\)/)).toBeVisible();
		await expect(page.getByLabel('Layout')).toHaveValue('generic');
		await screenshot(page, JOURNEY, 'desktop-no-layout');
	});
});

test.describe('add folder with layout profiles - mobile', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the dialog fits a phone', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, () =>
			smDetection({
				outside_folders: [
					{ model_type: 'embedding', path: '/srv/sm/Embeddings', label: 'embeddings', install_path: '/srv/sm/Data' }
				]
			})
		);
		await openAddFolder(page, '/srv/sm');
		await expect(page.getByText('Detected: StabilityMatrix')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'mobile-add-folder');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	});
});
