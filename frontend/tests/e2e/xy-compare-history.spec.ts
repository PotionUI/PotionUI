import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import {
	ASPECT,
	QUALITY,
	captureStartRequest,
	createGrid,
	enableFakeModels,
	openGridFromHistory,
	waitGridSettled
} from './xyCompareHelpers';

const JOURNEY = 'xy-compare-history';

const stackCard = (page: Page) =>
	page.locator('[data-history-card]').filter({ has: page.getByTestId('grid-stack-chip') }).first();

test.describe.configure({ mode: 'serial' });

test.describe('X/Y compare in History', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let request: Record<string, any> = {};
	let gridId = '';

	test('setup: a settled 3 × 2 grid', async ({ page }) => {
		test.setTimeout(300000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await enableFakeModels(page, token);
		request = await captureStartRequest(page);
		gridId = await createGrid(page, token, request, QUALITY, ASPECT);
		await waitGridSettled(page, token, gridId);
	});

	test('one stack card stands for the whole grid', async ({ page }) => {
		await loginAsOwner(page);
		await page.goto('/history');
		const card = stackCard(page);
		await expect(card).toBeVisible({ timeout: 20000 });
		await expect(card.getByTestId('grid-stack-chip')).toHaveText('3 × 2');
		await expect(card.getByTestId('grid-stack-title')).toHaveText('Quality × Aspect ratio');
		await expect(card.getByTestId('grid-stack-count')).toHaveText('6 cells');
		await expect(card.getByTestId('grid-stack-layers')).toBeAttached();
		await expect(page.locator('[data-history-card]').filter({ has: page.getByTestId('grid-stack-chip') })).toHaveCount(1);
		await expect(page.getByTestId('grid-cell-chip')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'stack-card-1440');
	});

	test('opening the stack shows the grid viewer in overview with every cell', async ({ page }) => {
		await loginAsOwner(page);
		await openGridFromHistory(page);
		await expect(page.getByTestId('history-grid-dims')).toContainText('Quality × Aspect ratio');
		await expect(page.getByTestId('history-grid-dims')).toContainText('3 × 2');
		await expect(page.getByTestId('compare-cell')).toHaveCount(6);
		await expect(page.getByTestId('compare-count')).toHaveText('6/6');
		await expect(page.locator('[data-cell-state="completed"]')).toHaveCount(6);
		await expect(page.getByRole('button', { name: /Cancel all/ })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'grid-overview-1440');
	});

	test('a cell\'s details carry the Part of X/Y grid card and X and Y tags', async ({ page }) => {
		await loginAsOwner(page);
		await openGridFromHistory(page);
		await page.getByTestId('compare-cell').nth(4).click();
		await page.getByRole('button', { name: 'Open generation details' }).click();

		const card = page.getByTestId('grid-details-card');
		await expect(card).toBeVisible();
		await expect(page.getByTestId('grid-details-summary')).toContainText('Part of X/Y grid');
		await expect(page.getByTestId('grid-details-summary')).toContainText('quality = 5');
		await expect(page.getByTestId('grid-details-summary')).toContainText('aspect_ratio = 16:9');
		await expect(card).toContainText('cell 5 of 6');
		await expect(card.getByTestId('grid-minimap-cell')).toHaveCount(6);
		await expect(page.getByRole('heading', { name: 'Parameters' })).toBeVisible();
		await expect(page.getByTestId('axis-tag')).toHaveText(['X', 'Y']);
		await screenshot(page, JOURNEY, 'cell-details-1440');

		await card.getByRole('button', { name: 'Back to compare view' }).click();
		await expect(page.getByTestId('cell-viewer-card')).toBeVisible();
	});

	test.describe('mobile', () => {
		test.use({ viewport: { width: 390, height: 844 } });

		test('the grid scrolls in two directions with pinned labels and the viewer fills the screen', async ({ page }) => {
			await loginAsOwner(page);
			await openGridFromHistory(page);

			const scroller = page.getByTestId('compare-scroller');
			await expect(scroller).toHaveAttribute('data-scrolls', 'true');
			expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);

			const yLabel = page.getByTestId('compare-y-label').first();
			const xLabel = page.getByTestId('compare-x-label').first();
			const yBefore = await yLabel.boundingBox();
			const xBefore = await xLabel.boundingBox();
			await scroller.evaluate((el) => {
				el.scrollLeft = 200;
				el.scrollTop = 40;
			});
			await page.waitForTimeout(250);
			const yAfter = await yLabel.boundingBox();
			const xAfter = await xLabel.boundingBox();
			expect(Math.abs((yAfter?.x ?? 0) - (yBefore?.x ?? 0))).toBeLessThan(2);
			expect(Math.abs((xAfter?.y ?? 0) - (xBefore?.y ?? 0))).toBeLessThan(2);
			await screenshot(page, JOURNEY, 'mobile-scroller-390');

			await page.getByTestId('compare-cell').nth(1).click();
			const modal = page.getByRole('dialog').filter({ hasText: 'Compare grid' }).last();
			await expect(modal).toBeVisible();
			const box = await modal.boundingBox();
			expect(box?.width ?? 0).toBeGreaterThanOrEqual(385);
			await expect(page.getByTestId('grid-minimap-cell')).toHaveCount(6);
			await page.getByTestId('grid-minimap-cell').nth(4).click();
			await expect(page.locator('[data-generation-position]')).toContainText('5 / 6');
			await screenshot(page, JOURNEY, 'mobile-viewer-390');
		});
	});
});
