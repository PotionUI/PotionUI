import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { pickFieldTool } from './mediaFieldHelpers';
import { SIZES, expectEditorReady, openEditField, stagePixel } from './imageEditorHelpers';

const JOURNEY = 'image-editor-filters';

async function openEditor(page: Page, field: Locator) {
	await pickFieldTool(page, field, 'edit');
	const dialog = page.getByRole('dialog', { name: 'Edit image' });
	await expect(dialog).toBeVisible();
	await expectEditorReady(dialog.getByLabel('Drawing canvas'));
	return dialog;
}

function distance(a: number[], b: number[]): number {
	return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]);
}

async function chooseFilter(dialog: Locator, name: string) {
	const radio = dialog.getByRole('radio', { name, exact: true });
	await expect(radio).toBeVisible({ timeout: 15000 });
	await radio.click();
	await expect(radio).toHaveAttribute('aria-checked', 'true');
}

async function setIntensity(dialog: Locator, value: number) {
	await dialog.getByLabel('Intensity', { exact: true }).fill(String(value));
	await expect(dialog.getByText(`${value}%`, { exact: true })).toBeVisible();
}

for (const size of SIZES) {
	const mobile = size.tag === '390';

	test(`applies Ember, fine-tunes it, manages a saved filter and saves the image at ${size.tag}`, async ({ page }) => {
		test.setTimeout(300000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 160);
		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');
		const original = await stagePixel(canvas, 0.5, 0.5);

		await page.keyboard.press('r');
		await expect(dialog.getByRole('button', { name: 'Filters', exact: true }).first()).toHaveAttribute(
			'aria-pressed',
			'true'
		);
		await expect(dialog.getByRole('radiogroup', { name: 'Filters' })).toBeVisible();
		await expect(dialog.getByRole('radio', { name: 'None', exact: true })).toHaveAttribute('aria-checked', 'true');
		await expect(dialog.getByRole('button', { name: 'Apply', exact: true })).toBeDisabled();
		await screenshot(page, JOURNEY, `strip-none-${size.tag}`);

		await chooseFilter(dialog, 'Ember');
		await setIntensity(dialog, 70);
		await expect.poll(async () => distance(await stagePixel(canvas, 0.5, 0.5), original)).toBeGreaterThan(6);
		await screenshot(page, JOURNEY, `ember-70-${size.tag}`);

		await dialog.getByRole('button', { name: 'Fine-tune', exact: true }).click();
		await expect(dialog.getByRole('button', { name: 'Filters', exact: true }).first()).toBeVisible();
		await expect(dialog.getByRole('region', { name: 'White balance' })).toBeVisible();
		const vignetteEye = dialog.getByRole('button', { name: 'Disable Vignette' });
		await vignetteEye.click();
		await expect(dialog.getByRole('button', { name: 'Enable Vignette' })).toBeVisible();
		await screenshot(page, JOURNEY, `fine-tune-${size.tag}`);

		await dialog.getByRole('button', { name: 'Save as filter', exact: true }).click();
		const saveDialog = page.getByRole('dialog', { name: 'Save as filter' });
		await expect(saveDialog).toBeVisible();
		await saveDialog.getByLabel('Name').fill('My Ember');
		await screenshot(page, JOURNEY, `save-as-filter-${size.tag}`);
		const [created] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/filters/mine') && r.request().method() === 'POST'),
			saveDialog.getByRole('button', { name: 'Save filter' }).click()
		]);
		expect(created.ok()).toBeTruthy();
		const body = created.request().postDataJSON();
		expect(body.name).toBe('My Ember');
		expect(body.intensity).toBe(70);
		expect(body.steps.find((step: { op: string }) => step.op === 'vignette').enabled).toBe(false);
		await expect(saveDialog).toBeHidden();

		const mine = dialog.getByRole('radio', { name: 'My Ember', exact: true });
		await expect(mine).toBeVisible({ timeout: 15000 });
		await expect(mine).toHaveAttribute('aria-checked', 'true');

		await mine.hover();
		await page.getByRole('button', { name: 'More actions for My Ember' }).click();
		await page.getByRole('menuitem', { name: 'Rename' }).click();
		const [renamed] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/filters/mine/') && r.request().method() === 'PATCH'),
			(async () => {
				const rename = page.getByRole('textbox', { name: 'Rename My Ember' });
				await rename.fill('Evening');
				await rename.press('Enter');
			})()
		]);
		expect(renamed.ok()).toBeTruthy();
		const evening = dialog.getByRole('radio', { name: 'Evening', exact: true });
		await expect(evening).toBeVisible();
		await screenshot(page, JOURNEY, `renamed-${size.tag}`);

		await evening.hover();
		await page.getByRole('button', { name: 'More actions for Evening' }).click();
		await page.getByRole('menuitem', { name: 'Delete' }).click();
		const confirm = dialog.getByRole('alertdialog', { name: 'Delete filter' });
		await expect(confirm).toContainText("Delete Evening? This can't be undone.");
		const [deleted] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/filters/mine/') && r.request().method() === 'DELETE'),
			confirm.getByRole('button', { name: 'Delete', exact: true }).click()
		]);
		expect(deleted.ok()).toBeTruthy();
		await expect(dialog.getByRole('radio', { name: 'Evening', exact: true })).toHaveCount(0);

		await chooseFilter(dialog, 'Ember');
		await setIntensity(dialog, 70);
		await dialog.getByRole('button', { name: 'Apply', exact: true }).click();
		await expect(dialog.getByRole('button', { name: /^Undo Filter: Ember 70%/ })).toBeEnabled();
		await expect(dialog.getByRole('radio', { name: 'None', exact: true })).toHaveAttribute('aria-checked', 'true');
		expect(distance(await stagePixel(canvas, 0.5, 0.5), original)).toBeGreaterThan(6);

		await dialog.getByRole('button', { name: /Save as new/ }).click();
		const popover = page.getByRole('group', { name: 'Save as new upload' });
		await expect(popover).toBeVisible();
		const [saved] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
			popover.getByRole('button', { name: 'Save and use' }).click()
		]);
		expect(saved.ok()).toBeTruthy();
		await expect(dialog).toBeHidden({ timeout: 15000 });
	});
}
