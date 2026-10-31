import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { presetOption } from './presetPreamble';
import { SLOTS, assignPreset, captureGenerationStart, mockInstalledModels, startFromNoTabs } from './formSeedMocks';

const JOURNEY = 'mobile-generate-without-settings';
const PRESETS = [
	{ name: 'Z-Image', slug: 'zimage' },
	{ name: 'Krea-2', slug: 'krea2' },
	{ name: 'Qwen-Image', slug: 'qwen-image' }
];

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

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
	await startFromNoTabs(page);
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
