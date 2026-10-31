import { test, expect, type Page } from '@playwright/test';
import { clearTabDrafts, loginAsOwner, ownerToken, screenshot } from './helpers';
import { presetOption } from './presetPreamble';

const JOURNEY = 'mobile-generate-without-settings';
const GENERATION_ID = '01MOBILENOSETTINGS0000001';
const SLOTS = ['diffusion_model', 'text_encoder', 'vae'] as const;
const PRESETS = [
	{ name: 'Z-Image', slug: 'zimage' },
	{ name: 'Krea-2', slug: 'krea2' },
	{ name: 'Qwen-Image', slug: 'qwen-image' }
];

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

function modelRow(id: string, modelType: string) {
	const now = new Date().toISOString();
	return {
		id,
		filename: `${id}.safetensors`,
		model_type: modelType,
		file_size: 4_000_000_000,
		sha256: id.padEnd(64, '0'),
		created_at: now,
		updated_at: now,
		indexed_at: now,
		is_available: true,
		is_favorite: false,
		tags: [],
		providers: [],
		files: [],
		backend_ids: ['native']
	};
}

async function mockInstalledModels(page: Page, perSlot: number) {
	const searches: string[] = [];
	await page.route(
		(url) => /^\/api\/presets\/[^/]+\/models$/.test(url.pathname),
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			const type = new URL(route.request().url()).searchParams.get('model_type') ?? 'checkpoint';
			searches.push(type);
			const models = Array.from({ length: perSlot }, (_, index) => modelRow(`e2e-${type}-${index + 1}`, type));
			await route.fulfill({
				json: {
					success: true,
					data: { engine: 'native', models, total: models.length, indexed: true, availability_indexed: true }
				}
			});
		}
	);
	return searches;
}

async function captureGenerationStart(page: Page) {
	const calls: Array<{ body: any; status: number }> = [];
	await page.route('**/api/generations/start', async (route) => {
		const body = route.request().postDataJSON();
		const formData = body?.form_data ?? {};
		const missing = SLOTS.filter((slot) => typeof formData[slot] !== 'string' || !formData[slot]);
		if (missing.length > 0) {
			calls.push({ body, status: 422 });
			await route.fulfill({
				status: 422,
				json: {
					error: 'form_validation_failed',
					field_errors: Object.fromEntries(missing.map((slot) => [slot, ['This field is required.']]))
				}
			});
			return;
		}
		calls.push({ body, status: 200 });
		await route.fulfill({
			json: {
				success: true,
				data: {
					generation_id: GENERATION_ID,
					status: {
						id: GENERATION_ID,
						generation_id: GENERATION_ID,
						status: 'pending',
						progress: 0.0,
						created_at: new Date().toISOString()
					},
					queue_position: null
				}
			}
		});
	});
	return calls;
}

async function assignPreset(page: Page, name: string) {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await (await page.request.get('/api/presets?include_uninstalled=true', { headers })).json();
	const preset = ((list.data || []) as Array<{ id: string; name: string; installed?: boolean }>).find(
		(candidate) => candidate.name === name
	);
	if (!preset) return null;
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { headers, data: {} });
	const me = await (await page.request.get('/api/auth/me', { headers })).json();
	await page.request.post(`/api/presets/${preset.id}/assign`, { headers, data: { user_ids: [me.data.id] } });
	return preset;
}

async function choosePresetOnPhone(page: Page, name: string) {
	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Open preset and session' }).click();
	const sheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(sheet).toBeVisible({ timeout: 5000 });
	await sheet.locator('button[aria-haspopup="dialog"]').first().click();
	await expect(page.locator('[role="listbox"][aria-label="Presets"]')).toBeVisible({ timeout: 15000 });
	await presetOption(page, name).click();
	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
	await page.keyboard.press('Escape');
	await expect(sheet).toHaveCount(0);
}

async function typePrompt(page: Page, text: string) {
	await page.getByRole('button', { name: 'Prompt', exact: true }).click();
	const editor = page.locator('[contenteditable="true"]').first();
	await expect(editor).toBeVisible({ timeout: 8000 });
	await editor.click();
	await page.keyboard.type(text);
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Prompt', exact: true })).toHaveCount(0);
}

async function openFreshPhoneSession(page: Page, presetName: string) {
	await loginAsOwner(page);
	const preset = await assignPreset(page, presetName);
	expect(preset, `${presetName} ships with the app`).not.toBeNull();
	await clearTabDrafts(page);
	await page.evaluate(() => {
		for (const key of Object.keys(localStorage)) if (key.startsWith('potionui_tabs_state')) localStorage.removeItem(key);
	});
	await page.goto('/generate');
	await choosePresetOnPhone(page, preset!.name);
	await typePrompt(page, 'a glass potion bottle on a wooden alchemist bench');
}

for (const preset of PRESETS) {
	test(`${preset.name}: Generate without opening Settings sends the auto-picked models`, async ({ page }) => {
		test.setTimeout(120000);
		const searches = await mockInstalledModels(page, 1);
		const calls = await captureGenerationStart(page);
		await openFreshPhoneSession(page, preset.name);

		await expect(page.getByRole('dialog', { name: 'Settings' })).toHaveCount(0);
		const generate = page.locator('.generation-panel .generate-button');
		await expect(generate).toBeEnabled({ timeout: 15000 });
		await expect(page.locator('.generation-panel')).not.toContainText(/Select .+ to generate/);
		await generate.click();

		await expect.poll(() => calls.length, { timeout: 15000 }).toBe(1);
		expect(calls[0].status).toBe(200);
		const formData = calls[0].body.form_data;
		for (const slot of SLOTS) expect(formData[slot], slot).toMatch(/^model:e2e-.+-1$/);
		await expect(page.getByRole('dialog', { name: 'Settings' })).toHaveCount(0);
		expect(searches.length, 'each slot is searched once, not in a loop').toBeLessThanOrEqual(SLOTS.length);
		await screenshot(page, JOURNEY, `${preset.slug}-generated`);
	});
}

test('Z-Image with two installed candidates per slot keeps Generate off and names the missing model', async ({ page }) => {
	test.setTimeout(120000);
	await mockInstalledModels(page, 2);
	const calls = await captureGenerationStart(page);
	await openFreshPhoneSession(page, 'Z-Image');

	await expect(page.locator('.generation-panel')).toContainText(/Select .+ to generate/, { timeout: 15000 });
	await expect(page.locator('.generation-panel .generate-button')).toBeDisabled();
	expect(calls).toHaveLength(0);
	await screenshot(page, JOURNEY, 'zimage-two-candidates');
});
