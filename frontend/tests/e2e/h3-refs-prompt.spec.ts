import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';

const JOURNEY = 'h3-refs-prompt';

async function openH3(page: Page) {
	await loginAsOwner(page);
	const preset = await assignPreset(page, 'MiniMax-H3');
	test.skip(!preset, 'MiniMax-H3 preset is not available on this instance');
	await page.goto('/generate');
	await startFromNoTabs(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);
}

async function chooseMode(page: Page, label: RegExp) {
	await page.getByTestId('preset-header-mode').locator('button').first().click();
	await page.getByRole('option').filter({ hasText: label }).first().click();
}

test.describe('MiniMax-H3 References prompt', () => {
	test('References shows an editable prompt with no frame after a start frame was picked in Video', async ({ page }) => {
		test.setTimeout(120000);
		await page.setViewportSize({ width: 1440, height: 960 });
		await openH3(page);
		await chooseMode(page, /Video Director/);

		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 20000 });
		await director.locator('.kf-anchor').first().click();
		await expect(director.getByText('Drop an image')).toBeVisible();

		await chooseMode(page, /References/);
		await expect(page.getByTestId('preset-header-mode')).toContainText('References');
		await expect(director.locator('.kf-anchor')).toHaveCount(0);
		await expect(director.getByText('Drop an image')).toHaveCount(0);

		const editor = director.locator('[contenteditable="true"]').first();
		await expect(editor).toBeVisible();
		await editor.click();
		await page.keyboard.type('a lighthouse at dusk');
		await expect(editor).toContainText('a lighthouse at dusk');
		await director.scrollIntoViewIfNeeded();
		await screenshot(page, JOURNEY, 'refs-1440');

		await page.setViewportSize({ width: 390, height: 844 });
		await page.getByText('a lighthouse at dusk').first().click();
		const sheetDirector = page.locator('section.video-director[aria-label="Video Director"]:visible').first();
		await expect(sheetDirector).toBeVisible({ timeout: 20000 });
		await expect(sheetDirector.locator('[contenteditable="true"]').first()).toContainText('a lighthouse at dusk');
		await screenshot(page, JOURNEY, 'refs-390');
	});
});
