import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';

const JOURNEY = 'h3-refmod-citation';
const MODEL_ID = '01E2EREFMOD0000000000FACE1';
const MODEL_NAME = 'E2E Face Bundle';
const HINT = 'This shot uses no references. Type @ to add one.';

function refModRow() {
	const now = new Date().toISOString();
	return {
		id: MODEL_ID,
		name: MODEL_NAME,
		filename: `${MODEL_ID}.safetensors`,
		model_type: 'refmod',
		file_size: 1_000_000,
		sha256: MODEL_ID.padEnd(64, '0'),
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

async function mockRefMods(page: Page) {
	await page.route(
		(url) => /^\/api\/presets\/[^/]+\/models$/.test(url.pathname),
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			const type = new URL(route.request().url()).searchParams.get('model_type');
			const models = type === 'refmod' ? [refModRow()] : [];
			await route.fulfill({
				json: { success: true, data: { engine: 'native', models, total: models.length, indexed: true, availability_indexed: true } }
			});
		}
	);
	await page.route(
		(url) => url.pathname === `/api/models/${MODEL_ID}`,
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			await route.fulfill({ json: { success: true, data: { model: refModRow() } } });
		}
	);
	await page.route(
		(url) => url.pathname === `/api/models/${MODEL_ID}/refmod-members`,
		async (route) => {
			await route.fulfill({
				json: { success: true, data: { members: [{ kind: 'image', name: null }, { kind: 'video', name: null }] } }
			});
		}
	);
}

async function openRefsMode(page: Page) {
	await loginAsOwner(page);
	await mockRefMods(page);
	const preset = await assignPreset(page, 'MiniMax-H3');
	test.skip(!preset, 'MiniMax-H3 preset is not available on this instance');
	await page.goto('/generate');
	await startFromNoTabs(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);
	await page.getByTestId('preset-header-mode').locator('button').first().click();
	await page.getByRole('option').filter({ hasText: /References/ }).first().click();
	await expect(page.getByTestId('preset-header-mode')).toContainText('References');
}

test.describe('MiniMax-H3 Director RefMod citation', () => {
	test('a selected RefMod is listed under @ with its badge, cites as a named chip, and clears the hint', async ({ page }) => {
		test.setTimeout(120000);
		await page.setViewportSize({ width: 1440, height: 960 });
		await openRefsMode(page);

		const formTabs = page.getByRole('tablist', { name: 'tabs', exact: true });
		await formTabs.getByRole('tab', { name: 'References' }).click();
		const refModField = page.locator('[data-field-name="reference_mods"]').first();
		await refModField.scrollIntoViewIfNeeded();
		await refModField.getByRole('button', { name: /Choose RefMods|Add LoRA/ }).first().click();
		await page.getByText(MODEL_NAME).first().click();
		await formTabs.getByRole('tab', { name: 'Generation' }).click();

		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 20000 });
		await director.scrollIntoViewIfNeeded();
		const editor = director.locator('.stage [contenteditable="true"]').first();
		await expect(editor).toBeVisible({ timeout: 20000 });
		await expect(director.getByTestId('shot-no-references-hint')).toHaveText(HINT);

		await editor.click();
		await page.keyboard.type('a lighthouse with @');
		const picker = page.locator('.resource-picker');
		await expect(picker).toBeVisible();
		await picker.locator('.picker-row').filter({ hasText: 'RefMods' }).first().click();
		const entry = picker.locator('.picker-row').filter({ hasText: MODEL_NAME }).first();
		await expect(entry).toBeVisible();
		await expect(entry).toContainText('Picture + Video');
		await screenshot(page, JOURNEY, 'refmod-menu');
		await entry.click();

		await expect(editor.locator('.resource-chip')).toContainText(MODEL_NAME);
		await expect(director.getByTestId('shot-no-references-hint')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'refmod-chip');
	});
});
