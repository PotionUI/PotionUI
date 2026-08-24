import { test, expect } from '@playwright/test';
import { loginAsOwner, choosePreset, beat } from './helpers';

// Scene 1 (★): open /generate, open the preset picker, show the grid, pick
// SDXL, click through its per-model form tabs (Generation, LoRA, ControlNet,
// Advanced), then open a second generation tab and switch back to the first
// - the multi-tab workspace bar, distinct from the preset's own form tabs.
test('preset-picker-and-form', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await beat(page, 800);

	await page.getByRole('button', { name: 'Choose a preset' }).click();
	const picker = page.getByRole('listbox', { name: 'Presets' });
	await picker.waitFor({ state: 'visible' });
	await beat(page, 1200);

	await picker.getByText('SDXL', { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await beat(page, 1200);

	for (const tabName of ['Generation', 'LoRA', 'ControlNet', 'Advanced']) {
		const tab = page.getByRole('tab', { name: tabName });
		if (await tab.isVisible().catch(() => false)) {
			await tab.click();
			await beat(page, 900);
		}
	}

	const addTab = page.getByRole('button', { name: 'Add new tab' });
	if (await addTab.isVisible().catch(() => false)) {
		await addTab.click();
		await beat(page, 1000);
		const firstTab = page.getByRole('button', { name: 'Generation 1' });
		if (await firstTab.isVisible().catch(() => false)) {
			await firstTab.click();
			await beat(page, 900);
		}
	}

	await beat(page, 1000);
});
