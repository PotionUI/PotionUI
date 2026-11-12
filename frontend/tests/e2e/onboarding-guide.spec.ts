import { test, expect, type Page } from '@playwright/test';
import { fulfillJson, screenshot, SIGNED_OUT } from './helpers';

const JOURNEY = 'onboarding-guide';
const GIB = 1024 ** 3;

const MACHINE = {
	gpu: { name: 'NVIDIA GeForce RTX 4090', vram_bytes: 23.6 * GIB, compute_capability: '8.9', supported: true },
	ram_total_bytes: 64 * GIB,
	models_disk: { path: '/data/potionui/models', free_bytes: 412 * GIB, total_bytes: 900 * GIB }
};

function recipe(overrides: Record<string, unknown>) {
	return {
		id: 'r',
		name: 'R',
		summary: 'summary',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 3,
		total_download_bytes: 10 * GIB,
		presets: [],
		last_completed_at: null,
		blurb: 'blurb',
		min_vram_gb: null,
		recommended_vram_gb: null,
		sources: ['huggingface.co'],
		first_run: true,
		...overrides
	};
}

const RECIPES = [
	recipe({
		id: 'qwen-image-starter',
		name: 'Qwen-Image Starter',
		blurb: 'Strong at readable text inside images. The biggest download here.',
		recommended_vram_gb: 16,
		total_download_bytes: 28 * GIB
	}),
	recipe({
		id: 'sdxl-starter',
		name: 'SDXL Starter',
		blurb: 'The classic all-rounder.',
		min_vram_gb: 8,
		total_download_bytes: 6.62 * GIB
	}),
	recipe({
		id: 'zimage-starter',
		name: 'Z-Image Starter',
		blurb: 'Fast and compact.',
		min_vram_gb: 12,
		recommended_vram_gb: 16,
		total_download_bytes: 19.27 * GIB
	})
];

const NOT_READY = {
	overall: 'not_ready',
	checks: [
		{ area: 'content', status: 'not_ready', code: 'no_presets', message: 'No presets are installed.', action: null }
	]
};

async function noHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return el.scrollWidth - el.clientWidth;
	});
	expect(overflow, 'page must not scroll horizontally').toBeLessThanOrEqual(0);
}

async function mockGuideApi(page: Page, consentRun?: unknown) {
	await page.addInitScript(() => {
		for (const key of Object.keys(localStorage)) {
			if (key.startsWith('potionui:setup:')) localStorage.removeItem(key);
		}
	});
	await page.route('**/api/readiness*', (route) => fulfillJson(route, NOT_READY));
	await page.route('**/api/setup/machine', (route) => fulfillJson(route, MACHINE));
	await page.route('**/api/setup/recipes', (route) => fulfillJson(route, { recipes: RECIPES }));
	await page.route('**/api/setup/runs/active', (route) => fulfillJson(route, { detail: 'none' }, 404));
	if (consentRun) {
		await page.route('**/api/setup/runs', (route) => {
			if (route.request().method() === 'POST') return fulfillJson(route, consentRun);
			return route.fallback();
		});
		await page.route('**/api/setup/runs/run-1', (route) => fulfillJson(route, consentRun));
	}
}

test.describe('claim explainer', () => {
	test.use({ storageState: SIGNED_OUT });

	for (const viewport of [
		{ name: '1440', width: 1440, height: 900 },
		{ name: '390', width: 390, height: 844 }
	]) {
		test(`welcome then account explainer at ${viewport.name}`, async ({ page }) => {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			await page.route('**/api/setup/status', (route) =>
				fulfillJson(route, { needs_owner: true, registration_open: true, claim_requires_token: false })
			);
			await page.goto('/setup/claim');

			await expect(page.getByRole('heading', { name: 'A local studio for AI images and video' })).toBeVisible({
				timeout: 20000
			});
			await expect(page.getByText('You will need')).toBeVisible();
			await expect(page.getByText('Disk space')).toBeVisible();
			await expect(page.getByText('Internet', { exact: true })).toBeVisible();
			await expect(page.getByText('What setup will do')).toHaveCount(0);
			await expect(page.getByText('ComfyUI is optional')).toHaveCount(0);
			await expect(page.getByRole('button', { name: 'Skip the guide, go to the app' })).toHaveCount(0);
			await noHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `welcome-${viewport.name}`);

			await page.getByRole('button', { name: 'Get started' }).click();
			await expect(page.getByRole('heading', { name: 'Create your owner account' })).toBeVisible();
			await expect(page.getByText('This account lives only on this computer.')).toBeVisible();
			await expect(page.getByText('storage/db.sqlite')).toBeVisible();
			await expect(page.getByText('Sign-up is closed once you exist.')).toBeVisible();
			await expect(page.getByText('Step 2 of 6 - Required')).toBeVisible();
						await expect(page.locator('label[for="email"]')).toHaveText(/^\s*Email\s*$/);
			await expect(page.getByRole('button', { name: 'Create owner account' })).toBeVisible();
			await expect(page.getByRole('button', { name: 'Skip guide' })).toHaveCount(0);
			await noHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `account-${viewport.name}`);

			await page.getByRole('button', { name: 'Back', exact: true }).click();
			await expect(page.getByRole('heading', { name: 'A local studio for AI images and video' })).toBeVisible();
		});
	}

	test('a returning visitor lands on the account step', async ({ page }) => {
		await page.route('**/api/setup/status', (route) =>
			fulfillJson(route, { needs_owner: true, registration_open: true, claim_requires_token: false })
		);
		await page.goto('/setup/claim');
		await page.getByRole('button', { name: 'Get started' }).click();
		await page.reload();
		await expect(page.getByRole('heading', { name: 'Create your owner account' })).toBeVisible({ timeout: 20000 });
	});
});

test.describe('guide in the app shell', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('stepper progresses and the starter cards show the plain line, the model, the size and the fit', async ({ page }) => {
		await mockGuideApi(page);
		await page.goto('/setup');

		await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible({ timeout: 20000 });
		await expect(page.locator('[aria-current="step"]')).toContainText('Computer');
		await expect(page.getByText('NVIDIA GeForce RTX 4090')).toBeVisible();
		await expect(page.getByText('· 24 GB')).toBeVisible();
		await expect(page.getByText('Good fit.')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Back to Account' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'computer');

		await page.getByRole('button', { name: 'Continue' }).click();

		await expect(page.getByRole('heading', { name: 'Where should PotionUI keep your models?' })).toHaveCount(0);
		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
		await expect(page.locator('[aria-current="step"]')).toContainText('Starter');
		const cards = page.getByRole('radio');
		await expect(cards).toHaveCount(3);
		await expect(cards.nth(0)).toContainText('Fast and compact.');
		await expect(cards.nth(0)).toContainText('Z-Image');
		await expect(cards.nth(0)).toContainText('Recommended for your card');
		await expect(cards.nth(0)).toHaveAttribute('aria-checked', 'true');
		await expect(cards.nth(0)).toContainText('19.27 GB download');
		await expect(cards.nth(0)).toContainText('Fits your card');
		await expect(cards.nth(0)).not.toContainText('huggingface.co');
		await expect(cards.nth(0)).not.toContainText('Graphics memory');
		await expect(cards.nth(1)).toContainText('The classic all-rounder.');
		await expect(cards.nth(1)).toContainText('SDXL');
		await expect(cards.nth(1)).toContainText('6.62 GB download');
		await expect(cards.nth(2)).toContainText('Strong at readable text inside images.');
		await expect(cards.nth(2)).toContainText('Qwen-Image');
		await expect(cards.nth(2)).toContainText('28 GB download');
		await expect(page.getByText('Saved to')).toBeVisible();
		await expect(page.getByText('/data/potionui/models')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Change' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Use a folder you already have' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Review 19.27 GB download' })).toBeVisible();
		await screenshot(page, JOURNEY, 'starter');

		await cards.nth(1).click();
		await expect(page.getByRole('button', { name: 'Review 6.62 GB download' })).toBeVisible();
		await page.keyboard.press('ArrowDown');
		await expect(cards.nth(2)).toHaveAttribute('aria-checked', 'true');

		await page.getByRole('button', { name: 'Back to Computer' }).click();
		await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible();
	});

	test('skip leads to the app and Resume setup in the sidebar leads back', async ({ page }) => {
		await mockGuideApi(page);
		await page.goto('/setup');
		await page.getByRole('button', { name: 'Continue' }).click();
		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();

		await page.getByRole('button', { name: 'Skip the guide, go to the app' }).click();
		await page.waitForURL(/\/generate/);
		await expect(
			page.getByText('Guide skipped. Pick it up again any time from Resume setup.')
		).toBeVisible();
		await screenshot(page, JOURNEY, 'skipped-landing');

		const resume = page.getByRole('link', { name: 'Resume setup' });
		await expect(resume).toBeVisible({ timeout: 15000 });
		await resume.click();
		await page.waitForURL(/\/setup/);
		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
	});

	test('the token disclosure on the consent card saves through the plugin settings route', async ({ page }) => {
		const consentRun = {
			id: 'run-1',
			recipe_id: 'zimage-starter',
			recipe_version: 1,
			scope: 'instance',
			mode: 'onboarding',
			status: 'awaiting_consent',
			current_step: 'plan',
			safe_input: null,
			safe_output: null,
			error_code: null,
			safe_error_detail: null,
			created_at: null,
			updated_at: null,
			completed_at: null,
			attempts: [],
			steps: [
				{
					step_key: 'plan',
					title: 'Plan required downloads',
					kind: 'artifacts.plan',
					ordinal: 0,
					status: 'awaiting_consent',
					attempts: [
						{
							step_key: 'plan',
							attempt: 1,
							status: 'awaiting_consent',
							progress_current: null,
							progress_total: null,
							progress_unit: null,
							safe_output: {
								consent_request: {
									artifacts: [
										{
											id: 'a1',
											display_name: 'Z-Image Turbo',
											size_bytes: 12 * GIB,
											kind: 'diffusion_model',
											gated: false,
											license_url: null
										}
									],
									total_bytes: 12 * GIB,
									providers: [
										{
											id: 'huggingface',
											name: 'Hugging Face',
											website: 'https://huggingface.co/settings/tokens',
											field_name: 'api_key',
											configured: false
										}
									]
								}
							},
							error_code: null,
							safe_error_detail: null,
							safe_suggested_action: null,
							started_at: null,
							finished_at: null
						}
					]
				}
			]
		};
		await mockGuideApi(page, consentRun);
		let saved: unknown = null;
		await page.route('**/api/plugins/huggingface-provider/settings', async (route) => {
			saved = route.request().postDataJSON();
			await fulfillJson(route, { data: [{ key: 'api_key', value: '***' }] });
		});

		await page.goto('/setup');
		await page.getByRole('button', { name: 'Continue' }).click();
		await page.getByRole('button', { name: 'Continue' }).click();
		await page.getByRole('button', { name: /^Review .+ download$/ }).click();

		await expect(page.getByRole('heading', { name: 'Review the download' })).toBeVisible();
		await expect(page.getByText('Image model')).toBeVisible();
		await expect(page.getByText(/Downloads come from Hugging Face \(huggingface\.co\)/)).toBeVisible();

		const disclosure = page.locator('details', { hasText: 'Add a token (optional)' });
		await expect(disclosure).not.toHaveJSProperty('open', true);
		await disclosure.locator('summary').click();
		await expect(disclosure.getByText('Stored on this computer.')).toBeVisible();
		await expect(disclosure.getByRole('button', { name: 'Save' })).toBeDisabled();
		await disclosure.locator('input[type="password"]').fill('hf_example_token');
		await disclosure.getByRole('button', { name: 'Save' }).click();

		await expect(page.getByText('Hugging Face API key saved.')).toBeVisible();
		expect(saved).toEqual({ settings: { api_key: 'hf_example_token' } });
		await screenshot(page, JOURNEY, 'consent-token-saved');
	});
});

test.describe('guide on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('skip promises Settings and Resume setup there leads back', async ({ page }) => {
		await mockGuideApi(page);
		await page.goto('/setup');

		await expect(page.getByText('Step 3 of 6').first()).toBeVisible({ timeout: 20000 });
		await noHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'computer-390');

		await page.getByRole('button', { name: 'Skip the guide, go to the app' }).click();
		await page.waitForURL(/\/generate/);
		await expect(
			page.getByText('Guide skipped. Pick it up again any time from Settings, under Resume setup.')
		).toBeVisible();

		await page.goto('/settings');
		const row = page.getByRole('button', { name: 'Resume setup' });
		await expect(row).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'settings-resume-390');
		await row.click();
		await page.waitForURL(/\/setup/);
		await expect(page.getByText('Step 3 of 6').first()).toBeVisible();
	});
});

function smokeAttempt(overrides: Record<string, unknown>) {
	return {
		step_key: 'generation.smoke',
		attempt: 1,
		status: 'action_required',
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: null,
		error_code: 'GENERATION_SMOKE_FAILED',
		safe_error_detail: "The test didn't finish: it took longer than 5 minutes.",
		safe_suggested_action: 'Make a picture in Generate to try again.',
		safe_technical_detail: 'timed out waiting for completion after 300s',
		started_at: null,
		finished_at: '2026-11-12T10:00:00Z',
		...overrides
	};
}

function succeededStep(kind: string, ordinal: number, output: Record<string, unknown>) {
	return {
		step_key: kind,
		title: kind,
		kind,
		ordinal,
		status: 'succeeded',
		attempts: [
			{
				step_key: kind,
				attempt: 1,
				status: 'succeeded',
				progress_current: null,
				progress_total: null,
				progress_unit: null,
				safe_output: output,
				error_code: null,
				safe_error_detail: null,
				safe_suggested_action: null,
				started_at: null,
				finished_at: '2026-11-12T10:00:00Z'
			}
		]
	};
}

const COMPLETED_WITH_FAILED_TEST = {
	id: 'run-done',
	recipe_id: 'zimage-starter',
	recipe_version: 2,
	scope: 'instance',
	mode: 'onboarding',
	status: 'completed',
	current_step: 'workspace.activate',
	safe_input: { card_memory_gb: 6, needed_memory_gb: 12, below_graphics_memory: true },
	safe_output: null,
	error_code: null,
	safe_error_detail: null,
	created_at: null,
	updated_at: null,
	completed_at: '2026-11-12T10:01:00Z',
	attempts: [],
	steps: [
		succeededStep('preset.ensure', 1, { preset_id: 'preset-zimage' }),
		succeededStep('pipeline.render', 2, { mode: 'txt2img' }),
		{
			step_key: 'generation.smoke',
			title: 'Test picture',
			kind: 'generation.smoke',
			ordinal: 3,
			status: 'action_required',
			attempts: [smokeAttempt({})]
		},
		succeededStep('workspace.activate', 4, {})
	]
};

test.describe('a card below the starter and a test that did not finish', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the small-card confirm can be cancelled, then accepted, and the run starts with the acknowledgement', async ({
		page
	}) => {
		const consentRun = { ...COMPLETED_WITH_FAILED_TEST, status: 'awaiting_consent', steps: [], safe_input: {} };
		await mockGuideApi(page, consentRun);
		await page.route('**/api/setup/machine', (route) =>
			fulfillJson(route, {
				...MACHINE,
				gpu: { ...MACHINE.gpu, vram_bytes: 6 * GIB }
			})
		);
		let startBody: any = null;
		await page.route('**/api/setup/runs', (route) => {
			if (route.request().method() === 'POST') startBody = route.request().postDataJSON();
			return route.fulfill({ json: consentRun });
		});

		await page.goto('/setup');
		await page.getByRole('button', { name: 'Continue' }).click();
		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
		await page.getByRole('radio', { name: /Fast and compact\./ }).click();
		await page.getByRole('button', { name: /^Review .* download$/ }).click();

		const dialog = page.getByRole('alertdialog');
		await expect(dialog).toContainText('This starter needs a bigger graphics card');
		await expect(dialog).toContainText('Needs a 12 GB graphics card. Yours has 6 GB.');
		await expect(dialog).toContainText('It will probably not run here.');
		await screenshot(page, JOURNEY, 'small-card-confirm');

		await dialog.getByRole('button', { name: 'Pick another' }).click();
		await expect(dialog).toHaveCount(0);
		expect(startBody).toBeNull();

		await page.getByRole('button', { name: /^Review .* download$/ }).click();
		await page.getByRole('alertdialog').getByRole('button', { name: 'Install anyway' }).click();
		await expect.poll(() => startBody).not.toBeNull();
		expect(startBody.recipe_id).toBe('zimage-starter');
		expect(startBody.safe_input).toEqual({
			card_memory_gb: 6,
			needed_memory_gb: 12,
			below_graphics_memory: true
		});
	});

	test('a finished run whose test did not finish says so on Ready, and still does after a reload', async ({ page }) => {
		await mockGuideApi(page);
		await page.route('**/api/recipes/runs*', (route) =>
			fulfillJson(route, { runs: [COMPLETED_WITH_FAILED_TEST] })
		);
		await page.route('**/api/setup/runs/run-done', (route) => fulfillJson(route, COMPLETED_WITH_FAILED_TEST));

		for (const attempt of ['first load', 'after a reload']) {
			if (attempt === 'after a reload') await page.reload();
			else await page.goto('/setup');
			await expect(page.getByRole('heading', { name: /Z-Image Starter is installed/ })).toBeVisible({
				timeout: 20000
			});
			await expect(page.getByText('Test failed')).toBeVisible();
			await expect(page.getByText("The test didn't finish: it took longer than 5 minutes.")).toBeVisible();
			await expect(page.getByText('Make a picture in Generate to try again.')).toBeVisible();
			await expect(page.getByText('This card is below what Z-Image Starter needs.')).toBeVisible();
			await expect(page.getByRole('button', { name: 'Try it in Generate' })).toBeVisible();
			await expect(page.getByRole('link', { name: 'Run the check again' })).toHaveAttribute(
				'href',
				'/admin?tab=recipes'
			);
			await expect(page.getByRole('button', { name: 'Create your first image' })).toHaveCount(0);
			await expect(page.getByText('a test image was made')).toHaveCount(0);
			const details = page.locator('details', { hasText: 'timed out waiting for completion after 300s' });
			await expect(details).not.toHaveJSProperty('open', true);
		}
		await screenshot(page, JOURNEY, 'ready-test-failed');
	});

	test('Try it in Generate opens the preset the run installed', async ({ page }) => {
		await mockGuideApi(page);
		await page.route('**/api/recipes/runs*', (route) =>
			fulfillJson(route, { runs: [COMPLETED_WITH_FAILED_TEST] })
		);
		await page.route('**/api/setup/runs/run-done', (route) => fulfillJson(route, COMPLETED_WITH_FAILED_TEST));
		await page.goto('/setup');
		await page.getByRole('button', { name: 'Try it in Generate' }).click();
		await page.waitForURL(/\/generate/);
	});
});

test.describe('a computer with no NVIDIA card', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	async function noCard(page: Page) {
		await mockGuideApi(page);
		await page.route('**/api/setup/machine', (route) => fulfillJson(route, { ...MACHINE, gpu: null }));
	}

	test('Computer, Starter and Ready all name the three ways to make pictures somewhere else', async ({ page }) => {
		await noCard(page);
		await page.goto('/setup');
		await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible({ timeout: 20000 });
		await expect(page.getByText('another computer with an NVIDIA card')).toBeVisible();
		await page.getByRole('button', { name: 'Continue' }).click();

		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible();
		await expect(
			page.getByText('This computer has no NVIDIA graphics card, so pictures are made somewhere else.')
		).toBeVisible();
		for (const title of ['Another computer with an NVIDIA card', 'A ComfyUI server', 'An online service']) {
			await expect(page.getByText(title, { exact: true })).toBeVisible();
		}
		await expect(page.getByRole('button', { name: /^Review/ })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'starter-no-card');

		await page.getByRole('button', { name: 'Continue without a starter' }).click();
		await expect(page.getByRole('heading', { name: 'Your account is ready' })).toBeVisible();
		await expect(page.getByText('An online service', { exact: true })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Create your first image' })).toHaveCount(0);
		await expect(page.getByRole('link', { name: 'Connect a backend' })).toHaveAttribute(
			'href',
			'/admin?tab=backends'
		);
		await screenshot(page, JOURNEY, 'ready-no-card');
	});

	test('each option links to its admin tab and its how-to page', async ({ page }) => {
		await noCard(page);
		await page.goto('/setup');
		await page.getByRole('button', { name: 'Continue' }).click();
		const rows = page.locator('[data-no-card-option]');
		await expect(rows).toHaveCount(3);
		await expect(rows.nth(0).getByRole('link', { name: 'How to' })).toHaveAttribute('href', '/docs?doc=remote-native');
		await expect(rows.nth(1).getByRole('link', { name: 'How to' })).toHaveAttribute(
			'href',
			'/docs?doc=user/comfyui-presets'
		);
		await expect(rows.nth(2).getByRole('link', { name: 'Admin → Plugins' })).toHaveAttribute(
			'href',
			'/admin?tab=plugins'
		);
	});
});
