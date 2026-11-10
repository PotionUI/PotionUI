import { expect, type Page } from '@playwright/test';
import { presetOption } from './presetPreamble';

export async function pickFakeImage(page: Page) {
	const model = page.locator('[data-field-name="model"]');
	await expect(model).toBeVisible({ timeout: 20000 });
	const swap = model.locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await model.locator('input').first().click();
	await page.getByText('Fake Image', { exact: true }).last().click();
	const details = page.getByText('Model Details', { exact: true });
	if (await details.isVisible().catch(() => false)) {
		await page.keyboard.press('Escape');
		await expect(details).toBeHidden();
	}
}

export async function openFakeStudio(page: Page) {
	await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const model = page.locator('[data-field-name="model"]');
	await expect(choose.or(model).first()).toBeVisible({ timeout: 20000 });
	await page.waitForLoadState('networkidle');
	if (!(await model.isVisible())) {
		await choose.click();
		await presetOption(page, 'Fake Studio').click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
		await pickFakeImage(page);
	}
	await expect(model).toBeVisible({ timeout: 20000 });
}
