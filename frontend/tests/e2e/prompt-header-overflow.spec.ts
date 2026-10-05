import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'prompt-header-overflow';
const LABELS = ['Styles', 'Prompts', 'Segments', 'Templates', 'Variables'];

async function expectNothingCutOff(page: Page) {
	const header = page.locator('header.composer-toolbar').first();
	await expect(header).toBeVisible({ timeout: 15000 });
	const box = await header.boundingBox();
	expect(box).not.toBeNull();
	const slots = header.locator('[data-toolbar-action]');
	const count = await slots.count();
	for (let i = 0; i < count; i++) {
		const slot = await slots.nth(i).boundingBox();
		expect(slot).not.toBeNull();
		expect(slot!.x).toBeGreaterThanOrEqual(box!.x - 1);
		expect(slot!.x + slot!.width).toBeLessThanOrEqual(box!.x + box!.width + 1);
	}
	const overflowing = await header.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
	expect(overflowing).toBe(false);
}

async function fullRowWidth(page: Page): Promise<number> {
	const header = page.locator('header.composer-toolbar').first();
	return header.evaluate((el) => {
		const style = getComputedStyle(el);
		const gap = parseFloat(style.columnGap) || 0;
		const items = (Array.from(el.children) as HTMLElement[]).filter(
			(child) => !child.classList.contains('toolbar-spacer')
		);
		const content = items.reduce((sum, child) => sum + child.offsetWidth, 0);
		return Math.ceil(
			content + gap * items.length + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
		);
	});
}

async function reachable(page: Page, label: string): Promise<boolean> {
	const header = page.locator('header.composer-toolbar').first();
	if ((await header.locator('[data-toolbar-action]').filter({ hasText: label }).count()) > 0) return true;
	const more = header.getByRole('button', { name: 'More prompt actions' });
	if ((await more.count()) === 0) return false;
	await more.click();
	const item = page.getByRole('menuitem', { name: new RegExp(`^${label}`) });
	const found = (await item.count()) > 0;
	await page.keyboard.press('Escape');
	return found;
}

test('prompt header actions collapse into the more menu as the prompts pane is dragged narrow', async ({ page }) => {
	test.setTimeout(90000);
	await loginAsOwner(page);
	await page.setViewportSize({ width: 1600, height: 900 });
	await page.goto('/generate');
	await installAndSelectImagePreset(page);

	await page.getByRole('button', { name: 'More view options' }).click();
	await page.getByRole('option', { name: /Three panes/ }).click();

	const handle = page.getByTestId('prompts-workbench-handle');
	await expect(handle).toBeVisible({ timeout: 20000 });
	await expectNothingCutOff(page);
	await screenshot(page, JOURNEY, 'wide');

	const header = page.locator('header.composer-toolbar').first();
	const startWidth = (await header.boundingBox())!.width;
	const handleBox = (await handle.boundingBox())!;

	await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
	await page.mouse.down();
	await page.mouse.move(handleBox.x - Math.round(startWidth * 0.6), handleBox.y + handleBox.height / 2, { steps: 12 });
	await page.mouse.up();

	await expect.poll(async () => (await header.boundingBox())!.width, { timeout: 5000 }).toBeLessThan(startWidth);
	await expectNothingCutOff(page);

	if ((await header.locator('[data-toolbar-action]').count()) === LABELS.length) {
		const needed = await fullRowWidth(page);
		await header.evaluate((el, width) => {
			(el as HTMLElement).style.maxWidth = `${width}px`;
		}, Math.round(needed * 0.75));
	}

	await expect
		.poll(async () => header.locator('[data-toolbar-action]').count(), { timeout: 5000 })
		.toBeLessThan(LABELS.length);
	await expect(header.getByRole('button', { name: 'More prompt actions' })).toBeVisible();
	await expectNothingCutOff(page);
	await screenshot(page, JOURNEY, 'narrow');

	for (const label of LABELS) {
		expect(await reachable(page, label), `${label} is visible or in the more menu`).toBe(true);
	}
});
