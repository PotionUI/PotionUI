import { test, expect, type Locator } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { pickFieldTool } from './mediaFieldHelpers';
import {
	SIZES,
	openControlTab,
	fixturePng,
	prepare,
	strokeAcross,
	expectEditorReady,
	visibleText
} from './imageEditorHelpers';

const JOURNEY = 'image-editor';

const toolButton = (dialog: Locator, mobile: boolean, tool: string) =>
	mobile ? dialog.getByRole('button', { name: new RegExp(`^Paint, ${tool}$`) }) : dialog.getByRole('button', { name: tool, exact: true });

for (const size of SIZES) {
	test(`image editor draws, undoes and saves a new upload at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		const mobile = size.tag === '390';
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);

		const field = root.locator('[data-field-name]:has(input[type="file"])').first();
		await field.scrollIntoViewIfNeeded();
		const [uploadResponse] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
			field.locator('input[type="file"]').first().setInputFiles({
				name: 'sample.png',
				mimeType: 'image/png',
				buffer: fixturePng(256)
			})
		]);
		expect(uploadResponse.ok()).toBeTruthy();
		await expect(field.locator('[data-media-inspector]')).toBeVisible();
		await screenshot(page, JOURNEY, `field-${size.tag}`);

		await pickFieldTool(page, field, 'edit');
		const dialog = page.getByRole('dialog', { name: 'Edit image' });
		await expect(dialog).toBeVisible();
		const canvas = dialog.getByLabel('Drawing canvas');
		await expect(visibleText(dialog, '256 × 256')).toBeVisible();
		await expectEditorReady(canvas);

		const hasPixels = await canvas.evaluate((el) => {
			const c = el as HTMLCanvasElement;
			const g = c.getContext('2d')!;
			const data = g.getImageData(c.width / 2, c.height / 2, 1, 1).data;
			return data[3] > 0;
		});
		expect(hasPixels).toBeTruthy();
		await screenshot(page, JOURNEY, `idle-${size.tag}`);

		const undo = dialog.getByRole('button', { name: 'Undo' });
		const redo = dialog.getByRole('button', { name: 'Redo' });
		await expect(undo).toBeDisabled();

		await dialog.locator('[data-colour-swatch]').click();
		const picker = page.locator('[data-colour-picker]');
		await picker.getByRole('button', { name: 'Use colour #ffffff' }).click();
		await page.keyboard.press('Escape');
		await expect(picker).toHaveCount(0);
		await dialog.locator('#paint-size').fill('20');

		await strokeAcross(page, canvas, [0.3, 0.5], [0.7, 0.5]);
		await expect(undo).toBeEnabled();
		await expect(dialog.getByText('unsaved')).toBeVisible();
		await screenshot(page, JOURNEY, `drawn-${size.tag}`);

		await page.keyboard.press('Control+z');
		await expect(undo).toBeDisabled();
		await expect(redo).toBeEnabled();
		await page.keyboard.press('Control+y');
		await expect(undo).toBeEnabled();
		await expect(redo).toBeDisabled();

		await page.keyboard.press('e');
		await expect(toolButton(dialog, mobile, 'Eraser')).toHaveAttribute('aria-pressed', 'true');
		await page.keyboard.press('b');
		await expect(toolButton(dialog, mobile, 'Brush')).toHaveAttribute('aria-pressed', 'true');

		await expect(dialog.locator('#paint-save-name')).toHaveValue('sample-edit');
		await screenshot(page, JOURNEY, `paint-modal-${size.tag}`);
		await screenshot(page, JOURNEY, `saving-${size.tag}`);

		const [saved] = await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
			dialog.getByRole('button', { name: 'Save', exact: true }).click()
		]);
		expect(saved.ok()).toBeTruthy();
		await expect(dialog).toBeHidden({ timeout: 15000 });
		await expect(field.getByText('sample-edit.png')).toBeVisible();
		await screenshot(page, JOURNEY, `saved-${size.tag}`);

		const stats = await field.locator('img').first().evaluate(async (img: HTMLImageElement) => {
			await img.decode();
			const c = document.createElement('canvas');
			c.width = img.naturalWidth;
			c.height = img.naturalHeight;
			const g = c.getContext('2d')!;
			g.drawImage(img, 0, 0);
			const data = g.getImageData(0, 0, c.width, c.height).data;
			let white = 0;
			for (let i = 0; i < data.length; i += 4) {
				if (data[i] > 250 && data[i + 1] > 250 && data[i + 2] > 250) white++;
			}
			return { width: c.width, height: c.height, white };
		});
		expect(stats.width).toBe(256);
		expect(stats.height).toBe(256);
		expect(stats.white).toBeGreaterThan(300);
	});

	test(`image editor asks before discarding changes at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		const mobile = size.tag === '390';
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);

		const field = root.locator('[data-field-name]:has(input[type="file"])').first();
		await field.scrollIntoViewIfNeeded();
		await field.locator('input[type="file"]').first().setInputFiles({
			name: 'sample.png',
			mimeType: 'image/png',
			buffer: fixturePng(128)
		});
		await expect(field.locator('[data-media-inspector]')).toBeVisible({ timeout: 15000 });

		await pickFieldTool(page, field, 'edit');
		const dialog = page.getByRole('dialog', { name: 'Edit image' });
		await expect(dialog).toBeVisible();
		const canvas = dialog.getByLabel('Drawing canvas');
		await expect(visibleText(dialog, '128 × 128')).toBeVisible();

		await dialog.getByRole('button', { name: 'Cancel' }).click();
		await expect(dialog).toBeHidden();

		await pickFieldTool(page, field, 'edit');
		await expect(dialog).toBeVisible();
		await expect(visibleText(dialog, '128 × 128')).toBeVisible();
		await strokeAcross(page, canvas, [0.2, 0.3], [0.8, 0.6]);
		await dialog.getByRole('button', { name: 'Cancel' }).click();

		const alert = page.getByRole('alertdialog', { name: 'Discard changes' });
		await expect(alert).toBeVisible();
		const alertBox = await alert.boundingBox();
		const cancelBox = await dialog.getByRole('button', { name: 'Cancel' }).boundingBox();
		expect(alertBox && cancelBox && alertBox.x + alertBox.width).toBeGreaterThanOrEqual(cancelBox!.x + cancelBox!.width - 1);
		await screenshot(page, JOURNEY, `discard-${size.tag}`);
		await alert.getByRole('button', { name: 'Keep editing' }).click();
		await expect(alert).toBeHidden();
		await expect(dialog).toBeVisible();

		await page.keyboard.press('Escape');
		await expect(alert).toBeVisible();
		await alert.getByRole('button', { name: 'Discard' }).click();
		await expect(dialog).toBeHidden();
	});
}
