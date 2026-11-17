import { test, expect, type Locator, type Page } from '@playwright/test';
import { screenshot } from './helpers';
import { catalogWith, resultVideo, type MediaToolLog } from './mediaToolMocks';
import {
	SIZES,
	audioItem,
	imageItem,
	mountField,
	pickTool,
	prepareFieldPage,
	published,
	videoItem,
	type Size
} from './mediaToolFieldHost';

const JOURNEY = 'media-tool-dialog';

function dialogOf(page: Page, name: string): Locator {
	return page.getByRole('dialog', { name, exact: true });
}

async function expectFits(page: Page, dialog: Locator) {
	const box = await dialog.boundingBox();
	const viewport = page.viewportSize()!;
	expect(box).not.toBeNull();
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.y).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
	expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow, 'the page scrolls sideways').toBeLessThanOrEqual(0);
	const inner = await dialog.evaluate((el) => {
		const rect = el.getBoundingClientRect();
		let widest = 0;
		el.querySelectorAll('*').forEach((child) => {
			const childRect = child.getBoundingClientRect();
			if (childRect.width === 0) return;
			widest = Math.max(widest, childRect.right - rect.right);
		});
		return widest;
	});
	expect(inner, 'something sticks out of the dialog').toBeLessThanOrEqual(1);
}

for (const size of SIZES) {
	test.describe(`media tool dialog at ${size.tag}`, () => {
		let log: MediaToolLog;

		test.beforeEach(async ({ page }) => {
			log = await prepareFieldPage(page, size);
		});

		test('fit canvas is a form built from its params with a before and after preview', async ({ page }) => {
			const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'fit_canvas');

			const dialog = dialogOf(page, 'Fit canvas');
			await expect(dialog).toBeVisible();
			await expect(dialog.locator('[data-tool-subject]')).toHaveText('harbour-1.png');

			await expect(dialog.locator('[data-field-name="width"] input')).toHaveValue('1024');
			await expect(dialog.locator('[data-field-name="height"] input')).toHaveValue('1024');
			await expect(dialog.locator('[data-field-name="scale_percent"]')).toContainText('100%');
			await expect(dialog.locator('[data-field-name="anchor"] button[data-anchor]')).toHaveCount(9);
			await expect(dialog.locator('[data-field-name="anchor"] [aria-pressed="true"]')).toHaveAttribute('data-anchor', 'center');
			await expect(dialog.locator('[data-field-name="fill"] [aria-pressed="true"]')).toHaveText('Transparent');

			await expect(dialog.locator('[data-before-after]')).toBeVisible();
			await expect(dialog.locator('[data-before-after] img[alt="After"]')).toBeVisible();
			await expect(dialog.getByText('Before', { exact: true })).toBeVisible();
			await expect(dialog.getByText('After', { exact: true })).toBeVisible();

			expect(log.previews[0]).toEqual({
				tool: 'fit_canvas',
				params: { width: 1024, height: 1024, scale_percent: 100, anchor: 'center', fill: 'transparent' },
				input: { item_id: 'row-image-1' }
			});
			await expect(dialog.locator('[data-tool-note]')).toHaveText(
				'The result replaces this image in the field. The library copy stays as it is.'
			);
			await expect(dialog.getByRole('button', { name: 'Replace original' })).toHaveCount(0);

			await expectFits(page, dialog);
			await screenshot(page, JOURNEY, `fit-canvas-${size.tag}`);
		});

		test('every control reports its change to the preview, folded into one request', async ({ page }) => {
			const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'fit_canvas');
			const dialog = dialogOf(page, 'Fit canvas');
			await expect.poll(() => log.previews.length).toBe(1);

			await dialog.locator('[data-anchor="top_left"]').click();
			await dialog.locator('[data-anchor="bottom_right"]').click();
			await expect.poll(() => log.previews.length).toBe(2);
			expect((log.previews[1].params as Record<string, unknown>).anchor).toBe('bottom_right');

			await dialog.locator('[data-field-name="width"] input').fill('1920');
			await expect.poll(() => log.previews.length).toBe(3);
			expect((log.previews[2].params as Record<string, unknown>).width).toBe(1920);

			await dialog.locator('[data-field-name="scale_percent"] input[type="range"]').fill('50');
			await expect.poll(() => log.previews.length).toBe(4);
			expect((log.previews[3].params as Record<string, unknown>).scale_percent).toBe(50);

			await dialog.getByRole('button', { name: 'Colour' }).click();
			await dialog.locator('[data-field-name="fill"] input[type="text"]').fill('#ff0000');
			await dialog.locator('[data-field-name="fill"] input[type="text"]').press('Enter');
			await expect.poll(() => log.previews.at(-1) && (log.previews.at(-1)!.params as Record<string, unknown>).fill).toBe('#FF0000');
			await screenshot(page, JOURNEY, `fit-canvas-colour-${size.tag}`);
		});

		test('a number that breaks its bound is flagged and holds the preview and Apply back', async ({ page }) => {
			const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'fit_canvas');
			const dialog = dialogOf(page, 'Fit canvas');
			await expect.poll(() => log.previews.length).toBe(1);

			await dialog.locator('[data-field-name="width"] input').fill('0');
			await expect(dialog.getByText('At least 1')).toBeVisible();
			await expect(dialog.getByRole('button', { name: 'Apply' })).toBeDisabled();
			await page.waitForTimeout(600);
			expect(log.previews).toHaveLength(1);

			await dialog.locator('[data-field-name="width"] input').fill('800');
			await expect(dialog.getByRole('button', { name: 'Apply' })).toBeEnabled();
		});

		test('a field that depends on another appears only when it applies', async ({ page }) => {
			const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'color_key');
			const dialog = dialogOf(page, 'Color key');

			await expect(dialog.locator('[data-field-name="key_color"]')).toHaveCount(0);
			await dialog.locator('[data-field-name="key_mode"]').getByRole('button', { name: 'Color', exact: true }).click();
			await expect(dialog.locator('[data-field-name="key_color"]')).toBeVisible();
			await expect(dialog.locator('[data-field-name="key_color"] input[type="text"]')).toHaveValue('#00B140');
			await dialog.locator('[data-field-name="key_mode"]').getByRole('button', { name: 'Auto', exact: true }).click();
			await expect(dialog.locator('[data-field-name="key_color"]')).toHaveCount(0);
			await screenshot(page, JOURNEY, `color-key-${size.tag}`);
		});
	});
}

test.describe('media tool dialog behaviour', () => {
	const size: Size = SIZES[0];

	test('opens on the values the user last used', async ({ page }) => {
		await prepareFieldPage(page, size, { catalog: catalogWith({ speed: { speed: 3.5, keep_pitch: false } }) });
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');

		await expect(dialog.locator('[data-field-name="speed"]')).toContainText('3.5×');
		await expect(dialog.locator('[data-field-name="keep_pitch"] input[type="checkbox"]')).not.toBeChecked();
	});

	test('shows the size and length the result will have', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');

		await expect(dialog.locator('[data-tool-readout]')).toContainText('1280×720 · 00:06.0');
		await dialog.locator('[data-field-name="speed"] input[type="range"]').fill('4');
		await expect(dialog.locator('[data-tool-readout]')).toContainText('1280×720 · 00:03.0');
		await expect(dialog.locator('[data-before-after]')).toHaveCount(0);
	});

	test('applies the form, waits with progress, then writes the result into the field and closes', async ({ page }) => {
		const log = await prepareFieldPage(page, size, { hold: true, items: [resultVideo()] });
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');

		await dialog.getByRole('button', { name: 'Apply' }).click();

		await expect(dialog.locator('[data-tool-progress]')).toContainText('Encoding · 40%');
		await expect(dialog.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40');
		await expect(dialog.getByRole('button', { name: 'Cancel edit' })).toBeVisible();
		await expect(dialog.getByRole('button', { name: 'Apply' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'speed-running');

		expect(log.runs).toEqual([
			{
				steps: [{ tool: 'speed', params: { speed: 2, keep_pitch: true } }],
				inputs: [{ item_id: 'row-video-1' }],
				output: { mode: 'new' }
			}
		]);

		log.release();
		await expect(dialog).toHaveCount(0);
		const values = await published(page);
		expect(values.at(-1)).toMatchObject({ url: '/api/e2e-media/video-9.webm', type: 'video' });
	});

	test('cancelling a run asks the server to stop it and hands the form back', async ({ page }) => {
		const log = await prepareFieldPage(page, size, { hold: true });
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');
		await dialog.getByRole('button', { name: 'Apply' }).click();

		await dialog.getByRole('button', { name: 'Cancel edit' }).click();

		await expect.poll(() => log.cancels).toEqual(['run-1']);
		await expect(dialog.getByRole('button', { name: 'Apply' })).toBeVisible();
		await expect(dialog.locator('[data-tool-progress]')).toHaveCount(0);
		expect(await published(page)).toEqual([]);
		const pollsAfterCancel = log.runPolls;
		await page.waitForTimeout(1200);
		expect(log.runPolls - pollsAfterCancel).toBeLessThanOrEqual(1);
	});

	test('shows why the server refused the request and keeps the dialog open', async ({ page }) => {
		await prepareFieldPage(page, size, {
			refuse: { status: 400, body: { error: 'invalid_params', message: 'speed must be at most 4', field: 'speed', step_index: 0 } }
		});
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');

		await dialog.getByRole('button', { name: 'Apply' }).click();

		await expect(dialog.locator('[data-tool-failure]')).toHaveText('speed must be at most 4');
		await expect(dialog.getByRole('button', { name: 'Apply' })).toBeEnabled();
		await screenshot(page, JOURNEY, 'speed-refused');
	});

	test('a rotate dialog offers its three choices as a segmented control and previews a video from its first frame', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'rotate');
		const dialog = dialogOf(page, 'Rotate');

		const choices = dialog.locator('[data-field-name="degrees"] button');
		await expect(choices).toHaveText(['90', '180', '270']);
		await expect(dialog.locator('[data-before-after] video')).toHaveCount(1);
		await choices.nth(2).click();
		await expect.poll(() => log.previews.at(-1) && (log.previews.at(-1)!.params as Record<string, unknown>).degrees).toBe(270);
	});

	test('shows where the before and after split has focus when it is reached by keyboard', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'fit_canvas');
		const dialog = dialogOf(page, 'Fit canvas');
		const splitInput = dialog.locator('[data-split-input]');
		await expect(splitInput).toHaveCount(1);

		await splitInput.focus();
		await page.keyboard.press('Shift+Tab');
		await page.keyboard.press('Tab');
		await expect(splitInput).toBeFocused();

		const handle = dialog.locator('[data-split-handle]');
		const ring = await handle.evaluate((el) => getComputedStyle(el).boxShadow);
		expect(ring).not.toBe('none');
		const before = await dialog.locator('[data-split-line]').evaluate((el) => getComputedStyle(el).width);
		expect(parseFloat(before)).toBeGreaterThanOrEqual(4);
		await screenshot(page, JOURNEY, 'split-focus');

		await page.keyboard.press('ArrowRight');
		const moved = await splitInput.inputValue();
		expect(Number(moved)).toBeGreaterThan(50);
	});

	test('closes on Escape and on Cancel without starting anything', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });

		await pickTool(page, host, 'flip');
		await expect(dialogOf(page, 'Flip')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(dialogOf(page, 'Flip')).toHaveCount(0);

		await pickTool(page, host, 'flip');
		await dialogOf(page, 'Flip').getByRole('button', { name: 'Cancel', exact: true }).click();
		await expect(dialogOf(page, 'Flip')).toHaveCount(0);
		expect(log.runs).toEqual([]);
	});

	test('keeps focus inside the dialog while it is open', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'flip');
		const dialog = dialogOf(page, 'Flip');

		for (let press = 0; press < 12; press += 1) {
			await page.keyboard.press('Tab');
			const inside = await dialog.evaluate((el) => el.contains(document.activeElement));
			expect(inside).toBe(true);
		}
	});

	test('draws no text below 12 pixels', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'fit_canvas');
		const dialog = dialogOf(page, 'Fit canvas');
		await expect(dialog.locator('[data-before-after]')).toBeVisible();

		const small = await dialog.evaluate((el) => {
			const found: string[] = [];
			el.querySelectorAll('*').forEach((node) => {
				const own = Array.from(node.childNodes).some((child) => child.nodeType === 3 && (child.textContent || '').trim().length > 0);
				if (!own) return;
				const px = parseFloat(getComputedStyle(node).fontSize);
				if (px < 12) found.push(`${(node.textContent || '').trim().slice(0, 30)} ${px}px`);
			});
			return found;
		});
		expect(small).toEqual([]);
	});

	test('a result for an audio clip names the clip, not an image', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: audioItem(1) });
		await pickTool(page, host, 'speed');
		const dialog = dialogOf(page, 'Speed');
		await expect(dialog.locator('[data-tool-note]')).toHaveText(
			'The result replaces this clip in the field. The library copy stays as it is.'
		);
		await expect(dialog.locator('[data-tool-source]')).toContainText('voice-1.wav');
	});
});
