import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, scopedStorageKey } from './helpers';
import { openControlTab, prepare, uploadSample } from './imageEditorHelpers';
import { pickFieldTool } from './mediaFieldHelpers';

const chat = (page: Page) => page.getByRole('dialog', { name: 'AI chat' });
const help = (page: Page) => page.getByRole('dialog', { name: 'Keyboard Shortcuts' });
const toolHost = (page: Page) => page.getByTestId('tool-tab-host');
const bookTabs = (page: Page) => page.locator('button.book-tab');

async function bodyFocus(page: Page) {
	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur?.());
	await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
}

async function pressOpens(page: Page, key: string, dialog: Locator) {
	await bodyFocus(page);
	await page.keyboard.press(key);
	await expect(dialog).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(dialog).toHaveCount(0);
}

async function workbenchCollapsed(page: Page): Promise<boolean> {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	return page.evaluate((storageKey) => {
		const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{"tabs":[]}');
		const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool') ?? {};
		return !!generation.workbenchCollapsed;
	}, key);
}

async function generateWithImageField(page: Page): Promise<Locator> {
	await loginAsOwner(page);
	const name = await prepare(page);
	const root = await openControlTab(page, false, name);
	const field = root.locator('[data-field-name]:has(input[type="file"])').first();
	await pressOpens(page, 'c', chat(page));
	await uploadSample(page, field, 96);
	return field;
}

async function pointerAway(page: Page) {
	await page.mouse.move(1400, 880);
}

test.describe('Global shortcuts on Generate with an image field', () => {
	test.use({ viewport: { width: 1440, height: 900 } });
	test.setTimeout(240000);

	test('C opens the chat while the pointer rests on the picked image', async ({ page }) => {
		const field = await generateWithImageField(page);
		await field.locator('[data-media-inspector]').hover();
		await pressOpens(page, 'c', chat(page));
		await expect(page.getByRole('dialog')).toHaveCount(0);
		await expect(toolHost(page)).toHaveCount(0);
	});

	test('E folds the workbench instead of opening the field editor while the pointer rests on the image', async ({ page }) => {
		const field = await generateWithImageField(page);
		await field.locator('[data-media-inspector]').hover();
		const before = await workbenchCollapsed(page);
		await bodyFocus(page);
		await page.keyboard.press('e');
		await expect.poll(() => workbenchCollapsed(page)).toBe(!before);
		await expect(page.getByRole('dialog')).toHaveCount(0);
		await expect(toolHost(page)).toHaveCount(0);
	});

	test('C, ? and T fire from the page after the field tools menu opens and closes', async ({ page }) => {
		const field = await generateWithImageField(page);
		await field.locator('[data-tools-trigger]').first().click();
		await expect(page.locator('[data-tools-menu]')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.locator('[data-tools-menu]')).toHaveCount(0);
		await pointerAway(page);
		await pressOpens(page, 'c', chat(page));
		await pressOpens(page, '?', help(page));
		const tabs = await bookTabs(page).count();
		await bodyFocus(page);
		await page.keyboard.press('t');
		await expect(bookTabs(page)).toHaveCount(tabs + 1);
	});

	test('C opens the chat after leaving an Image editor tab opened from the field', async ({ page }) => {
		const field = await generateWithImageField(page);
		await pickFieldTool(page, field, 'open-in-editor');
		await expect(toolHost(page)).toHaveAttribute('data-tool', 'image');
		await pressOpens(page, 'c', chat(page));
		await bookTabs(page).first().click();
		await expect(toolHost(page)).toHaveCount(0);
		await page.getByRole('tab', { name: 'Control', exact: true }).first().click();
		await expect(field.locator('[data-media-inspector]')).toBeVisible();
		await field.locator('[data-media-inspector]').hover();
		await pressOpens(page, 'c', chat(page));
		await pointerAway(page);
		await pressOpens(page, 'c', chat(page));
	});
});
