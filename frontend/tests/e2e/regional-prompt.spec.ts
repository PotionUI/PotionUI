import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import {
	SWITCH,
	assignMoss,
	centerCanvas,
	drawRegion,
	fillCaption,
	mockPresetVars,
	openMoss,
	setTheme,
	switchToRegional,
	typePrompt
} from './regionalHelpers';

const JOURNEY = 'regional-prompt';

test.describe('Regional prompting', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('switch is hidden when the preset has no option', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await loginAsOwner(page);
		const name = await assignMoss(page);
		test.skip(!name, 'No MOSS-Image preset on this instance');
		await mockPresetVars(page, false);
		await openMoss(page, name!);
		await expect(page.locator('[data-header-lead]').first()).toBeVisible({ timeout: 15000 });
		await expect(page.locator(SWITCH)).toHaveCount(0);
		await expect(page.getByTestId('regional-editor')).toHaveCount(0);
	});

	test('draw, resize, delete, resolution reshape, resolved JSON and screenshots', async ({ page }) => {
		test.setTimeout(240000);
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await loginAsOwner(page);
		const name = await assignMoss(page);
		test.skip(!name, 'No MOSS-Image preset on this instance');
		await mockPresetVars(page, true);
		await openMoss(page, name!);
		await expect(page.locator(SWITCH)).toBeVisible({ timeout: 20000 });
		await typePrompt(page, 'A vintage travel poster of a lighthouse');
		await switchToRegional(page);

		const canvas = page.getByTestId('regional-canvas');
		await expect(page.getByTestId('regional-list')).toContainText('The prompt above describes the whole image.');
		await screenshot(page, JOURNEY, '01-two-pane-empty-dark');

		await drawRegion(page, [0.1, 0.05], [0.9, 0.22]);
		await drawRegion(page, [0.1, 0.35], [0.5, 0.85]);
		await drawRegion(page, [0.62, 0.45], [0.92, 0.85]);
		await expect(page.locator('[data-region]')).toHaveCount(3);
		await expect(page.locator(SWITCH)).toContainText('3');
		await expect(page.locator('[data-region="2"]')).toHaveClass(/sel/);
		await expect(page.locator('[data-card="2"]')).toHaveClass(/sel/);

		await fillCaption(page, 0, 'Title "EXPLORE THE COAST" in bold condensed cream letters');
		await fillCaption(page, 1, 'A weathered stone lighthouse on a basalt cliff');
		await fillCaption(page, 2, 'A hiker in a yellow rain jacket seen from behind');

		await page.locator('[data-region="1"]').click({ position: { x: 20, y: 30 } });
		await expect(page.locator('[data-card="1"]')).toHaveClass(/sel/);

		const handle = page.locator('[data-region="1"] [data-corner="se"]');
		await centerCanvas(page);
		const before = (await page.locator('[data-region="1"]').boundingBox())!;
		const hb = (await handle.boundingBox())!;
		await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
		await page.mouse.down();
		await page.mouse.move(hb.x + hb.width / 2 - 30, hb.y + hb.height / 2 - 30, { steps: 6 });
		await page.mouse.up();
		const after = (await page.locator('[data-region="1"]').boundingBox())!;
		expect(after.width).toBeLessThan(before.width - 20);
		expect(after.x).toBeCloseTo(before.x, 0);

		await screenshot(page, JOURNEY, '02-two-pane-dark');

		await page.locator('[data-card="0"] textarea').focus();
		await expect(page.locator('[data-region="0"]')).toHaveClass(/sel/);

		const resolved = page.getByRole('button', { name: /Resolved prompt/ });
		await resolved.scrollIntoViewIfNeeded();
		await resolved.click();
		const resolvedText = page.locator('.whitespace-pre-wrap').filter({ hasText: 'bbox_coord_space' });
		await expect(resolvedText).toContainText('"label": "text"');
		await expect(resolvedText).toContainText('"text": "EXPLORE THE COAST"');
		await expect(resolvedText).toContainText('"bbox_format": "yxyx"');
		await screenshot(page, JOURNEY, '03-resolved-dark');

		const sizeBefore = await page.getByTestId('regional-size').textContent();
		const aspectBefore = (await canvas.boundingBox())!;
		const resolutionCard = page
			.locator('.field-card')
			.filter({ has: page.locator('label', { hasText: 'Resolution' }) })
			.first();
		await resolutionCard.scrollIntoViewIfNeeded();
		await resolutionCard.locator('button[aria-haspopup="listbox"]').click();
		const rows = page.locator('[role="listbox"]').filter({ has: page.locator('input[type="text"]') }).locator('button[role="option"]');
		const count = await rows.count();
		let changed = false;
		for (let i = 0; i < count && !changed; i++) {
			if ((await rows.nth(i).getAttribute('aria-selected')) === 'true') continue;
			await rows.nth(i).click();
			changed = true;
		}
		expect(changed, 'a different resolution row must exist').toBe(true);
		await expect(page.getByTestId('regional-size')).not.toHaveText(sizeBefore!);
		await page.getByTestId('regional-canvas').scrollIntoViewIfNeeded();
		const aspectAfter = (await canvas.boundingBox())!;
		expect(aspectAfter.width / aspectAfter.height).not.toBeCloseTo(aspectBefore.width / aspectBefore.height, 1);
		await expect(page.locator('[data-region]')).toHaveCount(3);

		await page.locator('[data-region="2"]').click({ position: { x: 20, y: 30 } });
		await page.keyboard.press('Delete');
		await expect(page.locator('[data-region]')).toHaveCount(2);
		await page.locator('[data-card="0"] button[aria-label^="Remove region"]').click();
		await expect(page.locator('[data-region]')).toHaveCount(1);

		await page.getByTestId('regional-canvas').focus();
		await page.keyboard.press('Escape');
		await expect(page.locator('.rg-box.sel')).toHaveCount(0);

		await page.locator(SWITCH).getByRole('button', { name: 'Text' }).click();
		await expect(page.getByTestId('regional-editor')).toHaveCount(0);
		await page.locator(SWITCH).getByRole('button', { name: /^Regional/ }).click();
		await expect(page.locator('[data-region]')).toHaveCount(1);
	});

	test('three-pane and light screenshots', async ({ page }) => {
		test.setTimeout(240000);
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await loginAsOwner(page);
		const name = await assignMoss(page);
		test.skip(!name, 'No MOSS-Image preset on this instance');
		await mockPresetVars(page, true);
		await openMoss(page, name!);
		await expect(page.locator(SWITCH)).toBeVisible({ timeout: 20000 });
		await typePrompt(page, 'A vintage travel poster of a lighthouse on a basalt cliff at dusk');
		await switchToRegional(page);
		await drawRegion(page, [0.1, 0.05], [0.9, 0.22]);
		await drawRegion(page, [0.1, 0.35], [0.5, 0.85]);
		await drawRegion(page, [0.62, 0.45], [0.92, 0.85]);
		await fillCaption(page, 0, 'Title "EXPLORE THE COAST" in bold condensed cream letters with a worn print texture');
		await fillCaption(page, 1, 'A weathered stone lighthouse on a basalt cliff, its lamp glowing warm against a teal dusk sky');
		await fillCaption(page, 2, 'A hiker in a yellow rain jacket seen from behind, looking out over the sea');
		await page.locator('[data-region="1"]').click({ position: { x: 20, y: 30 } });
		await setTheme(page, 'light');
		await screenshot(page, JOURNEY, '04-two-pane-light');
		await setTheme(page, 'dark');

		await page.getByRole('button', { name: /Two panes/ }).first().click();
		await page.waitForTimeout(1200);
		await expect(page.getByTestId('regional-editor')).toBeVisible();
		await page.getByTestId('regional-canvas').scrollIntoViewIfNeeded();
		await screenshot(page, JOURNEY, '05-three-pane-dark');
	});
});

test.describe('Regional prompting on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('prompt sheet shows the switch and the stacked editor', async ({ page }) => {
		test.setTimeout(240000);
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await loginAsOwner(page);
		const name = await assignMoss(page);
		test.skip(!name, 'No MOSS-Image preset on this instance');
		await mockPresetVars(page, true);
		await openMoss(page, name!);
		await page.getByRole('button', { name: 'Prompt', exact: true }).first().click();
		await expect(page.locator(SWITCH)).toBeVisible({ timeout: 20000 });
		await typePrompt(page, 'A vintage travel poster of a lighthouse on a basalt cliff at dusk');
		await switchToRegional(page);
		await drawRegion(page, [0.1, 0.05], [0.9, 0.22]);
		await drawRegion(page, [0.1, 0.35], [0.5, 0.85]);
		await fillCaption(page, 0, 'Title "EXPLORE THE COAST" in bold cream letters');
		await fillCaption(page, 1, 'A weathered stone lighthouse on a basalt cliff');
		const overflow = await page.evaluate(() => {
			const el = document.scrollingElement!;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflow).toBeLessThanOrEqual(0);
		await page.getByTestId('regional-canvas').scrollIntoViewIfNeeded();
		await screenshot(page, JOURNEY, '06-phone-sheet-dark');
	});
});
