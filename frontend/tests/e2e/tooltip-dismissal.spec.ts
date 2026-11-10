import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner } from './helpers';

const tooltip = (page: Page) => page.locator('[role="tooltip"]');

async function hoverAiChat(page: Page) {
	await loginAsOwner(page);
	await page.goto('/models');
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await page.mouse.move(0, 0);
	await fab.hover();
	await expect(tooltip(page)).toHaveCount(1);
	return fab;
}

test.describe('tooltip dismissal', () => {
	test('a tooltip disappears when its anchor is removed without a pointer leave', async ({ page }) => {
		const fab = await hoverAiChat(page);
		await fab.evaluate((el) => el.parentElement!.remove());
		await expect(tooltip(page).filter({ hasText: 'AI Chat' })).toHaveCount(0);
	});

	test('a tooltip disappears when the page loses focus', async ({ page }) => {
		await hoverAiChat(page);
		await page.evaluate(() => window.dispatchEvent(new Event('blur')));
		await expect(tooltip(page)).toHaveCount(0);
	});

	test('a tooltip disappears when the pointer leaves, even without a mouseleave event', async ({ page }) => {
		await hoverAiChat(page);
		await page.mouse.move(600, 400);
		await expect(tooltip(page)).toHaveCount(0);
	});

	test('a clicked control does not reopen its tooltip when the window regains focus', async ({ page }) => {
		const fab = await hoverAiChat(page);
		await fab.click();
		await page.mouse.move(600, 400);
		await expect(tooltip(page)).toHaveCount(0);
		await page.evaluate(() => {
			window.dispatchEvent(new Event('blur'));
			window.dispatchEvent(new Event('focus'));
			(document.activeElement as HTMLElement | null)?.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
		});
		await page.waitForTimeout(400);
		await expect(tooltip(page)).toHaveCount(0);
	});
});
