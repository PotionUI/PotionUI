import { test, expect } from '@playwright/test';
import { loginAsOwner, choosePreset, beat } from './helpers';

// Scene 6: `#`-triggered phrasebook in the segmented prompt editor. Two
// categories are seeded via the real admin Phrasebook API
// (tests/e2e/marketing/seed.py `_seed_phrasebook` - "camera"/"lighting",
// 8 values each, real prompt-craft vocabulary), so `#camera`/`#lighting`
// match a category's own path exactly and its values render immediately
// (PhrasebookManager.search_phrasebook's "exact_category" branch) rather
// than needing a `#camera.` navigation step first.
test('prompt-segments-phrasebook', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await choosePreset(page, 'SDXL');
	await beat(page, 800);

	const segments = page.getByRole('list', { name: 'Positive segments' });
	const firstEditor = segments.getByRole('textbox').first();
	await expect(firstEditor).toBeVisible({ timeout: 15000 });
	await firstEditor.click();
	await beat(page, 300);

	await firstEditor.pressSequentially('Extreme macro shot, ', { delay: 35 });
	await beat(page, 400);
	await firstEditor.pressSequentially('#camera', { delay: 60 });

	const dropdown = page.getByRole('listbox');
	await expect(dropdown).toBeVisible({ timeout: 5000 });
	const cameraValues = dropdown.getByRole('option');
	await expect(cameraValues.first()).toBeVisible({ timeout: 5000 });
	await beat(page, 900);

	await cameraValues.filter({ hasText: 'Extreme close-up' }).click();
	await beat(page, 700);

	const cameraChip = firstEditor.locator('.inline-chip').first();
	await expect(cameraChip).toBeVisible();

	// Toggle the chip's AUTO (auto-shuffle-on-every-generation) control.
	const autoToggle = cameraChip.locator('button[aria-pressed]');
	await expect(autoToggle).toBeVisible();
	await expect(autoToggle).toHaveAttribute('aria-pressed', 'false');
	await autoToggle.click();
	await expect(autoToggle).toHaveAttribute('aria-pressed', 'true');
	await beat(page, 900);

	// A second segment with its own `#lighting` chip.
	const addSegment = page.getByRole('button', { name: 'Add segment' }).first();
	await addSegment.click();
	await beat(page, 700);

	const secondEditor = segments.getByRole('textbox').nth(1);
	await expect(secondEditor).toBeVisible({ timeout: 10000 });
	await secondEditor.click();
	await secondEditor.pressSequentially('#lighting', { delay: 60 });

	const lightingValues = page.getByRole('listbox').getByRole('option');
	await expect(lightingValues.first()).toBeVisible({ timeout: 5000 });
	await beat(page, 800);

	await lightingValues.filter({ hasText: 'Bioluminescent glow' }).click();
	await beat(page, 700);

	const lightingChip = secondEditor.locator('.inline-chip').first();
	await expect(lightingChip).toBeVisible();
	await beat(page, 1200);
});
