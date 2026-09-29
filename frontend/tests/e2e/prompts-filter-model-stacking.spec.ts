import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'prompts-filter-model-stacking';

const TINY_PNG_BASE64 =
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

async function pointClosestDialogLabel(page: Page, point: { x: number; y: number }) {
	return page.evaluate(({ x, y }) => {
		const el = document.elementFromPoint(x, y);
		const dialog = el?.closest('[role="dialog"]');
		return dialog?.getAttribute('aria-label') ?? null;
	}, point);
}

function rectOverlap(
	a: { x: number; y: number; width: number; height: number },
	b: { x: number; y: number; width: number; height: number }
) {
	const left = Math.max(a.x, b.x);
	const top = Math.max(a.y, b.y);
	const right = Math.min(a.x + a.width, b.x + b.width);
	const bottom = Math.min(a.y + a.height, b.y + b.height);
	return { left, top, right, bottom };
}

async function openFiltersThenModelPicker(page: Page) {
	await page.goto('/prompts');
	const filtersButton = page.getByRole('button', { name: 'Filters' });
	await expect(filtersButton).toBeVisible({ timeout: 15000 });
	await filtersButton.click();

	const popover = page.getByRole('dialog', { name: 'Filters' });
	await expect(popover).toBeVisible({ timeout: 10000 });

	const modelPickerButton = popover.getByText('Model', { exact: true }).locator('..').getByRole('button');
	await expect(modelPickerButton).toBeVisible({ timeout: 10000 });
	await modelPickerButton.click();

	const modal = page.getByRole('dialog', { name: 'Filter prompts by model' });
	await expect(modal).toBeVisible({ timeout: 10000 });

	return { popover, modal };
}

test('the Model picker modal stacks above the open Filters popover at desktop width', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	const { popover, modal } = await openFiltersThenModelPicker(page);

	await screenshot(page, JOURNEY, 'desktop-model-over-filters');

	await expect(popover).toBeVisible();

	const modalBox = await modal.boundingBox();
	const popoverBox = await popover.boundingBox();
	expect(modalBox, 'modal has a box').toBeTruthy();
	expect(popoverBox, 'popover has a box').toBeTruthy();

	const modalCenter = {
		x: modalBox!.x + modalBox!.width / 2,
		y: modalBox!.y + modalBox!.height / 2
	};
	expect(await pointClosestDialogLabel(page, modalCenter)).toBe('Filter prompts by model');

	const overlap = rectOverlap(modalBox!, popoverBox!);
	expect(overlap.right, 'modal and popover overlap horizontally').toBeGreaterThan(overlap.left);
	expect(overlap.bottom, 'modal and popover overlap vertically').toBeGreaterThan(overlap.top);

	const overlapPoint = { x: (overlap.left + overlap.right) / 2, y: (overlap.top + overlap.bottom) / 2 };
	expect(await pointClosestDialogLabel(page, overlapPoint)).toBe('Filter prompts by model');
});

test('the Model picker modal stacks above the open Filters popover at phone width', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 390, height: 844 });
	await loginAsOwner(page);

	const { popover, modal } = await openFiltersThenModelPicker(page);

	await screenshot(page, JOURNEY, 'phone-model-over-filters');

	const modalBox = await modal.boundingBox();
	expect(modalBox, 'modal has a box').toBeTruthy();

	const modalCenter = {
		x: modalBox!.x + modalBox!.width / 2,
		y: modalBox!.y + modalBox!.height / 2
	};
	expect(await pointClosestDialogLabel(page, modalCenter)).toBe('Filter prompts by model');

	await expect(popover).toHaveCount(1);
});

test('a tag picker dropdown opened from inside the Generation Details modal stacks above that modal', async ({
	page
}) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { files: { name: 'stacking-repro.png', mimeType: 'image/png', buffer: pngBuffer } }
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();

	await page.goto('/history');
	const viewButton = page.getByRole('button', { name: 'View generation details' }).first();
	await expect(viewButton).toBeVisible({ timeout: 20000 });
	await viewButton.click();

	const modal = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(modal).toBeVisible({ timeout: 10000 });

	const tagButton = page.getByRole('button', { name: 'Manage tags' });
	await expect(tagButton).toBeVisible({ timeout: 20000 });
	await tagButton.click();

	const searchInput = page.getByPlaceholder('Search or create tags...');
	await expect(searchInput).toBeVisible({ timeout: 10000 });
	const panel = searchInput.locator('..');

	await screenshot(page, JOURNEY, 'modal-tag-dropdown-over-modal');

	const modalBox = await modal.boundingBox();
	const panelBox = await panel.boundingBox();
	expect(modalBox, 'modal has a box').toBeTruthy();
	expect(panelBox, 'panel has a box').toBeTruthy();

	const overlap = rectOverlap(modalBox!, panelBox!);
	expect(overlap.right, 'panel and modal overlap horizontally').toBeGreaterThan(overlap.left);
	expect(overlap.bottom, 'panel and modal overlap vertically').toBeGreaterThan(overlap.top);

	const overlapPoint = { x: (overlap.left + overlap.right) / 2, y: (overlap.top + overlap.bottom) / 2 };
	const label = await pointClosestDialogLabel(page, overlapPoint);
	expect(label, 'the overlap point should not resolve inside the modal dialog').not.toBe('Generation Details');
});

async function openLLMCreateConfigModal(page: Page) {
	await page.goto('/admin?tab=llm');
	const addButton = page.getByRole('button', { name: 'Add configuration' }).first();
	await expect(addButton).toBeVisible({ timeout: 20000 });
	await addButton.click();

	const modal = page.getByRole('dialog', { name: 'Create LLM Configuration' });
	await expect(modal).toBeVisible({ timeout: 10000 });
	return modal;
}

async function zIndexOf(locator: Locator) {
	return locator.evaluate((el) => Number(getComputedStyle(el).zIndex));
}

async function backdropZIndexOf(dialog: Locator) {
	return dialog.evaluate((el) => Number(getComputedStyle(el.parentElement as HTMLElement).zIndex));
}

test('the model combobox in the LLM create-config modal stacks above that modal', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	const modal = await openLLMCreateConfigModal(page);

	const combobox = modal.locator('[data-testid="llm-model-combobox"] input');
	await expect(combobox).toBeVisible({ timeout: 10000 });
	await combobox.click();

	const listbox = page.getByRole('listbox', { name: 'Available models' });
	await expect(listbox).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, 'llm-config-modal-combobox-over-modal');

	const modalBox = await modal.boundingBox();
	const listboxBox = await listbox.boundingBox();
	expect(modalBox, 'modal has a box').toBeTruthy();
	expect(listboxBox, 'listbox has a box').toBeTruthy();

	const overlap = rectOverlap(modalBox!, listboxBox!);
	expect(overlap.right, 'listbox and modal overlap horizontally').toBeGreaterThan(overlap.left);
	expect(overlap.bottom, 'listbox and modal overlap vertically').toBeGreaterThan(overlap.top);

	const overlapPoint = { x: (overlap.left + overlap.right) / 2, y: (overlap.top + overlap.bottom) / 2 };
	const overlapEl = await page.evaluate(({ x, y }) => {
		const el = document.elementFromPoint(x, y);
		return el?.closest('[role="listbox"]') ? 'listbox' : el?.closest('[role="dialog"]')?.getAttribute('aria-label') ?? null;
	}, overlapPoint);
	expect(overlapEl).toBe('listbox');
});

test('a toast shown while the LLM create-config modal is open stacks above that modal', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	const modal = await openLLMCreateConfigModal(page);
	const modalZ = await backdropZIndexOf(modal);

	await page.evaluate(() => {
		(window as unknown as { __potionui: { notifications: { toast: (level: string, message: string) => void } } })
			.__potionui.notifications.toast('info', 'Layering check toast');
	});

	const toast = page.getByRole('status').filter({ hasText: 'Layering check toast' });
	await expect(toast).toBeVisible({ timeout: 5000 });

	await screenshot(page, JOURNEY, 'toast-over-llm-modal');

	expect(await zIndexOf(toast)).toBeGreaterThan(modalZ);
});

test('a Tooltip opened inside the LLM create-config modal stacks above that modal', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	const modal = await openLLMCreateConfigModal(page);
	const modalZ = await backdropZIndexOf(modal);

	const refreshButton = modal.getByRole('button', { name: 'Refresh models' });
	await expect(refreshButton).toBeVisible({ timeout: 10000 });
	await refreshButton.hover();

	const tooltip = page.locator('.z-tooltip').filter({ hasText: 'Refresh models' });
	await expect(tooltip).toBeVisible({ timeout: 5000 });

	await screenshot(page, JOURNEY, 'tooltip-over-llm-modal');

	expect(await zIndexOf(tooltip)).toBeGreaterThan(modalZ);
});

test('FloatingWorkbench stays below a modal opened over it', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	test.skip(!preset, 'no installable image preset in this fixture backend');

	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden({ timeout: 15000 });
	const pickerTrigger = page.locator('button[aria-haspopup="dialog"]', { hasText: preset!.name });
	await expect(pickerTrigger).toBeVisible({ timeout: 15000 });
	await expect(pickerTrigger).toBeEnabled({ timeout: 15000 });

	await expect(page.locator('button.generate-button')).toBeVisible({ timeout: 15000 });
	await expect(page.locator('[data-testid="workbench-pane"]')).toBeVisible({ timeout: 15000 });
	await page.waitForLoadState('networkidle');

	await page.keyboard.press('w');
	const workbench = page.getByRole('dialog', { name: 'Workbench' });
	await expect(workbench).toBeVisible({ timeout: 10000 });

	await pickerTrigger.focus();
	await page.keyboard.press('Enter');
	const modal = page.getByRole('dialog', { name: 'Choose preset' });
	await expect(modal).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, 'floating-workbench-below-preset-modal');

	const workbenchBox = await workbench.boundingBox();
	const modalBox = await modal.boundingBox();
	expect(workbenchBox, 'workbench has a box').toBeTruthy();
	expect(modalBox, 'modal has a box').toBeTruthy();

	const overlap = rectOverlap(workbenchBox!, modalBox!);
	expect(overlap.right, 'workbench and modal overlap horizontally').toBeGreaterThan(overlap.left);
	expect(overlap.bottom, 'workbench and modal overlap vertically').toBeGreaterThan(overlap.top);

	const overlapPoint = { x: (overlap.left + overlap.right) / 2, y: (overlap.top + overlap.bottom) / 2 };
	expect(await pointClosestDialogLabel(page, overlapPoint)).toBe('Choose preset');
});
