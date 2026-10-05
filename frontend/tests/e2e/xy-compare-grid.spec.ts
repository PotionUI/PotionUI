import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import {
	ASPECT,
	QUALITY,
	backendId,
	captureStartRequest,
	closeCompareDrawer,
	compareGenerate,
	createGrid,
	enableFakeModels,
	field,
	openFakeStudio,
	openCompareDrawer,
	openGridFromHistory,
	pickAxisField,
	pickModel,
	setKnob,
	turnOffCompare,
	typePrompt,
	waitGridSettled
} from './xyCompareHelpers';

const JOURNEY = 'xy-compare-grid';

const cells = (page: Page) => page.getByTestId('compare-cell');
const position = (page: Page) => page.locator('[data-generation-position]');

async function pressInViewer(page: Page, key: string) {
	await page.keyboard.press(key);
	await page.waitForTimeout(250);
}

async function positionText(page: Page) {
	return ((await position(page).textContent()) ?? '').replace(/\s+/g, ' ').trim();
}

test.describe.configure({ mode: 'serial' });

test.describe('X/Y compare grid view', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let request: Record<string, any> = {};

	test('setup: models are enabled and one ordinary generation gives a request to compare', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await enableFakeModels(page, token);
		request = await captureStartRequest(page);
		expect(request.form_data.model).toBeTruthy();
	});

	test('the Workbench draws labelled axes and the cells fill in until the grid is done', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await openFakeStudio(page);
		await pickModel(page, 'Fake Image');
		await typePrompt(page, 'a lighthouse on a cliff at dusk');

		const drawer = await openCompareDrawer(page);
		await pickAxisField(page, 'X', 'quality');
		const xCard = drawer.getByTestId('axis-card-x');
		await expect(xCard.getByTestId('chips-count')).toHaveText('4 of 10');
		await xCard.locator('button[aria-pressed="true"]', { hasText: /^\s*4\s*$/ }).click();
		await expect(xCard.getByTestId('chips-count')).toHaveText('3 of 10');
		await pickAxisField(page, 'Y', 'aspect_ratio');
		await expect(drawer.getByTestId('axis-card-y').getByTestId('chips-count')).toHaveText('2 of 2');
		await expect(drawer.getByTestId('compare-summary')).toContainText('3 × 2 = 6 generations');
		await closeCompareDrawer(page);
		await expect(compareGenerate(page, 6)).toBeVisible({ timeout: 15000 });

		const grid = page.getByTestId('compare-grid');
		await expect(grid).toBeVisible();
		await expect(cells(page)).toHaveCount(6);
		await expect(grid.getByTestId('compare-x-label')).toHaveText(['1', '2', '3']);
		await expect(grid.getByTestId('compare-y-label')).toHaveText(['1:1', '16:9']);
		await expect(page.locator('[data-cell-state="empty"]')).toHaveCount(6);
		await screenshot(page, JOURNEY, 'workbench-preview-1440');

		await compareGenerate(page, 6).click();
		await expect(page.locator('[data-cell-state="queued"], [data-cell-state="running"]').first()).toBeVisible({
			timeout: 15000
		});
		await expect(page.getByTestId('compare-progress')).toBeVisible();
		await expect(page.getByRole('button', { name: /Cancel all/ }).first()).toBeVisible();
		await screenshot(page, JOURNEY, 'workbench-running-1440');

		await expect(page.getByTestId('compare-count')).toHaveText('6/6', { timeout: 150000 });
		await expect(page.locator('[data-cell-state="completed"]')).toHaveCount(6);
		await expect(page.getByText('All done')).toBeVisible();
		await expect(page.getByTestId('compare-grid').locator('img').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'workbench-done-1440');

		await turnOffCompare(page);
		await expect(page.getByTestId('compare-grid')).toHaveCount(0);
	});

	test('a finished grid opens a cell with 2D arrows, a map and Use these settings', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		const gridId = await createGrid(page, token, request, QUALITY, ASPECT);
		await waitGridSettled(page, token, gridId);
		await openGridFromHistory(page);

		await expect(page.getByTestId('compare-count')).toHaveText('6/6', { timeout: 30000 });
		await expect(page.getByTestId('compare-x-label')).toHaveText(['3', '5', '7']);
		await expect(page.getByTestId('compare-y-label')).toHaveText(['1:1', '16:9']);

		await cells(page).nth(1).click();
		await expect(page.getByRole('dialog').filter({ hasText: 'Compare grid' }).last()).toBeVisible();
		expect(await positionText(page)).toBe('2 / 6');
		await expect(page.getByTestId('grid-minimap-cell')).toHaveCount(6);
		await expect(page.getByTestId('grid-minimap-cell').nth(1)).toHaveAttribute('aria-current', 'true');

		await pressInViewer(page, 'ArrowRight');
		expect(await positionText(page)).toBe('3 / 6');
		await pressInViewer(page, 'ArrowDown');
		expect(await positionText(page)).toBe('6 / 6');
		await pressInViewer(page, 'ArrowLeft');
		expect(await positionText(page)).toBe('5 / 6');
		await pressInViewer(page, 'ArrowUp');
		expect(await positionText(page)).toBe('2 / 6');
		await pressInViewer(page, 'ArrowUp');
		expect(await positionText(page)).toBe('2 / 6');
		await screenshot(page, JOURNEY, 'viewer-cell-1440');

		await page.getByTestId('grid-minimap-cell').nth(5).click();
		expect(await positionText(page)).toBe('6 / 6');
		await expect(page.getByTestId('use-settings-note')).toContainText('quality = 7');
		await expect(page.getByTestId('use-settings-note')).toContainText('aspect_ratio = 16:9');

		await page.getByRole('button', { name: 'Use these settings' }).click();
		await page.waitForURL(/\/generate/, { timeout: 15000 });
		await expect(field(page, 'quality').locator('button[aria-haspopup="listbox"]')).toHaveText(/7/, { timeout: 20000 });
		await expect(field(page, 'aspect_ratio').locator('button[aria-haspopup="listbox"]')).toHaveText(/16:9/);
	});

	test('failed cells are tinted with a plain reason and Retry failed completes them', async ({ page }) => {
		test.setTimeout(300000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		const id = await backendId(page, token);
		await setKnob(page, token, id, { fail_kind: 'unavailable' });
		let gridId = '';
		try {
			gridId = await createGrid(page, token, request, { ...QUALITY, values: QUALITY.values.slice(0, 2) }, null);
			const settled = await waitGridSettled(page, token, gridId);
			expect((settled.cells as Array<{ status: string }>).every((cell) => cell.status === 'failed')).toBe(true);
		} finally {
			await setKnob(page, token, id, { fail_kind: '' });
		}

		await openGridFromHistory(page);
		await expect(page.getByTestId('compare-failed')).toContainText('2 failed');
		const failed = page.locator('[data-cell-state="failed"]');
		await expect(failed).toHaveCount(2);
		await expect(failed.first().getByTestId('compare-cell-error')).not.toBeEmpty();
		await expect(failed.first().getByRole('button', { name: 'Retry' })).toBeVisible();
		await screenshot(page, JOURNEY, 'failed-cells-1440');

		await cells(page).first().click();
		await expect(page.getByTestId('cell-viewer-empty')).toContainText('Failed');
		await expect(page.getByRole('button', { name: /Retry failed 2/ })).toBeEnabled();
		await page.keyboard.press('Escape');

		await page.getByRole('button', { name: 'Retry failed' }).first().click();
		await expect(page.getByTestId('compare-count')).toHaveText('2/2', { timeout: 120000 });
		await expect(page.locator('[data-cell-state="failed"]')).toHaveCount(0);
		await expect(page.getByTestId('compare-failed')).toHaveCount(0);
	});

	test('Export stitched opens the Stitch modal on the grid layout and downloads a PNG', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		const gridId = await createGrid(page, token, request, QUALITY, ASPECT);
		await waitGridSettled(page, token, gridId);
		await openGridFromHistory(page);
		await expect(page.getByTestId('compare-count')).toHaveText('6/6', { timeout: 30000 });

		await cells(page).first().click();
		await page.getByRole('button', { name: 'Export stitched' }).click();

		const dialog = page.getByRole('dialog').filter({ hasText: 'Stitch' }).last();
		await expect(dialog).toBeVisible({ timeout: 30000 });
		await expect(dialog.getByRole('button', { name: 'Grid', exact: true })).toHaveAttribute('aria-pressed', 'true');
		await expect(dialog.getByRole('switch', { name: 'Axis labels' })).toBeChecked();
		await expect(dialog.getByRole('switch', { name: 'Skip failed cells' })).not.toBeChecked();
		await expect(dialog.getByText('Under each cell')).toBeVisible();
		await expect(dialog.getByRole('button', { name: 'axis values' })).toHaveAttribute('aria-pressed', 'true');
		await expect(dialog.getByRole('button', { name: 'seed' })).toBeVisible();
		await expect(dialog.getByRole('button', { name: 'time' })).toBeVisible();
		await expect(dialog.getByLabel('Stitch preview')).toBeVisible();
		await dialog.getByRole('button', { name: 'seed' }).click();
		await expect(dialog.getByRole('button', { name: 'seed' })).toHaveAttribute('aria-pressed', 'true');
		await screenshot(page, JOURNEY, 'stitch-grid-1440');

		const download = page.waitForEvent('download');
		await dialog.getByRole('button', { name: /Download PNG/ }).click();
		expect((await download).suggestedFilename()).toMatch(/\.png$/);
	});
});
