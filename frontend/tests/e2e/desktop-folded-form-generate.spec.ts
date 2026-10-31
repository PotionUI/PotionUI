import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { presetOption } from './presetPreamble';
import { SLOTS, assignPreset, captureGenerationStart, mockInstalledModels, startFromNoTabs } from './formSeedMocks';

const JOURNEY = 'desktop-folded-form-generate';

test.use({ viewport: { width: 1440, height: 900 } });

async function choosePreset(page: Page, name: string) {
	await page.getByTestId('preset-picker-trigger').click();
	await expect(page.locator('[role="listbox"][aria-label="Presets"]')).toBeVisible({ timeout: 15000 });
	await presetOption(page, name).click();
	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
}

test('a folded form pane still sends the auto-picked models after a preset switch', async ({ page }) => {
	test.setTimeout(120000);
	await mockInstalledModels(page, 1);
	const calls = await captureGenerationStart(page);
	await loginAsOwner(page);
	const first = await assignPreset(page, 'Z-Image');
	const second = await assignPreset(page, 'Krea-2');
	expect(first, 'Z-Image ships with the app').not.toBeNull();
	expect(second, 'Krea-2 ships with the app').not.toBeNull();
	await startFromNoTabs(page);
	await page.goto('/generate');

	await choosePreset(page, first!.name);
	await expect(page.locator('[data-field-name="diffusion_model"]')).toBeVisible({ timeout: 20000 });
	await page.getByRole('button', { name: 'Collapse generation settings' }).click();
	await expect(page.getByRole('button', { name: 'Expand generation settings' })).toBeVisible();
	await expect(page.locator('[data-field-name="diffusion_model"]')).toHaveCount(0);

	await choosePreset(page, second!.name);
	await expect(page.getByRole('button', { name: 'Expand generation settings' })).toBeVisible({ timeout: 15000 });
	const editor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(editor).toBeVisible({ timeout: 20000 });
	await editor.click();
	await page.keyboard.type('a glass potion bottle on a wooden alchemist bench');

	const generate = page.getByRole('button', { name: 'Generate', exact: true });
	await expect(generate).toBeEnabled({ timeout: 15000 });
	await generate.click();

	await expect.poll(() => calls.length, { timeout: 15000 }).toBe(1);
	expect(calls[0].status).toBe(200);
	expect(calls[0].body.preset_id).toBe(second!.id);
	for (const slot of SLOTS) expect(calls[0].body.form_data[slot], slot).toMatch(/^model:e2e-.+-1$/);
	await expect(page.locator('[data-field-name="diffusion_model"]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'krea2-generated-folded');
});
