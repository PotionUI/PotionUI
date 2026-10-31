import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson } from './helpers';
import { discoverAndAssignImagePreset, installAndSelectImagePreset, presetOption } from './presetPreamble';

const JOURNEY = 'recipes-install-modes';
const RECIPE_ID = 'modes-ltx-2-5';
const RUN_ID = 'run-modes-1';
const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

type Phase = 'none' | 'consent' | 'downloading' | 'done';

interface Mode {
	id: string;
	label: string;
	desc: string;
	ready: boolean;
	missing: number;
	slots: string[];
	extras?: string[];
}

const MODES: Mode[] = [
	{ id: 'video', label: 'Video', desc: 'Text or image to video', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'], extras: ['preview_decoder'] },
	{ id: 'extend', label: 'Extend', desc: 'Continue a clip past its last frame', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'restore', label: 'Restore', desc: 'Clean up old or damaged footage', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'colorize', label: 'Colorize', desc: 'Add color to black and white footage', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'day_to_night', label: 'Day to night', desc: 'Relight a scene as night', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'add_water', label: 'Add water', desc: 'Add rivers, rain or waves to a scene', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'remove_object', label: 'Remove object', desc: 'Paint something out and fill in behind it', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae'] },
	{ id: 'layout', label: 'Layout to render', desc: 'Turn a rough layout into a finished shot', ready: false, missing: 1.4 * GIB, slots: ['model', 'text_encoder', 'video_vae', 'layout_control'] },
	{ id: 'cinemagraph', label: 'Cinemagraph', desc: 'Make a still image loop seamlessly', ready: false, missing: 201 * MIB, slots: ['model', 'text_encoder', 'video_vae', 'cine_lora'] },
	{ id: 'motion', label: 'Motion transfer', desc: 'Move a video’s motion onto a picture', ready: false, missing: 2.6 * GIB, slots: ['model', 'text_encoder', 'video_vae', 'motion_patch'] },
	{ id: 'upscale', label: 'Upscale', desc: 'Sharper video at a higher resolution', ready: true, missing: 0, slots: ['model', 'text_encoder', 'video_vae', 'upscaler'] },
	{ id: 'refine', label: 'Refine faces', desc: 'Fix faces and keep them steady', ready: false, missing: 0.7 * GIB, slots: ['model', 'text_encoder', 'video_vae', 'face_refiner'] }
];

function variant(id: string, label: string, size: number, extra: Record<string, unknown> = {}) {
	return {
		id,
		label,
		precision: null,
		filename: `${id}.safetensors`,
		size_bytes: size,
		installed: false,
		gated: false,
		license_url: null,
		recommended: true,
		best_fit: false,
		note: null,
		...extra
	};
}

function slot(id: string, label: string, usedBy: string[], variants: ReturnType<typeof variant>[], extra: Record<string, unknown> = {}) {
	return {
		id,
		label,
		model_type: 'lora',
		required: true,
		extra: false,
		manual: false,
		used_by: usedBy,
		disk_id: 'd0',
		satisfied_by: null,
		recommended_variant_id: variants[0].id,
		best_fit_variant_id: null,
		reason: '',
		variants,
		...extra
	};
}

const ALL = MODES.map((m) => m.id);
const SLOTS = [
	slot('model', 'Main model', ALL, [
		variant('int8', 'int8', 20 * GIB, { installed: true, recommended: false, precision: 'int8' }),
		variant('nvfp4', 'nvfp4', 11.4 * GIB, { best_fit: true, precision: 'nvfp4', note: 'Fastest on RTX 50 cards, and the smallest file.' }),
		variant('bf16', 'bf16', 40.1 * GIB, { recommended: false, precision: 'bf16', note: 'Needs about 40 GB of video memory. Your card has 32 GB.' })
	], { recommended_variant_id: 'int8', best_fit_variant_id: 'nvfp4' }),
	slot('text_encoder', 'Text encoder', ALL, [variant('gemma', 'Gemma 3 · fp8', 9 * GIB, { installed: true })]),
	slot('video_vae', 'Video decoder', ALL, [variant('vae', 'Video decoder', 1.4 * GIB, { installed: true })]),
	slot('layout_control', 'Layout control', ['layout'], [variant('layout', 'Layout control', 1.4 * GIB)]),
	slot('cine_lora', 'Cinemagraph LoRA', ['cinemagraph'], [variant('cine', 'Cinemagraph LoRA', 201 * MIB)]),
	slot('motion_patch', 'Motion transfer patch', ['motion'], [
		variant('motion', 'Motion transfer patch', 2.6 * GIB, { gated: true, license_url: 'https://huggingface.co/Lightricks/LTX-2.5' })
	]),
	slot('upscaler', 'Spatial upscaler', ['upscale'], [variant('upscaler', 'Spatial upscaler', 1.7 * GIB, { installed: true })]),
	slot('face_refiner', 'Face refiner', ['refine'], [
		variant('refiner', 'Face refiner', 0.7 * GIB, { gated: true, license_url: 'https://huggingface.co/Lightricks/LTX-2.5' })
	]),
	slot('preview_decoder', 'Preview decoder', ['video'], [variant('decoder', 'Preview decoder', 12 * MIB)], { extra: true, reason: 'Faster previews' })
];

function consentRequest(target: 'native' | 'pod') {
	const pod = target === 'pod';
	return {
		kind: 'modes',
		preset_id: 'ltx-2-5-preset',
		preset_name: 'LTX-2.5',
		target: {
			backend_id: target,
			label: pod ? 'Worker rtx-pod' : 'This computer',
			kind: pod ? 'remote' : 'local',
			free_bytes: pod ? 3.1 * GIB : 812 * GIB,
			gpu_name: pod ? 'NVIDIA GeForce RTX 4090' : 'NVIDIA GeForce RTX 5090',
			vram_gb: pod ? 24 : 32,
			options: [
				{ backend_id: 'native', label: 'This computer', kind: 'local', free_bytes: 812 * GIB, gpu_name: 'NVIDIA GeForce RTX 5090', vram_gb: 32 },
				{ backend_id: 'pod', label: 'Worker rtx-pod', kind: 'remote', free_bytes: 3.1 * GIB, gpu_name: 'NVIDIA GeForce RTX 4090', vram_gb: 24 }
			]
		},
		gpu: { generation: 'blackwell', name: 'NVIDIA GeForce RTX 5090', vram_gb: 32 },
		modes: MODES.map((m) => ({
			id: m.id,
			label: m.label,
			short_description: m.desc,
			selected: m.id === 'video',
			locked: m.id === 'video',
			ready: m.ready,
			slot_ids: m.slots,
			extra_slot_ids: m.extras ?? [],
			any_of: [],
			missing_bytes: m.missing
		})),
		slots: SLOTS,
		manifest: [],
		disk: [{ id: 'd0', target, label: 'Models folder', free_bytes: pod ? 3.1 * GIB : 812 * GIB, reserve_bytes: 2 * GIB, model_types: [] }],
		estimate: { bytes_per_sec: 31_000_000, basis: 'median of the last 5 completed downloads' },
		providers: [],
		warnings: [],
		artifacts: [],
		total_bytes: 0
	};
}

const RECIPES = [
	{
		id: RECIPE_ID,
		name: 'LTX-2.5 modes',
		summary: 'Pick the video modes you want and get their files.',
		description: '',
		engine: 'native',
		category: 'video',
		artifact_count: 0,
		total_download_bytes: 4.9 * GIB,
		last_completed_at: null,
		source: 'derived',
		plugin_id: null,
		step_count: 5,
		presets: [{ id: 'ltx-2-5-preset', name: 'LTX-2.5', cover_url: null, installed: true }],
		preset_id: 'ltx-2-5-preset',
		modes_total: 12,
		modes_ready: 8,
		missing_bytes: 4.9 * GIB,
		installed_bytes: 35.6 * GIB
	},
	{
		id: 'modes-minimax-h3',
		name: 'MiniMax-H3 modes',
		summary: 'Pick the H3 modes you want and get their files.',
		description: '',
		engine: 'native',
		category: 'video',
		artifact_count: 0,
		total_download_bytes: 9 * GIB,
		last_completed_at: null,
		source: 'derived',
		plugin_id: null,
		step_count: 5,
		presets: [{ id: 'h3', name: 'MiniMax-H3', cover_url: null, installed: true }],
		preset_id: 'h3',
		modes_total: 6,
		modes_ready: 3,
		missing_bytes: 9 * GIB,
		installed_bytes: 18.2 * GIB
	},
	{
		id: 'modes-qwen-image',
		name: 'Qwen-Image modes',
		summary: 'Pick the Qwen-Image modes you want.',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 0,
		total_download_bytes: 0,
		last_completed_at: null,
		source: 'derived',
		plugin_id: null,
		step_count: 5,
		presets: [{ id: 'qwen', name: 'Qwen-Image', cover_url: null, installed: true }],
		preset_id: 'qwen',
		modes_total: 5,
		modes_ready: 5,
		missing_bytes: 0,
		installed_bytes: 41 * GIB
	},
	{
		id: 'flux-kontext-starter',
		name: 'Flux Kontext starter',
		summary: 'Everything to edit images with Flux Kontext.',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 4,
		total_download_bytes: 23.8 * GIB,
		last_completed_at: '2026-10-01T00:00:00Z',
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: [{ id: 'flux', name: 'Flux Kontext', cover_url: null, installed: true }]
	},
	{
		id: 'zimage-starter',
		name: 'Z-Image starter',
		summary: 'The fast photoreal model, ready to use.',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 3,
		total_download_bytes: 12.4 * GIB,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: [{ id: 'zimage', name: 'Z-Image', cover_url: null, installed: false }]
	}
];

const DETAIL_MODES = MODES.map((m, index) => ({
	id: m.id,
	label: m.label,
	short_description: m.desc,
	ready: m.ready,
	size_bytes: m.ready ? [31, 0, 1.1, 0.4, 0.3, 0.2, 0.5, 0, 0, 0, 2.1, 0][index] * GIB || null : null,
	own_bytes: m.ready && index >= 2 ? 1 * GIB : 0,
	missing_bytes: m.missing,
	removable: m.ready && index >= 2,
	shares_with: m.ready && index < 2 ? ['video'] : []
}));

function attempt(stepKey: string, status: string, output: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
	return {
		step_key: stepKey,
		attempt: 1,
		status,
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: output,
		error_code: null,
		safe_error_detail: null,
		safe_suggested_action: null,
		started_at: null,
		finished_at: null,
		...extra
	};
}

const STEP_DEFS = [
	{ key: 'space', kind: 'disk.check', title: 'Check space' },
	{ key: 'modes.plan', kind: 'modes.plan', title: 'Work out the files' },
	{ key: 'fetch', kind: 'artifacts.fetch', title: 'Download files' },
	{ key: 'defaults', kind: 'preset.defaults', title: 'Set default files' },
	{ key: 'verify', kind: 'models.index', title: 'Check the files' }
];

function buildRun(phase: Phase, target: 'native' | 'pod') {
	const plan = { preset_id: 'ltx-2-5-preset', modes: ['video', 'layout', 'cinemagraph', 'motion', 'refine'], bytes: 4.9 * GIB, files: [1, 2, 3, 4] };
	const files = [
		{ slot_id: 'cine_lora', display_name: 'Cinemagraph LoRA', filename: 'cine.safetensors', size_bytes: 201 * MIB, downloaded_bytes: 201 * MIB, status: 'completed' },
		{ slot_id: 'motion_patch', display_name: 'Motion transfer patch', filename: 'motion.safetensors', size_bytes: 2.6 * GIB, downloaded_bytes: 0.61 * 2.6 * GIB, status: 'downloading' },
		{ slot_id: 'layout_control', display_name: 'Layout control', filename: 'layout.safetensors', size_bytes: 1.4 * GIB, downloaded_bytes: 0.38 * 1.4 * GIB, status: 'downloading' },
		{ slot_id: 'face_refiner', display_name: 'Face refiner', filename: 'refiner.safetensors', size_bytes: 0.7 * GIB, downloaded_bytes: 0, status: 'pending' }
	];
	const steps = STEP_DEFS.map((def, ordinal) => {
		let status = 'pending';
		let attempts: unknown[] = [];
		if (phase === 'consent') {
			if (def.key === 'space') ((status = 'succeeded'), (attempts = [attempt(def.key, 'succeeded', null)]));
			if (def.key === 'modes.plan') ((status = 'awaiting_consent'), (attempts = [attempt(def.key, 'awaiting_consent', { consent_request: consentRequest(target) })]));
		} else if (phase === 'downloading' || phase === 'done') {
			const finished = phase === 'done';
			if (def.key === 'space') ((status = 'succeeded'), (attempts = [attempt(def.key, 'succeeded', null)]));
			if (def.key === 'modes.plan') {
				status = 'succeeded';
				attempts = [attempt(def.key, 'awaiting_consent', { consent_request: consentRequest(target) }), attempt(def.key, 'succeeded', { plan })];
			}
			if (def.key === 'fetch') {
				status = finished ? 'succeeded' : 'running';
				attempts = [
					attempt(
						def.key,
						status,
						{
							files: finished ? files.map((f) => ({ ...f, downloaded_bytes: f.size_bytes, status: 'completed' })) : files,
							files_done: finished ? 4 : 1,
							files_total: 4,
							speed_bytes_per_sec: 58_000_000,
							eta_seconds: 45
						},
						{ progress_current: 2.3 * GIB, progress_total: 4.9 * GIB, progress_unit: 'bytes' }
					)
				];
			}
			if (finished && (def.key === 'defaults' || def.key === 'verify')) ((status = 'succeeded'), (attempts = [attempt(def.key, 'succeeded', null)]));
		}
		return { step_key: def.key, title: def.title, kind: def.kind, ordinal, status, attempts };
	});
	return {
		id: RUN_ID,
		recipe_id: RECIPE_ID,
		recipe_version: 1,
		scope: 'admin',
		mode: 'admin',
		status: phase === 'consent' ? 'awaiting_consent' : phase === 'done' ? 'completed' : 'running',
		current_step: null,
		safe_input: { preselect_modes: [] },
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: '2026-10-31T10:00:00Z',
		updated_at: '2026-10-31T10:01:04Z',
		completed_at: phase === 'done' ? '2026-10-31T10:02:41Z' : null,
		steps,
		attempts: []
	};
}

interface Mock {
	phase: Phase;
	target: 'native' | 'pod';
	grants: unknown[];
	plans: unknown[];
	removals: unknown[];
}

async function mockRecipes(page: Page, mock: Mock) {
	await page.route(/\/api\/recipes(\/[^?]*)?(\?.*)?$/, async (route: Route) => {
		const request = route.request();
		const url = new URL(request.url());
		const path = url.pathname.replace(/^.*\/api\/recipes/, '') || '/';
		const method = request.method();
		const body = () => (request.postData() ? JSON.parse(request.postData()!) : {});

		if (path === '/' && method === 'GET') return fulfillJson(route, { recipes: RECIPES });
		if (path === '/runs' && method === 'GET') {
			return fulfillJson(route, { runs: mock.phase === 'none' ? [] : [buildRun(mock.phase, mock.target)] });
		}
		if (path === `/runs/${RUN_ID}` && method === 'GET') return fulfillJson(route, buildRun(mock.phase, mock.target));
		if (path === `/runs/${RUN_ID}/actions` && method === 'POST') {
			mock.phase = 'none';
			return fulfillJson(route, { ...buildRun('downloading', mock.target), status: 'cancelled' });
		}
		if (path === `/runs/${RUN_ID}/consent/modes.plan` && method === 'POST') {
			mock.grants.push(body());
			mock.phase = 'downloading';
			return fulfillJson(route, buildRun('downloading', mock.target));
		}
		if (path === `/${RECIPE_ID}/runs` && method === 'POST') {
			mock.phase = 'consent';
			mock.target = 'native';
			return fulfillJson(route, buildRun('consent', 'native'));
		}
		if (path === `/${RECIPE_ID}/plan` && method === 'POST') {
			const payload = body();
			mock.plans.push(payload);
			mock.target = payload.target === 'pod' ? 'pod' : 'native';
			return fulfillJson(route, {
				recipe_id: RECIPE_ID,
				steps: STEP_DEFS.map((def) => ({ key: def.key, kind: def.kind, title: def.title, onboarding_only: false, preview: def.key === 'modes.plan' ? { summary: '12 modes, 8 ready' } : null })),
				consent_request: consentRequest(mock.target),
				totals: { download_bytes: 4.9 * GIB, file_count: 4 }
			});
		}
		if (path === `/${RECIPE_ID}` && method === 'GET') {
			return fulfillJson(route, {
				...RECIPES[0],
				steps: STEP_DEFS.map((def) => ({ ...def, onboarding_only: false })),
				artifacts: [],
				smoke: null,
				load_errors: [],
				modes: DETAIL_MODES
			});
		}
		if (path === `/${RECIPE_ID}/modes/upscale/removal` && method === 'GET') {
			return fulfillJson(route, {
				preset_id: 'ltx-2-5-preset',
				mode: 'upscale',
				label: 'Upscale',
				target: 'native',
				delete: [{ model_id: 'd1', label: 'Spatial upscaler', filename: 'ltx-2.5-spatial-upscaler-x2.safetensors', size_bytes: 1.7 * GIB }],
				keep: [
					{ model_id: 'k1', label: 'Main model', filename: 'm.safetensors', size_bytes: 20 * GIB, reason_code: 'shared', reason: 'Used by Video and 9 other modes' },
					{ model_id: 'k2', label: 'Text encoder', filename: 't.safetensors', size_bytes: 9 * GIB, reason_code: 'shared', reason: 'Used by Video and 9 other modes' },
					{ model_id: 'k3', label: 'Video decoder', filename: 'v.safetensors', size_bytes: 1.4 * GIB, reason_code: 'shared', reason: 'Used by Video and 9 other modes' },
					{ model_id: 'k4', label: 'Detail LoRA', filename: 'detail.safetensors', size_bytes: 0.4 * GIB, reason_code: 'shared', reason: 'Also used by Restore' },
					{ model_id: 'k5', label: 'Upscaler', filename: 'ltx-upscale-custom.safetensors', size_bytes: 1.2 * GIB, reason_code: 'outside_models_folder', reason: 'You added this yourself, outside the models folder' }
				],
				frees_bytes: 1.7 * GIB,
				clears_defaults: [{ field: 'upscale_model' }]
			});
		}
		if (path === `/${RECIPE_ID}/modes/upscale/removal` && method === 'POST') {
			mock.removals.push(body());
			return fulfillJson(route, { deleted: [], kept: [], freed_bytes: 1.7 * GIB });
		}
		if (/^\/[^/]+\/readiness$/.test(path)) {
			return fulfillJson(route, { overall: 'ready', checks: [{ area: 'content', status: 'ready', code: 'ok', message: '', action: null }] });
		}
		return route.fallback();
	});
}

async function mockAccess(page: Page) {
	await page.route(/\/api\/presets\/ltx-2-5-preset\/assignments(\?.*)?$/, (route) =>
		fulfillJson(route, { success: true, data: { preset_db_id: 'db-1', assignments: [{ user_id: 'u-editor' }] } })
	);
	await page.route(/\/api\/user-groups(\?.*)?$/, (route) =>
		fulfillJson(route, {
			success: true,
			data: [
				{ id: 'g-everyone', name: 'Everyone', description: null, member_count: 14 },
				{ id: 'g-team', name: 'Studio team', description: null, member_count: 4 }
			]
		})
	);
	await page.route(/\/api\/user-groups\/[^/]+\/presets(\?.*)?$/, (route) =>
		fulfillJson(route, { success: true, data: [{ preset_id: 'db-1' }] })
	);
}

async function openRecipes(page: Page, id?: string) {
	await page.goto(`/admin?tab=recipes${id ? `&id=${id}` : ''}`);
}

for (const width of [1440, 390]) {
	test.describe(`install modes at ${width}px`, () => {
		test.use({ viewport: { width, height: width === 1440 ? 1100 : 844 } });

		test('admin recipes: cards, detail, sheet, customize, disk too small, progress, done, remove', async ({ page }) => {
			const mock: Mock = { phase: 'none', target: 'native', grants: [], plans: [], removals: [] };
			await loginAsOwner(page);
			await mockRecipes(page, mock);
			await mockAccess(page);
			const shot = (scene: string) => screenshot(page, JOURNEY, `${scene}-${width}-dark`);

			await openRecipes(page);
			await expect(page.locator('[data-recipe-group="modes"]')).toBeVisible();
			await expect(page.getByText('8 of 12 modes ready')).toBeVisible();
			await expect(page.getByText('35.6 GB on disk · 4 to add')).toBeVisible();
			await expect(page.getByText('All 5 modes ready')).toBeVisible();
			await expect(page.getByRole('button', { name: 'Manage modes' })).toBeVisible();
			await shot('4a');

			await openRecipes(page, RECIPE_ID);
			const modesCard = page.locator('[data-recipe-modes]');
			await expect(modesCard).toBeVisible();
			await expect(modesCard.locator('[data-recipe-mode]')).toHaveCount(12);
			await expect(modesCard.locator('[data-recipe-mode="video"]')).toContainText('Shares Video files');
			await expect(modesCard.locator('[data-recipe-mode="cinemagraph"]')).toContainText('Install · 201 MB');
			await expect(modesCard.locator('[data-recipe-mode="upscale"]')).toContainText('Remove files');
			await shot('4b');

			await page.getByRole('button', { name: 'Install modes' }).first().click();
			const sheet = page.locator('[data-mode-install-sheet]');
			await expect(sheet).toBeVisible();
			await expect(sheet.locator('[data-mode-row]')).toHaveCount(12);
			await expect(sheet.locator('[data-mode-row="video"] input')).toBeDisabled();
			await expect(sheet.locator('[data-total-download]')).toHaveText('4.9 GB');
			await expect(sheet.locator('[data-total-free]')).toHaveText('812 GB');
			await expect(sheet.locator('[data-total-time]')).toContainText('About 3 min');
			await expect(sheet.locator('[data-mode-gated]')).toContainText('gated on Hugging Face');
			await expect(page.getByRole('button', { name: /^Install 4 modes/ })).toBeEnabled();
			await shot('1');
			if (width === 1440) {
				await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
				await screenshot(page, JOURNEY, `1-${width}-light`);
				await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
			}

			await sheet.locator('[data-customize-toggle]').click();
			await expect(sheet.locator('[data-variant="int8"]')).toHaveAttribute('aria-checked', 'true');
			await sheet.locator('[data-variant="nvfp4"]').click();
			await expect(sheet.locator('[data-total-download]')).toHaveText('16.3 GB');
			await expect(page.getByRole('button', { name: 'Install 4 modes and nvfp4' })).toBeVisible();
			await sheet.locator('[data-customize-toggle]').scrollIntoViewIfNeeded();
			await shot('1b');
			await sheet.locator('[data-customize-toggle]').click();
			await expect(sheet.locator('[data-mode-customize]')).toHaveCount(0);

			await sheet.locator('[data-target="pod"]').click();
			await expect(sheet.locator('[data-mode-disk-short]')).toBeVisible();
			await expect(sheet.locator('[data-customize-toggle]')).toBeVisible();
			await expect(page.locator('[data-mode-blocker]')).toContainText('Deselect');
			await expect(page.getByRole('button', { name: /^Install 4 modes/ })).toBeDisabled();
			await expect(sheet.locator('[data-total-time]')).toContainText('—');
			await shot('2');
			expect(mock.plans).toEqual([{ preselect_modes: ['layout', 'cinemagraph', 'motion', 'refine'], target: 'pod' }]);

			await sheet.locator('[data-install-local]').click();
			await expect(sheet.locator('[data-mode-disk-short]')).toHaveCount(0);
			await expect(page.getByRole('button', { name: /^Install 4 modes/ })).toBeEnabled();
			await page.getByRole('button', { name: /^Install 4 modes/ }).click();
			await expect(page.locator('[data-mode-downloads]')).toBeVisible();
			expect(mock.grants).toHaveLength(1);
			expect(mock.grants[0]).toMatchObject({ modes: ALL, extras: [], target: 'native', selections: {} });
			const downloads = page.locator('[data-mode-downloads]');
			await expect(downloads.locator('[data-download-file]')).toHaveCount(4);
			await expect(downloads.locator('[data-download-file="cine_lora"]')).toContainText('Done');
			await expect(downloads.locator('[data-download-file="face_refiner"]')).toContainText('Waiting');
			await expect(downloads).toContainText('58 MB/s');
			await expect(page.getByText('Installing 4 modes')).toBeVisible();
			await expect(page.locator('[data-mode-step="modes.plan"]')).toContainText('4 modes, 4 files, nothing duplicated');
			await expect(page.locator('[data-mode-step="fetch"]')).toContainText('2 of 4 running');
			await expect(page.locator('[data-mode-step="defaults"]')).toContainText('Fills empty choices only');
			await shot('5a');

			mock.phase = 'done';
			await expect(page.locator('[data-mode-install-done]')).toBeVisible({ timeout: 15000 });
			await expect(page.locator('[data-mode-install-done]')).toContainText('Ready: Layout to render, Cinemagraph, Motion transfer, Refine faces');
			await expect(page.locator('[data-try-mode]')).toHaveCount(4);
			await expect(page.locator('[data-mode-access-heading]')).toContainText('Who can use LTX-2.5');
			await expect(page.getByText('Installed 4 modes')).toBeVisible();
			await shot('5b');

			mock.phase = 'none';
			await openRecipes(page, RECIPE_ID);
			await page.locator('[data-recipe-mode="upscale"]').getByRole('button', { name: 'Remove files' }).click();
			const remove = page.locator('[data-mode-remove]');
			await expect(remove.locator('[data-remove-deleted]')).toContainText('ltx-2.5-spatial-upscaler-x2.safetensors');
			await expect(remove.locator('[data-remove-kept]')).toContainText('Used by Video and 9 other modes');
			await expect(remove.locator('[data-remove-kept]')).toContainText('Also used by Restore');
			await expect(remove.locator('[data-remove-kept]')).toContainText('ltx-upscale-custom.safetensors');
			await expect(page.getByRole('button', { name: 'Remove 1 file' })).toBeVisible();
			await shot('6');
			await page.getByRole('button', { name: 'Remove 1 file' }).click();
			await expect(remove).toHaveCount(0);
			expect(mock.removals).toEqual([{ model_ids: ['d1'] }]);
		});
	});
}

function patchModes(body: any, admin: boolean) {
	const extra = MODES.slice(7, 12).filter((m) => !m.ready);
	const synthetic = [
		...extra.map((m) => ({
			name: m.id,
			label: m.label,
			icon: null,
			variants: [],
			short_description: m.desc,
			requirements_missing: ['file'],
			...(admin ? { install: { recipe_id: RECIPE_ID, ready: false, missing_bytes: m.missing } } : {})
		})),
		{ name: 'upscale', label: 'Upscale', icon: null, variants: [], short_description: 'Sharper video at a higher resolution', requirements_missing: null, ...(admin ? { install: { recipe_id: RECIPE_ID, ready: true, missing_bytes: 0 } } : {}) }
	];
	const real = (body.data?.modes ?? []).map((mode: any) => {
		const { install: _install, ...rest } = mode;
		return { ...rest, requirements_missing: null };
	});
	return { ...body, data: { ...body.data, modes: [...real, ...synthetic] } };
}

for (const width of [1440, 390]) {
	test.describe(`generate mode picker at ${width}px`, () => {
		test.use({ viewport: { width, height: width === 1440 ? 900 : 844 } });

		for (const audience of ['admin', 'user'] as const) {
			test(`${audience} sees the right action on a not-set-up mode`, async ({ page }) => {
				await loginAsOwner(page);
				const admin = audience === 'admin';
				await page.route(/\/api\/presets\/[^/]+\/modes(\?.*)?$/, async (route) => {
					const response = await route.fetch();
					await route.fulfill({ response, json: patchModes(await response.json(), admin) });
				});
				if (!admin) {
					await page.route(/\/api\/auth\/me(\?.*)?$/, async (route) => {
						const response = await route.fetch();
						const json = await response.json();
						if (json?.data) json.data.account_type = 'USER';
						await route.fulfill({ response, json });
					});
				}

				let modeHost = page.getByTestId('preset-header-mode');
				if (width === 1440) {
					const preset = await installAndSelectImagePreset(page);
					test.skip(!preset, 'No image preset is available on this throwaway instance.');
				} else {
					const preset = await discoverAndAssignImagePreset(page);
					test.skip(!preset, 'No image preset is available on this throwaway instance.');
					await page.goto('/generate');
					await page.getByRole('button', { name: 'Open preset and session' }).click();
					const sheet = page.getByRole('dialog', { name: 'Preset and session' });
					await expect(sheet).toBeVisible({ timeout: 5000 });
					await sheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' }).click();
					await presetOption(page, preset!.name).click();
					await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
					modeHost = sheet.getByTestId('preset-header-mode');
				}

				await expect(modeHost).toBeVisible({ timeout: 20000 });
				await modeHost.getByRole('button').first().click();
				const list = page.getByRole('listbox', { name: 'Choose mode' });
				await expect(list).toBeVisible();
				await expect(list.getByRole('option', { name: /Cinemagraph/ })).toBeVisible();
				if (admin) {
					await expect(list.locator('[data-option-action]')).toHaveCount(4);
					await expect(list.locator('[data-option-action]').first()).toContainText('Install · 1.4 GB');
					await expect(list.locator('[data-option-notice]')).toHaveCount(0);
				} else {
					await expect(list.locator('[data-option-action]')).toHaveCount(0);
					await expect(list.locator('[data-option-notice]').first()).toContainText('Not set up yet - ask an admin');
				}
				await screenshot(page, JOURNEY, `3-${audience}-${width}-dark`);
			});
		}
	});
}
