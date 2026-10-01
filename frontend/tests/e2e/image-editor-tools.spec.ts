import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import {
	SIZES,
	fixturePng,
	openControlTab,
	openEditField,
	prepare,
	stagePixel,
	strokeAcross,
	uploadedStats,
	expectEditorReady,
	visibleText
} from './imageEditorHelpers';

const JOURNEY = 'image-editor-tools';

async function openEditor(page: Page, field: Locator) {
	await field.getByRole('button', { name: 'Edit image' }).click();
	const dialog = page.getByRole('dialog', { name: 'Edit image' });
	await expect(dialog).toBeVisible();
	await expectEditorReady(dialog.getByLabel('Drawing canvas'));
	return dialog;
}

async function reveal(dialog: Locator, mobile: boolean, name: 'Layers' | 'Canvas') {
	if (mobile) await dialog.getByRole('button', { name, exact: true }).click();
}

async function paintMask(page: Page, field: Locator) {
	await field.getByRole('button', { name: 'Create inpainting mask' }).click();
	const maskDialog = page.getByRole('dialog', { name: 'Create inpainting mask' });
	await expect(maskDialog).toBeVisible();
	const box = await maskDialog.locator('canvas').boundingBox();
	if (!box) throw new Error('mask canvas has no box');
	await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5, { steps: 6 });
	await page.mouse.up();
	await maskDialog.getByRole('button', { name: /Save mask/ }).click();
	await expect(maskDialog).toBeHidden({ timeout: 15000 });
	await expect(field.getByText('mask', { exact: true })).toBeVisible();
}

async function saveEdit(page: Page, dialog: Locator) {
	await dialog.getByRole('button', { name: /Save as new/ }).click();
	const popover = page.getByRole('group', { name: 'Save as new upload' });
	await expect(popover).toBeVisible();
	return popover;
}

async function confirmSave(page: Page, dialog: Locator, popover: Locator) {
	const [saved] = await Promise.all([
		page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
		popover.getByRole('button', { name: 'Save and use' }).click()
	]);
	expect(saved.ok()).toBeTruthy();
	await expect(dialog).toBeHidden({ timeout: 15000 });
}

function near(actual: number[], expected: number[], tolerance = 24) {
	for (let i = 0; i < 3; i++) expect(Math.abs(actual[i] - expected[i])).toBeLessThanOrEqual(tolerance);
}

for (const size of SIZES) {
	const mobile = size.tag === '390';

	test(`draws a scribble on a new canvas and saves it at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);
		const field = root.locator('[data-field-name]:has(input[type="file"])').first();
		await field.scrollIntoViewIfNeeded();

		await field.getByRole('button', { name: 'Draw a new image', exact: true }).click();
		const newDrawing = page.getByRole('dialog', { name: 'New drawing' });
		await expect(newDrawing).toBeVisible();
		await screenshot(page, JOURNEY, `new-drawing-${size.tag}`);
		await newDrawing.getByRole('button', { name: '512 × 512' }).click();
		await newDrawing.getByRole('button', { name: 'Start drawing' }).click();

		const dialog = page.getByRole('dialog', { name: 'Draw', exact: true });
		await expect(dialog).toBeVisible();
		const canvas = dialog.getByLabel('Drawing canvas');
		await expectEditorReady(canvas);
		near(await stagePixel(canvas, 0.5, 0.5), [255, 255, 255]);

		await strokeAcross(page, canvas, [0.3, 0.5], [0.7, 0.5]);
		await strokeAcross(page, canvas, [0.5, 0.3], [0.5, 0.7]);
		near(await stagePixel(canvas, 0.5, 0.5), [17, 17, 17], 40);
		await screenshot(page, JOURNEY, `scribble-${size.tag}`);

		const popover = await saveEdit(page, dialog);
		await expect(popover.locator('#paint-save-name')).toHaveValue('drawing');
		await confirmSave(page, dialog, popover);
		await expect(field.getByText('drawing.png')).toBeVisible();
		const stats = await uploadedStats(field);
		expect(stats.width).toBe(512);
		expect(stats.height).toBe(512);
		expect(stats.white).toBeGreaterThan(100000);
		expect(stats.dark).toBeGreaterThan(300);
	});

	test(`lifts a selection onto its own layer and undoes it at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 160);
		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');

		const before = await stagePixel(canvas, 0.4, 0.4);
		await page.keyboard.press('m');
		await expect(dialog.getByRole('button', { name: 'Rectangle select' })).toHaveAttribute(
			'aria-pressed',
			'true'
		);
		await strokeAcross(page, canvas, [0.4, 0.4], [0.5, 0.5]);
		await expect(dialog.getByText(/^sel \d+ × \d+$/)).toBeVisible();
		await screenshot(page, JOURNEY, `selection-${size.tag}`);

		await page.keyboard.press('v');
		await strokeAcross(page, canvas, [0.45, 0.45], [0.7, 0.7]);

		await reveal(dialog, mobile, 'Layers');
		await expect(dialog.getByRole('button', { name: /Select layer Piece/ })).toBeVisible();
		await screenshot(page, JOURNEY, `lifted-${size.tag}`);
		const moved = await stagePixel(canvas, 0.7, 0.7);
		near(moved, before, 60);
		const hole = await stagePixel(canvas, 0.42, 0.42);
		expect(hole[0] === before[0] && hole[1] === before[1] && hole[2] === before[2]).toBe(false);

		await page.keyboard.press('Control+z');
		await expect(dialog.getByRole('button', { name: /Select layer Piece/ })).toHaveCount(0);
		await expect(dialog.getByRole('button', { name: /^Redo/ })).toBeEnabled();
		near(await stagePixel(canvas, 0.42, 0.42), before, 60);
		await page.keyboard.press('Control+y');
		await expect(dialog.getByRole('button', { name: /Select layer Piece/ })).toBeVisible();
	});

	test(`adjusts colours, crops and flips with the mask cleared at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 160);
		await paintMask(page, field);
		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');

		const original = await stagePixel(canvas, 0.3, 0.3);
		await page.keyboard.press('a');
		await dialog.getByRole('button', { name: 'Invert', exact: true }).click();
		await expect
			.poll(async () => Math.abs((await stagePixel(canvas, 0.3, 0.3))[0] - original[0]))
			.toBeGreaterThan(40);
		const previewed = await stagePixel(canvas, 0.3, 0.3);
		await screenshot(page, JOURNEY, `adjust-${size.tag}`);
		await dialog.getByRole('button', { name: 'Apply', exact: true }).click();
		await expect(dialog.getByRole('button', { name: /^Undo Adjust colours/ })).toBeEnabled();
		near(await stagePixel(canvas, 0.3, 0.3), previewed, 30);

		await page.keyboard.press('c');
		await strokeAcross(page, canvas, [0.2, 0.2], [0.7, 0.7]);
		await page.keyboard.press('Enter');
		await expect(visibleText(dialog, '160 × 160')).toHaveCount(0);
		await screenshot(page, JOURNEY, `cropped-${size.tag}`);

		await reveal(dialog, mobile, 'Canvas');
		await dialog.getByRole('button', { name: 'Flip horizontally' }).click();
		await expect(dialog.getByRole('button', { name: /^Undo Flip horizontal/ })).toBeEnabled();

		const popover = await saveEdit(page, dialog);
		await expect(popover.getByText(/Inpaint mask will be cleared/)).toBeVisible();
		await screenshot(page, JOURNEY, `save-clears-mask-${size.tag}`);
		await confirmSave(page, dialog, popover);
		await expect(field.getByText('mask', { exact: true })).toHaveCount(0);
	});

	test(`keeps the inpaint mask when only pixels change at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 128);
		await paintMask(page, field);
		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');
		await strokeAcross(page, canvas, [0.3, 0.3], [0.6, 0.6]);

		const popover = await saveEdit(page, dialog);
		await expect(popover.getByText(/Inpaint mask is kept/)).toBeVisible();
		await confirmSave(page, dialog, popover);
		await expect(field.getByText('sample-edit.png')).toBeVisible();
		await expect(field.getByText('mask', { exact: true })).toBeVisible();
	});

	test(`keeps the inpaint mask after a crop is undone at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 128);
		await paintMask(page, field);
		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');

		await page.keyboard.press('c');
		await strokeAcross(page, canvas, [0.2, 0.2], [0.7, 0.7]);
		await page.keyboard.press('Enter');
		await expect(visibleText(dialog, '128 × 128')).toHaveCount(0);
		await page.keyboard.press('Control+z');
		await expect(visibleText(dialog, '128 × 128')).toBeVisible();

		await strokeAcross(page, canvas, [0.1, 0.1], [0.3, 0.3]);
		const popover = await saveEdit(page, dialog);
		await expect(popover.getByText(/Inpaint mask is kept/)).toBeVisible();
		await confirmSave(page, dialog, popover);
		await expect(field.getByText('mask', { exact: true })).toBeVisible();
	});

	test(`adds an image as a layer, scales it and opens another at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 128);
		const dialog = await openEditor(page, field);

		await dialog.getByRole('button', { name: 'Add image as layer' }).first().click();
		const menu = page.getByRole('menu', { name: 'Add an image as a layer' });
		await expect(menu).toBeVisible();
		await screenshot(page, JOURNEY, `add-menu-${size.tag}`);
		const [chooser] = await Promise.all([
			page.waitForEvent('filechooser'),
			menu.getByRole('menuitem', { name: 'Browse files' }).click()
		]);
		await chooser.setFiles({ name: 'piece.png', mimeType: 'image/png', buffer: fixturePng(96) });
		await expect(dialog.getByRole('button', { name: 'Transform' })).toHaveAttribute('aria-pressed', 'true');

		await reveal(dialog, mobile, 'Layers');
		await expect(dialog.getByRole('button', { name: /Select layer piece/ })).toBeVisible();
		if (mobile) await dialog.getByRole('button', { name: 'Transform' }).click();
		const slider = dialog.locator('#transform-scale:visible');
		await expect(slider).toBeVisible();
		await slider.fill('50');
		await expect(dialog.getByText('50%').first()).toBeVisible();
		await screenshot(page, JOURNEY, `transform-${size.tag}`);

		await dialog.getByRole('button', { name: 'Open another image' }).first().click();
		const openMenu = page.getByRole('menu', { name: 'Open an image' });
		await expect(openMenu).toBeVisible();
		await expect(openMenu.getByText(/unsaved changes will be lost/)).toBeVisible();
		const [second] = await Promise.all([
			page.waitForEvent('filechooser'),
			openMenu.getByRole('menuitem', { name: 'Browse files' }).click()
		]);
		await second.setFiles({ name: 'other.png', mimeType: 'image/png', buffer: fixturePng(96) });
		await expect(visibleText(dialog, '96 × 96')).toBeVisible();
		await reveal(dialog, mobile, 'Layers');
		await expect(dialog.getByRole('button', { name: /Select layer/ })).toHaveCount(1);
	});

	test(`lets a plugin register a tool and a filter at ${size.tag}`, async ({ page }) => {
		test.setTimeout(240000);
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const field = await openEditField(page, mobile, 128);
		await page.evaluate(() => {
			const editor = (window as unknown as { __potionui: { imageEditor: any } }).__potionui.imageEditor;
			let start: { x: number; y: number } | null = null;
			editor.registerTool({
				id: 'e2e-line',
				label: 'E2E line',
				icon: 'M5 19L19 5',
				key: 'N',
				group: 'shapes',
				options: ['size', 'color'],
				cursor: 'crosshair',
				pointerDown(_host: unknown, point: { x: number; y: number }) {
					start = point;
				},
				pointerUp(host: any, point: { x: number; y: number }) {
					const layer = host.activeLayer;
					const before = host.snapshotLayer(layer);
					const context = layer.canvas.getContext('2d');
					context.strokeStyle = 'rgb(255, 0, 255)';
					context.lineWidth = 12;
					context.beginPath();
					context.moveTo(start!.x - layer.x, start!.y - layer.y);
					context.lineTo(point.x - layer.x, point.y - layer.y);
					context.stroke();
					host.commitPixels(
						'E2E line',
						layer,
						{ x: 0, y: 0, width: layer.canvas.width, height: layer.canvas.height },
						before
					);
				}
			});
			editor.registerFilter({
				id: 'e2e-green',
				label: 'E2E green',
				params: [],
				toggle: true,
				active: (values: { on?: boolean }) => values.on === true,
				apply(image: { width: number; height: number; data: Uint8ClampedArray }) {
					const data = new Uint8ClampedArray(image.data);
					for (let i = 0; i < data.length; i += 4) {
						data[i] = 0;
						data[i + 1] = 255;
						data[i + 2] = 0;
					}
					return { width: image.width, height: image.height, data };
				}
			});
		});

		const dialog = await openEditor(page, field);
		const canvas = dialog.getByLabel('Drawing canvas');
		const line = dialog.getByRole('button', { name: 'E2E line' });
		await expect(line).toBeVisible();
		await page.keyboard.press('n');
		await expect(line).toHaveAttribute('aria-pressed', 'true');
		await strokeAcross(page, canvas, [0.3, 0.5], [0.7, 0.5]);
		near(await stagePixel(canvas, 0.5, 0.5), [255, 0, 255], 40);
		await expect(dialog.getByRole('button', { name: /^Undo E2E line/ })).toBeEnabled();

		await page.keyboard.press('a');
		await dialog.getByRole('button', { name: 'E2E green', exact: true }).click();
		await expect.poll(async () => (await stagePixel(canvas, 0.3, 0.3))[1]).toBe(255);
		near(await stagePixel(canvas, 0.3, 0.3), [0, 255, 0], 20);
		await screenshot(page, JOURNEY, `plugin-${size.tag}`);
	});
}

test('offers Edit in the library item view', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	await openEditField(page, false, 128);
	await page.goto('/library');
	await page.locator('[data-library-card]').first().click();
	const edit = page.getByRole('button', { name: /^Edit$/ }).first();
	await expect(edit).toBeVisible({ timeout: 15000 });
	await edit.click();
	await expect(page.getByRole('dialog', { name: 'Edit image' })).toBeVisible();
	await screenshot(page, JOURNEY, 'library-edit');
});
