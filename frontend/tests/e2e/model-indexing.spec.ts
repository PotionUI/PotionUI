import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson, mockAdminSocket } from './helpers';
import { gotoFolders as openFoldersPage, selectFolderView } from './model-folders-nav';

const JOURNEY = 'model-indexing';

const MIB = 1024 * 1024;
const GIB = MIB * 1024;
const TIB = GIB * 1024;

const LEVELS = [
	{ id: 'low', workers: 1, rate_bps: 96 * MIB, pauses_for_generation: true },
	{ id: 'normal', workers: 2, rate_bps: 384 * MIB, pauses_for_generation: true },
	{ id: 'max', workers: 4, rate_bps: null, pauses_for_generation: false }
];

type Json = Record<string, any>;

function digests(overrides: Json = {}): Json {
	return {
		policy: 'background',
		level: 'low',
		levels: LEVELS,
		paused: false,
		pause_reason: null,
		busy: false,
		workers: 1,
		pending_total: 0,
		queued: 0,
		deferred: 0,
		ready: 0,
		ready_bytes: 0,
		pending_bytes: 0,
		queued_bytes: 0,
		current: [],
		throughput_bps: null,
		eta_seconds: null,
		duplicate_checks: 0,
		failed: [],
		plan: { awaiting_choice: false, files: 0, bytes: 0, min_files: 20, min_bytes: 64 * GIB },
		version: 1,
		...overrides
	};
}

const ROOTS = [
	{ root_id: 'home', label: 'PotionUI models', state: 'online', found: 29, indexed: 29, failed: 0 },
	{ root_id: 'comfy', label: 'ComfyUI models', state: 'online', found: 458, indexed: 458, failed: 0 }
];

function indexingStatus(digestOverrides: Json = {}, overrides: Json = {}): Json {
	return {
		state: 'done',
		found_on_disk: 487,
		indexed: 0,
		roots: ROOTS,
		digests: digests(digestOverrides),
		...overrides
	};
}

const RUNNING = {
	policy: 'background',
	ready: 218,
	pending_total: 269,
	queued: 269,
	ready_bytes: 32 * GIB,
	pending_bytes: 1.7 * TIB,
	queued_bytes: 1.7 * TIB,
	throughput_bps: 96 * MIB,
	eta_seconds: 5 * 3600 + 12 * 60,
	duplicate_checks: 3,
	current: [{ model_id: 'm1', filename: 'neon_wan_lora_v6-190.safetensors', size: 308 * MIB, done: 279 * MIB }]
};

const AWAITING = {
	policy: 'unset',
	ready: 15,
	pending_total: 472,
	deferred: 472,
	pending_bytes: 1.75 * TIB,
	plan: { awaiting_choice: true, files: 472, bytes: 1.75 * TIB, min_files: 20, min_bytes: 64 * GIB }
};

const LATER = {
	policy: 'on_demand',
	ready: 15,
	pending_total: 472,
	deferred: 472,
	pending_bytes: 1.75 * TIB
};

interface PendingFile {
	model_id: string;
	location_id: string;
	filename: string;
	model_type: string;
	size: number;
	root_label: string;
	rel_path: string;
	queued: boolean;
}

function pendingFiles(): PendingFile[] {
	const files: PendingFile[] = [];
	const add = (filename: string, model_type: string, size: number) =>
		files.push({
			model_id: `m${files.length}`,
			location_id: `loc${files.length}`,
			filename,
			model_type,
			size,
			root_label: 'ComfyUI models',
			rel_path: filename,
			queued: false
		});
	add('Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors', 'checkpoint', 6.6 * GIB);
	add('sd_xl_base_1.0.safetensors', 'checkpoint', 6.5 * GIB);
	add('t5xxl_fp16.safetensors', 'text_encoder', 9.8 * GIB);
	add('umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'text_encoder', 6.3 * GIB);
	add('sdxl_vae.safetensors', 'vae', 319 * MIB);
	add('controlnet-union-sdxl-1.0.safetensors', 'controlnet', 2.3 * GIB);
	add('flux1-dev-fp8.safetensors', 'diffusion_model', 16 * GIB);
	add('qwen_image_edit_2509_bf16.safetensors', 'diffusion_model', 38 * GIB);
	for (let i = 0; i < 90; i++) add(`style_lora_v${i}.safetensors`, 'lora', 200 * MIB);
	return files;
}

const PENDING = pendingFiles();

interface Mock {
	status: Json;
	settings: Json[];
	digestRequests: Json[];
	pendingQueries: Record<string, string>[];
	pauses: number;
	resumes: number;
}

function rootsOverview(status: Json): Json {
	const binding = (model_type: string, files: number, bytes: number) => ({
		model_type,
		scan_headers: true,
		folder: model_type,
		subdir: model_type,
		path: `/models/${model_type}`,
		exists: true,
		position: 0,
		is_write: false,
		indexed_files: files,
		size_bytes: bytes,
		unindexed: 0
	});
	return {
		roots: [
			{
				id: 'home',
				label: 'PotionUI models',
				path: 'C:\\PotionUI\\models',
				kind: 'home',
				read_only: false,
				case_insensitive: false,
				state: 'online',
				state_reason: null,
				state_checked_at: null,
				bindings: [binding('lora', 29, 12 * GIB)]
			},
			{
				id: 'comfy',
				label: 'ComfyUI models',
				path: 'D:\\AI\\ComfyUI\\models',
				kind: 'library',
				read_only: false,
				case_insensitive: false,
				state: 'online',
				state_reason: null,
				state_checked_at: null,
				bindings: [binding('checkpoint', 458, 1.7 * TIB)]
			}
		],
		types: [],
		unplaced: [],
		indexing: status,
		server_os: 'Windows',
		path_style: 'windows'
	};
}

async function installMocks(page: Page, initial: Json): Promise<Mock> {
	const mock: Mock = { status: initial, settings: [], digestRequests: [], pendingQueries: [], pauses: 0, resumes: 0 };
	const d = () => mock.status.digests as Json;
	const socket = await mockAdminSocket(page, () => mock.status);

	await page.route('**/api/models/roots', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return fulfillJson(route, { success: true, data: rootsOverview(mock.status) });
	});

	await page.route(
		(url) => url.pathname.startsWith('/api/models/indexing/'),
		async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			const method = request.method();
			const path = url.pathname;
			if (path === '/api/models/indexing/status' && method === 'GET') {
				return fulfillJson(route, { success: true, data: mock.status });
			}
			if (path === '/api/models/indexing/settings' && method === 'PUT') {
				const body = request.postDataJSON() as Json;
				mock.settings.push(body);
				if (body.level) d().level = body.level;
				if (body.policy) {
					d().policy = body.policy;
					d().plan = { ...d().plan, awaiting_choice: false };
				}
				await socket.pushIndexingStatus(mock.status);
				return fulfillJson(route, { success: true, data: { level: d().level, policy: d().policy } });
			}
			if (path === '/api/models/indexing/pause' && method === 'POST') {
				mock.pauses += 1;
				d().paused = true;
				await socket.pushIndexingStatus(mock.status);
				return fulfillJson(route, { success: true, data: mock.status });
			}
			if (path === '/api/models/indexing/resume' && method === 'POST') {
				mock.resumes += 1;
				d().paused = false;
				await socket.pushIndexingStatus(mock.status);
				return fulfillJson(route, { success: true, data: mock.status });
			}
			if (path === '/api/models/indexing/digests' && method === 'POST') {
				mock.digestRequests.push(request.postDataJSON() as Json);
				d().plan = { ...d().plan, awaiting_choice: false };
				await socket.pushIndexingStatus(mock.status);
				return fulfillJson(route, { success: true, data: mock.status });
			}
			if (path === '/api/models/indexing/pending' && method === 'GET') {
				const query = Object.fromEntries(url.searchParams.entries());
				mock.pendingQueries.push(query);
				const needle = (query.query ?? '').toLowerCase();
				const matches = PENDING.filter(
					(file) => (!query.model_type || file.model_type === query.model_type) && file.filename.toLowerCase().includes(needle)
				);
				const offset = Number(query.offset ?? 0);
				const limit = Number(query.limit ?? 50);
				const byType: Record<string, number> = {};
				for (const file of PENDING) byType[file.model_type] = (byType[file.model_type] ?? 0) + 1;
				return fulfillJson(route, {
					success: true,
					data: {
						items: matches.slice(offset, offset + limit),
						total: matches.length,
						total_bytes: matches.reduce((sum, file) => sum + file.size, 0),
						by_type: byType
					}
				});
			}
			return route.continue();
		}
	);
	return mock;
}

const READINESS = {
	overall: 'not_ready',
	checks: [
		{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: 'Service is healthy.', action: null },
		{ area: 'execution', status: 'ready', code: 'EXECUTION_READY', message: 'A backend is ready.', action: null },
		{ area: 'content', status: 'not_ready', code: 'NO_PRESETS_ASSIGNED', message: 'No presets yet.', action: null },
		{ area: 'generation_proven', status: 'not_ready', code: 'NEVER_GENERATED', message: 'Nothing generated yet.', action: null }
	]
};

async function gotoFolders(page: Page) {
	await openFoldersPage(page, 'Indexing');
	await expect(page.getByText('Model indexing', { exact: true })).toBeVisible({ timeout: 15000 });
}

async function expectNoHorizontalScroll(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow).toBeLessThanOrEqual(1);
}

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	const phone = viewport.width < 768;

	test.describe(`model indexing @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('first run: the choice cannot be dismissed and Index all starts the background pass', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(AWAITING));
			await page.goto('/admin?tab=models&view=folders');

			const dialog = page.getByRole('dialog');
			await expect(dialog.getByRole('heading', { name: '487 models found' })).toBeVisible({ timeout: 15000 });
			await expect(dialog.getByText('In 2 folders · listed and ready to use now')).toBeVisible();
			await expect(dialog.getByText('472 files')).toBeVisible();
			await expect(dialog.getByText('1.75 TB')).toBeVisible();
			await expect(dialog.getByText('15 small files were indexed instantly.')).toBeVisible();
			await expect(dialog.getByRole('button', { name: 'Close modal' })).toHaveCount(0);
			if (phone) await expect(dialog.locator('kbd')).toBeHidden();
			else await expect(dialog.locator('kbd')).toHaveText('Enter');
			await screenshot(page, JOURNEY, `first-run-${viewport.name}`);

			await page.keyboard.press('Escape');
			await expect(dialog).toBeVisible();
			expect(mock.settings).toEqual([]);

			await dialog.getByRole('button', { name: /Index all in the background/ }).click();
			await expect(dialog).toBeHidden({ timeout: 10000 });
			expect(mock.settings).toEqual([{ policy: 'background' }]);
			expect(mock.digestRequests).toEqual([{ mode: 'all' }]);
			await expectNoHorizontalScroll(page);
		});

		test('first run: Later keeps everything listed and queues nothing', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(AWAITING));
			await page.goto('/admin?tab=models&view=folders');

			const dialog = page.getByRole('dialog');
			await expect(dialog).toBeVisible({ timeout: 15000 });
			await dialog.getByRole('button', { name: 'Later' }).click();
			await expect(dialog).toBeHidden({ timeout: 10000 });

			expect(mock.settings).toEqual([{ policy: 'on_demand' }]);
			expect(mock.digestRequests).toEqual([]);
			await selectFolderView(page, 'Indexing');
			await expect(page.getByText('472 files (1.75 TB) are not indexed yet')).toBeVisible();
		});

		test('first run: pick models by search, select all matches and index the selection', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(AWAITING));
			await page.goto('/admin?tab=models&view=folders');

			const dialog = page.getByRole('dialog');
			await expect(dialog).toBeVisible({ timeout: 15000 });
			await dialog.getByRole('button', { name: 'Pick models' }).click();
			await expect(dialog.getByRole('heading', { name: 'Pick models to index first' })).toBeVisible();
			await expect(dialog.getByRole('checkbox')).toHaveCount(50);
			await expect(dialog.getByText('Showing 50 of 98')).toBeVisible();

			await dialog.getByRole('button', { name: 'Show 48 more' }).click();
			await expect(dialog.getByRole('checkbox')).toHaveCount(98);

			await dialog.getByLabel('Search models by name').fill('xl');
			await expect(dialog.getByText('6 of 98 match')).toBeVisible();
			await expect(dialog.getByRole('checkbox')).toHaveCount(6);
			await dialog.getByRole('checkbox').nth(0).check();
			await dialog.getByRole('checkbox').nth(1).check();
			await expect(dialog.getByTestId('pick-summary')).toContainText('2 selected');
			await screenshot(page, JOURNEY, `pick-${viewport.name}`);

			await dialog.getByRole('button', { name: 'Select all 6' }).click();
			await expect(dialog.getByTestId('pick-summary')).toContainText('6 selected');
			await dialog.getByRole('checkbox').nth(2).uncheck();
			await expect(dialog.getByTestId('pick-summary')).toContainText('5 selected');

			await dialog.getByRole('button', { name: 'Index selected' }).click();
			await expect(dialog).toBeHidden({ timeout: 10000 });

			expect(mock.settings).toEqual([{ policy: 'on_demand' }]);
			expect(mock.digestRequests).toEqual([{ mode: 'matching', query: 'xl', exclude_model_ids: ['m2'] }]);
			await expectNoHorizontalScroll(page);
		});

		test('setup wizard: the choice stays pending behind the wizard and opens once the user is in the app', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(AWAITING));
			await page.route('**/api/readiness', (route) => fulfillJson(route, READINESS));
			await page.goto('/setup');

			await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible({ timeout: 15000 });
			await page.getByRole('button', { name: 'Continue' }).click();
			await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
			await expect(page.locator('[data-models-row]')).toBeVisible();
			await expect(
				page.getByText("487 models found. They're usable now; you'll choose how to index them after setup.")
			).toBeVisible();
			await expect(page.getByRole('dialog')).toHaveCount(0);
			expect(mock.settings).toEqual([]);
			expect(mock.digestRequests).toEqual([]);
			await screenshot(page, JOURNEY, `setup-line-${viewport.name}`);

			await page.goto('/generate');
			await expect(page.getByRole('dialog').getByRole('heading', { name: '487 models found' })).toBeVisible({ timeout: 15000 });
		});

		test('first run: Back returns to the summary', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(page, indexingStatus(AWAITING));
			await page.goto('/admin?tab=models&view=folders');

			const dialog = page.getByRole('dialog');
			await dialog.getByRole('button', { name: 'Pick models' }).click();
			await dialog.getByRole('button', { name: 'Back' }).click();
			await expect(dialog.getByRole('heading', { name: '487 models found' })).toBeVisible();
		});

		test('settings: running shows progress, speed, time left and the file being read', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(RUNNING));
			await gotoFolders(page);

			await expect(page.getByText('Indexing in the background')).toBeVisible();
			await expect(page.getByText('218 of 487 indexed')).toBeVisible();
			await expect(page.getByText('96 MB/s').first()).toBeVisible();
			await expect(page.getByText('about 5 h 12 m').first()).toBeVisible();
			await expect(page.getByText('neon_wan_lora_v6-190.safetensors')).toBeVisible();
			await expect(page.getByText('3 new files are being compared with files you already have.')).toBeVisible();
			await expect(page.getByRole('progressbar', { name: 'Indexing progress' })).toBeVisible();
			await screenshot(page, JOURNEY, `settings-running-${viewport.name}`);

			await page.getByRole('button', { name: 'Normal', exact: true }).click();
			await expect(page.getByText('Two workers at a gentler-than-usual priority, up to 384 MB/s.')).toBeVisible();
			expect(mock.settings).toEqual([{ level: 'normal' }]);

			await page.getByRole('button', { name: 'Pause' }).click();
			await expect(page.getByText('Indexing is paused')).toBeVisible();
			await expect(page.getByText('Paused. Progress is kept, even if you restart PotionUI.')).toBeVisible();
			expect(mock.pauses).toBe(1);

			await page.getByRole('button', { name: 'Resume' }).click();
			await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
			expect(mock.resumes).toBe(1);
			await expectNoHorizontalScroll(page);
		});

		test('settings: waiting for a generation explains the pause', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(
				page,
				indexingStatus({ ...RUNNING, pause_reason: 'generation', busy: true, throughput_bps: null, eta_seconds: null, current: [] })
			);
			await gotoFolders(page);

			await expect(page.getByText('Waiting for generation')).toBeVisible();
			await expect(page.getByText('Indexing is waiting for your generation to finish')).toBeVisible();
			await expect(
				page.getByText('Paused while a generation is running. Indexing carries on by itself when it finishes.')
			).toBeVisible();
			await screenshot(page, JOURNEY, `settings-generation-${viewport.name}`);
		});

		test('settings: after Later the files wait and can be started or picked', async ({ page }) => {
			await loginAsOwner(page);
			const mock = await installMocks(page, indexingStatus(LATER));
			await gotoFolders(page);

			await expect(page.getByText('Not started')).toBeVisible();
			await expect(page.getByText('472 files (1.75 TB) are not indexed yet')).toBeVisible();
			await expect(page.getByText('about 5 h 19 m for the rest')).toBeVisible();
			await screenshot(page, JOURNEY, `settings-later-${viewport.name}`);

			await page.getByRole('button', { name: 'Pick models' }).click();
			const dialog = page.getByRole('dialog');
			await expect(dialog.getByRole('heading', { name: 'Pick models to index first' })).toBeVisible();
			await expect(dialog.getByRole('button', { name: 'Close modal' })).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(dialog).toBeHidden();

			await page.getByRole('button', { name: 'Index all in the background' }).click();
			await expect.poll(() => mock.digestRequests).toEqual([{ mode: 'all' }]);
			expect(mock.settings).toEqual([{ policy: 'background' }]);
		});

		test('settings: everything indexed', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(page, indexingStatus({ ready: 487, ready_bytes: 1.75 * TIB }));
			await gotoFolders(page);

			await expect(page.getByText('Up to date')).toBeVisible();
			await expect(page.getByText('Every model is indexed')).toBeVisible();
			await expect(page.getByText('487 of 487 indexed')).toBeVisible();
			await expect(page.getByText('Nothing left to index.')).toBeVisible();
			await screenshot(page, JOURNEY, `settings-done-${viewport.name}`);
		});

		test('settings: the scan stays visible above the indexing block', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(page, indexingStatus(RUNNING, { state: 'scanning', scanned_roots: ['/mnt/models'] }));
			await gotoFolders(page);

			await expect(page.getByText('Scanning /mnt/models')).toBeVisible();
			await expect(page.getByText('Indexing in the background')).toBeVisible();
		});

		test('settings: files that could not be read are listed', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(
				page,
				indexingStatus({ ...RUNNING, failed: [{ path: '/models/loras/broken.safetensors', error: 'Permission denied' }] })
			);
			await gotoFolders(page);

			await page.getByRole('button', { name: /file could not be read/ }).click();
			await expect(page.getByText('Permission denied')).toBeVisible();
		});

		test('models list overflow links the unindexed count to Folders', async ({ page }) => {
			await loginAsOwner(page);
			await installMocks(page, indexingStatus(LATER));
			await page.goto('/admin?tab=models');

			await page.getByRole('button', { name: 'More actions' }).click();
			const link = page.getByRole('menuitem', { name: '472 not indexed yet' });
			await expect(link).toBeVisible({ timeout: 15000 });
			await expect(link).toHaveAttribute('href', '/admin?tab=models&view=folders');
			await link.click();
			await expect(page.locator('[data-folders-pane]')).toBeVisible();
		});
	});
}
